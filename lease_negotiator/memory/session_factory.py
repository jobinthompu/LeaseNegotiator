"""Persistent ADK session & memory runner factory (`DatabaseSessionService` + vector recall)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import SETTINGS, ShopperConfig
from ..observability.logging_config import get_logger
from .vector_store import get_default_vector_store

logger = get_logger("memory.session_factory")


def _ensure_sqlite_parent_dir(db_url: str) -> None:
    """Create parent directory on disk when `db_url` uses a file-backed SQLite DSN."""
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if db_url.startswith(prefix):
            raw_path = db_url[len(prefix) :]
            if raw_path and raw_path != ":memory:":
                Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            return


def create_session_service(cfg: ShopperConfig = SETTINGS) -> Any:
    """Construct ADK `DatabaseSessionService` (SQLite/Postgres) with `InMemorySessionService` fallback."""
    _ensure_sqlite_parent_dir(cfg.session_db_url)
    try:
        from google.adk.sessions import DatabaseSessionService

        service = DatabaseSessionService(db_url=cfg.session_db_url)
        logger.info(
            "Initialized persistent ADK DatabaseSessionService",
            extra={"session_db_backend": cfg.session_db_url.split(":", 1)[0]},
        )
        return service
    except Exception as exc:
        from google.adk.sessions import InMemorySessionService

        logger.warning(
            "Falling back to InMemorySessionService (install `lease-negotiator[db]` for SQL persistence): %s",
            exc,
        )
        return InMemorySessionService()


async def create_persistent_runner(
    app: Any,
    cfg: ShopperConfig = SETTINGS,
) -> Any:
    """Build an ADK `Runner` wired with persistent session state, ADK memory, and initialized vector DB."""
    from google.adk import Runner
    from google.adk.memory.in_memory_memory_service import InMemoryMemoryService

    vector_store = get_default_vector_store(cfg)
    await vector_store.initialize()

    session_service = create_session_service(cfg)
    memory_service = InMemoryMemoryService()

    return Runner(
        app=app,
        session_service=session_service,
        memory_service=memory_service,
    )
