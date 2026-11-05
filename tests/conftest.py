"""Shared pytest fixtures isolating async vector databases and telemetry per test."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import lease_negotiator.memory.vector_store as vs_mod
from lease_negotiator.memory.vector_store import AsyncDealVectorMemoryStore


@pytest.fixture(autouse=True)
def isolated_vector_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test its own ephemeral SQLite vector DB without touching working tree files."""
    store = AsyncDealVectorMemoryStore(
        sqlite_path=tmp_path / "test_deal_vectors.db",
        chroma_dir=tmp_path / "test_chroma",
        prefer_chromadb=False,
        use_gemini_embeddings=False,
    )
    monkeypatch.setattr(vs_mod, "_DEFAULT_VECTOR_STORE", store)
    yield
    vs_mod._DEFAULT_VECTOR_STORE = None
