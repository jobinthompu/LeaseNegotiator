"""Asynchronous persistent vector memory store for dealer quotes and negotiation history.

Supports two persistent backends seamlessly:
1. ChromaDB (`chromadb.PersistentClient`, HNSW cosine index) offloaded via `asyncio.to_thread`.
2. Async SQLite (`aiosqlite` with non-blocking fallback) storing L2-normalized embedding vectors
   with exact cosine-similarity search.

Embeddings support live async Gemini `embed_content` calls with automatic fallback to a
deterministic L2-normalized character/word n-gram feature hashing embedder for offline/CI parity.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal
import uuid

from ..config import SETTINGS, ShopperConfig
from ..observability.logging_config import get_logger
from ..observability.pii_redactor import DEFAULT_PII_REDACTOR

try:
    import aiosqlite

    HAS_AIOSQLITE = True
except ImportError:  # pragma: no cover
    aiosqlite = None  # type: ignore[assignment]
    HAS_AIOSQLITE = False

try:
    import chromadb

    HAS_CHROMADB = True
except ImportError:
    chromadb = None  # type: ignore[assignment]
    HAS_CHROMADB = False

logger = get_logger("memory.vector_store")

_TOKEN_RE = re.compile(r"[a-z0-9$%.]+")
_DEFAULT_DIM = 256


def compute_local_embedding(text: str, dim: int = _DEFAULT_DIM) -> list[float]:
    """Generate a deterministic L2-normalized signed feature-hashed embedding vector."""
    vec = [0.0] * dim
    lowered = text.lower()
    tokens = _TOKEN_RE.findall(lowered)
    features: list[tuple[str, float]] = [(tok, 1.0) for tok in tokens]
    # Add bigrams + 4-char shingles for resilient fuzzy/model-alias matching (e.g. ev9 / ev 9).
    for idx in range(len(tokens) - 1):
        features.append((f"{tokens[idx]}_{tokens[idx + 1]}", 1.25))
    compact = re.sub(r"\s+", "", lowered)
    for idx in range(max(0, len(compact) - 3)):
        features.append((f"#{compact[idx : idx + 4]}", 0.35))

    if not features:
        vec[0] = 1.0
        return vec

    for feat, weight in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        h_val = int.from_bytes(digest, "little")
        bucket = h_val % dim
        sign = 1.0 if ((h_val >> 32) & 1) == 0 else -1.0
        vec[bucket] += sign * weight

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two equal-dimension vectors in [-1.0, 1.0]."""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


