"""Persistent session state, async vector memory, and rolling context compaction."""

from .compaction import (
    HistoryCompactionManager,
    build_adk_context_filter_plugin,
    build_adk_events_compaction_config,
    estimate_content_tokens,
    extract_deal_anchors,
)
from .session_factory import create_persistent_runner, create_session_service
from .vector_store import (
    AsyncDealVectorMemoryStore,
    compute_local_embedding,
    cosine_similarity,
    get_default_vector_store,
)

__all__ = [
    "AsyncDealVectorMemoryStore",
    "HistoryCompactionManager",
    "build_adk_context_filter_plugin",
    "build_adk_events_compaction_config",
    "compute_local_embedding",
    "cosine_similarity",
    "create_persistent_runner",
    "create_session_service",
    "estimate_content_tokens",
    "extract_deal_anchors",
    "get_default_vector_store",
]
