"""LeaseNegotiator: Production Google ADK + Gemini multi-agent car lease negotiation package."""

from __future__ import annotations

__version__ = "0.2.0"

try:
    from . import agent
except ModuleNotFoundError as _exc:
    # Allow importing standalone stdlib modules (config, PII redactor, vector store, catalogs)
    # in minimal environments where optional runtime packages (`google-adk`, `pydantic`) are absent.
    if not (_exc.name or "").startswith(("google", "pydantic")):
        raise
    agent = None  # type: ignore[assignment]

__all__ = ["__version__", "agent"]