class AsyncDealVectorMemoryStore:
    """Thread- and coroutine-safe persistent vector store for cross-session deal recall."""

    def __init__(
        self,
        *,
        sqlite_path: str | Path | None = None,
        chroma_dir: str | Path | None = None,
        prefer_chromadb: bool = True,
        use_gemini_embeddings: bool = False,
        embedding_model: str = SETTINGS.embedding_model,
    ) -> None:
        self._sqlite_path = Path(sqlite_path or SETTINGS.vector_store_path)
        self._chroma_dir = Path(chroma_dir or SETTINGS.chroma_persist_dir)
        self._use_chroma = bool(prefer_chromadb and HAS_CHROMADB)
        self._use_gemini_embeddings = use_gemini_embeddings
        self._embedding_model = embedding_model
        self._lock = asyncio.Lock()
        self._initialized = False
        self._chroma_collection: Any = None

    @property
    def backend_name(self) -> Literal["chromadb", "sqlite_cosine"]:
        return "chromadb" if self._use_chroma else "sqlite_cosine"

    async def initialize(self) -> None:
        """Create underlying persistent storage directories, tables, or Chroma collections."""
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            if self._use_chroma:
                await asyncio.to_thread(self._init_chroma_sync)
            else:
                self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
                ddl = """
                CREATE TABLE IF NOT EXISTS deal_offer_vectors (
                    record_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    dealership_name TEXT NOT NULL,
                    vehicle_model TEXT NOT NULL,
                    msrp REAL NOT NULL,
                    selling_price REAL NOT NULL,
                    monthly_with_tax REAL NOT NULL,
                    due_at_signing REAL NOT NULL,
                    term_months INTEGER NOT NULL,
                    effective_monthly_with_tax REAL NOT NULL,
                    notes TEXT NOT NULL,
                    created_at_iso TEXT NOT NULL,
                    document_text TEXT NOT NULL,
                    embedding_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_deal_vectors_session
                    ON deal_offer_vectors(session_id, created_at_iso DESC);
                """
                if HAS_AIOSQLITE:
                    async with aiosqlite.connect(self._sqlite_path) as db:
                        await db.executescript(ddl)
                        await db.commit()
                else:
                    await asyncio.to_thread(self._exec_sqlite_ddl_sync, ddl)
            self._initialized = True
            logger.info(
                "Initialized async persistent deal vector store",
                extra={"backend": self.backend_name},
            )

    def _init_chroma_sync(self) -> None:
        self._chroma_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(self._chroma_dir))
        self._chroma_collection = client.get_or_create_collection(
            name="lease_deal_offers",
            metadata={"hnsw:space": "cosine"},
        )

    def _exec_sqlite_ddl_sync(self, ddl: str) -> None:
        with sqlite3.connect(self._sqlite_path) as conn:
            conn.executescript(ddl)
            conn.commit()

    async def _embed_async(self, text: str) -> list[float]:
        """Embed document or query asynchronously via Gemini embeddings or deterministic fallback."""
        if self._use_gemini_embeddings and os.getenv("GOOGLE_API_KEY"):
            try:
                from google import genai

                client = genai.Client()
                resp = await client.aio.models.embed_content(
                    model=self._embedding_model,
                    contents=text,
                )
                if resp.embeddings and resp.embeddings[0].values:
                    vals = [float(x) for x in resp.embeddings[0].values]
                    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
                    return [v / norm for v in vals]
            except Exception as exc:
                logger.warning(
                    "Falling back to deterministic local embeddings: %s",
                    exc,
                )
        return await asyncio.to_thread(compute_local_embedding, text)

    @staticmethod
    def _compose_document(record: dict[str, Any]) -> str:
        return (
            f"Dealership: {record['dealership_name']} | "
            f"Model: {record['vehicle_model']} | "
            f"MSRP: ${record['msrp']:,.0f} | Selling Price: ${record['selling_price']:,.0f} | "
            f"Monthly w/ tax: ${record['monthly_with_tax']:,.2f} | "
            f"DAS: ${record['due_at_signing']:,.2f} | Term: {record['term_months']}m | "
            f"Effective Monthly: ${record['effective_monthly_with_tax']:,.2f}/mo | "
            f"Notes: {record['notes']}"
        )

    async def upsert_offer(
        self,
        *,
        session_id: str,
        dealership_name: str,
        vehicle_model: str,
        msrp: float,
        selling_price: float,
        monthly_with_tax: float,
        due_at_signing: float,
        term_months: int,
        notes: str = "",
        record_id: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Persist an offer + vector embedding asynchronously and return (record_dict, session_count)."""
        await self.initialize()
        scrubbed_notes = DEFAULT_PII_REDACTOR.redact_text(notes.strip())[:2000]
        scrubbed_dealer = DEFAULT_PII_REDACTOR.redact_text(dealership_name.strip())
        scrubbed_model = DEFAULT_PII_REDACTOR.redact_text(vehicle_model.strip())

        total = (
            due_at_signing + monthly_with_tax * max(0, term_months - 1)
            if monthly_with_tax > 0
            else due_at_signing
        )
        effective_monthly = round(total / max(1, term_months), 2)
        rid = record_id or f"offer_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(tz=UTC).isoformat()

        rec: dict[str, Any] = {
            "record_id": rid,
            "session_id": session_id or "default",
            "dealership_name": scrubbed_dealer,
            "vehicle_model": scrubbed_model,
            "msrp": round(float(msrp), 2),
            "selling_price": round(float(selling_price), 2),
            "monthly_with_tax": round(float(monthly_with_tax), 2),
            "due_at_signing": round(float(due_at_signing), 2),
            "term_months": int(term_months),
            "effective_monthly_with_tax": effective_monthly,
            "notes": scrubbed_notes,
            "created_at_iso": now_iso,
        }
        doc_text = self._compose_document(rec)
        embedding = await self._embed_async(doc_text)

        async with self._lock:
            if self._use_chroma:
                count = await asyncio.to_thread(
                    self._upsert_chroma_sync, rec, doc_text, embedding
                )
            elif HAS_AIOSQLITE:
                count = await self._upsert_aiosqlite(rec, doc_text, embedding)
            else:
                count = await asyncio.to_thread(
                    self._upsert_sqlite_sync, rec, doc_text, embedding
                )

        logger.info(
            "Saved dealer quote to vector memory",
            extra={
                "record_id": rid,
                "dealership": scrubbed_dealer,
                "vehicle_model": scrubbed_model,
                "effective_monthly": effective_monthly,
                "backend": self.backend_name,
            },
        )
        return rec, count

    def _upsert_chroma_sync(
        self, rec: dict[str, Any], doc_text: str, embedding: list[float]
    ) -> int:
        self._chroma_collection.upsert(
            ids=[rec["record_id"]],
            documents=[doc_text],
            embeddings=[embedding],
            metadatas=[rec],
        )
        existing = self._chroma_collection.get(where={"session_id": rec["session_id"]})
        return len(existing.get("ids", []))

    async def _upsert_aiosqlite(
        self, rec: dict[str, Any], doc_text: str, embedding: list[float]
    ) -> int:
        sql = """
        INSERT OR REPLACE INTO deal_offer_vectors (
            record_id, session_id, dealership_name, vehicle_model, msrp, selling_price,
            monthly_with_tax, due_at_signing, term_months, effective_monthly_with_tax,
            notes, created_at_iso, document_text, embedding_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            rec["record_id"],
            rec["session_id"],
            rec["dealership_name"],
            rec["vehicle_model"],
            rec["msrp"],
            rec["selling_price"],
            rec["monthly_with_tax"],
            rec["due_at_signing"],
            rec["term_months"],
            rec["effective_monthly_with_tax"],
            rec["notes"],
            rec["created_at_iso"],
            doc_text,
            json.dumps(embedding),
        )
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(sql, params)
            await db.commit()
            async with db.execute(
                "SELECT COUNT(*) FROM deal_offer_vectors WHERE session_id = ?",
                (rec["session_id"],),
            ) as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else 1

    def _upsert_sqlite_sync(
        self, rec: dict[str, Any], doc_text: str, embedding: list[float]
    ) -> int:
        sql = """
        INSERT OR REPLACE INTO deal_offer_vectors (
            record_id, session_id, dealership_name, vehicle_model, msrp, selling_price,
            monthly_with_tax, due_at_signing, term_months, effective_monthly_with_tax,
            notes, created_at_iso, document_text, embedding_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            rec["record_id"],
            rec["session_id"],
            rec["dealership_name"],
            rec["vehicle_model"],
            rec["msrp"],
            rec["selling_price"],
            rec["monthly_with_tax"],
            rec["due_at_signing"],
            rec["term_months"],
            rec["effective_monthly_with_tax"],
            rec["notes"],
            rec["created_at_iso"],
            doc_text,
            json.dumps(embedding),
        )
        with sqlite3.connect(self._sqlite_path) as conn:
            conn.execute(sql, params)
            conn.commit()
            cur = conn.execute(
                "SELECT COUNT(*) FROM deal_offer_vectors WHERE session_id = ?",
                (rec["session_id"],),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 1

    async def semantic_search(
        self,
        query: str,
        *,
        top_k: int = 5,
        session_id: str | None = None,
    ) -> list[tuple[dict[str, Any], float]]:
        """Run non-blocking cosine similarity vector search across persisted dealer offers."""
        await self.initialize()
        limit = max(1, min(int(top_k), 25))
        q_vec = await self._embed_async(query)

        async with self._lock:
            if self._use_chroma:
                return await asyncio.to_thread(
                    self._search_chroma_sync, q_vec, limit, session_id
                )
            if HAS_AIOSQLITE:
                rows = await self._fetch_rows_aiosqlite(session_id)
            else:
                rows = await asyncio.to_thread(self._fetch_rows_sqlite_sync, session_id)

        scored: list[tuple[dict[str, Any], float]] = []
        for rec, emb in rows:
            sim = cosine_similarity(q_vec, emb)
            scored.append((rec, round(sim, 4)))
        scored.sort(key=lambda item: (item[1], -item[0]["effective_monthly_with_tax"]), reverse=True)
        return scored[:limit]

    def _search_chroma_sync(
        self, q_vec: list[float], limit: int, session_id: str | None
    ) -> list[tuple[dict[str, Any], float]]:
        total_count = self._chroma_collection.count()
        if total_count == 0:
            return []
        kwargs: dict[str, Any] = {
            "query_embeddings": [q_vec],
            "n_results": min(limit, total_count),
            "include": ["metadatas", "distances"],
        }
        if session_id:
            kwargs["where"] = {"session_id": session_id}
        res = self._chroma_collection.query(**kwargs)
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        hits: list[tuple[dict[str, Any], float]] = []
        for meta, dist in zip(metas, dists, strict=False):
            similarity = max(-1.0, min(1.0, 1.0 - float(dist)))
            hits.append((dict(meta), round(similarity, 4)))
        return hits

    async def _fetch_rows_aiosqlite(
        self, session_id: str | None
    ) -> list[tuple[dict[str, Any], list[float]]]:
        sql = (
            "SELECT record_id, session_id, dealership_name, vehicle_model, msrp, selling_price, "
            "monthly_with_tax, due_at_signing, term_months, effective_monthly_with_tax, "
            "notes, created_at_iso, embedding_json FROM deal_offer_vectors"
        )
        params: tuple[Any, ...] = ()
        if session_id:
            sql += " WHERE session_id = ?"
            params = (session_id,)
        sql += " ORDER BY created_at_iso DESC"

        out: list[tuple[dict[str, Any], list[float]]] = []
        async with aiosqlite.connect(self._sqlite_path) as db:
            async with db.execute(sql, params) as cur:
                async for row in cur:
                    out.append(self._row_to_record(row))
        return out

    def _fetch_rows_sqlite_sync(
        self, session_id: str | None
    ) -> list[tuple[dict[str, Any], list[float]]]:
        sql = (
            "SELECT record_id, session_id, dealership_name, vehicle_model, msrp, selling_price, "
            "monthly_with_tax, due_at_signing, term_months, effective_monthly_with_tax, "
            "notes, created_at_iso, embedding_json FROM deal_offer_vectors"
        )
        params: tuple[Any, ...] = ()
        if session_id:
            sql += " WHERE session_id = ?"
            params = (session_id,)
        sql += " ORDER BY created_at_iso DESC"

        with sqlite3.connect(self._sqlite_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row: Any) -> tuple[dict[str, Any], list[float]]:
        rec = {
            "record_id": str(row[0]),
            "session_id": str(row[1]),
            "dealership_name": str(row[2]),
            "vehicle_model": str(row[3]),
            "msrp": float(row[4]),
            "selling_price": float(row[5]),
            "monthly_with_tax": float(row[6]),
            "due_at_signing": float(row[7]),
            "term_months": int(row[8]),
            "effective_monthly_with_tax": float(row[9]),
            "notes": str(row[10]),
            "created_at_iso": str(row[11]),
        }
        emb = [float(v) for v in json.loads(row[12])]
        return rec, emb

    async def list_offers(
        self, *, session_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """List persisted dealer quotes sorted by best effective monthly cost first."""
        await self.initialize()
        capped = max(1, min(limit, 100))
        async with self._lock:
            if self._use_chroma:
                records = await asyncio.to_thread(self._list_chroma_sync, session_id)
            elif HAS_AIOSQLITE:
                rows = await self._fetch_rows_aiosqlite(session_id)
                records = [r for r, _ in rows]
            else:
                rows = await asyncio.to_thread(self._fetch_rows_sqlite_sync, session_id)
                records = [r for r, _ in rows]
        records.sort(key=lambda r: r["effective_monthly_with_tax"])
        return records[:capped]

    def _list_chroma_sync(self, session_id: str | None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"include": ["metadatas"]}
        if session_id:
            kwargs["where"] = {"session_id": session_id}
        res = self._chroma_collection.get(**kwargs)
        return [dict(m) for m in (res.get("metadatas") or []) if m]


_DEFAULT_VECTOR_STORE: AsyncDealVectorMemoryStore | None = None


def get_default_vector_store(cfg: ShopperConfig = SETTINGS) -> AsyncDealVectorMemoryStore:
    """Return process-wide singleton async vector store configured from `ShopperConfig`."""
    global _DEFAULT_VECTOR_STORE
    if _DEFAULT_VECTOR_STORE is None:
        _DEFAULT_VECTOR_STORE = AsyncDealVectorMemoryStore(
            sqlite_path=cfg.vector_store_path,
            chroma_dir=cfg.chroma_persist_dir,
            prefer_chromadb=True,
            use_gemini_embeddings=False,
            embedding_model=cfg.embedding_model,
        )
    return _DEFAULT_VECTOR_STORE
