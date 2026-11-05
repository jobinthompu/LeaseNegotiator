"""Typed runtime configuration for the Car Lease Negotiator multi-agent platform."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    _PKG_ENV = Path(__file__).resolve().parent / ".env"
    _ROOT_ENV = Path(__file__).resolve().parent.parent / ".env"
    if _PKG_ENV.exists():
        load_dotenv(_PKG_ENV)
    elif _ROOT_ENV.exists():
        load_dotenv(_ROOT_ENV)
except ImportError:
    pass


DEFAULT_TARGET_MODELS: tuple[str, ...] = (
    "Hyundai IONIQ 9",
    "Kia EV9",
    "Volvo EX90",
    "Volvo XC90 Plug-In Hybrid",
)


def _parse_models(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_TARGET_MODELS)
    parsed = [item.strip() for item in raw.split(",") if item.strip()]
    return parsed or list(DEFAULT_TARGET_MODELS)


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ShopperConfig:
    """Immutable settings controlling models, memory, guardrails, observability, and lease defaults."""

    # Multi-model tiering & resiliency
    pro_model: str = "gemini-3.1-pro-preview"
    flash_model: str = "gemini-3.5-flash"
    lite_model: str = "gemini-3.5-flash-lite"
    embedding_model: str = "gemini-embedding-001"
    enable_dynamic_routing: bool = True
    llm_max_retry_attempts: int = 4

    # Context compaction & persistent session/vector memory
    session_db_url: str = "sqlite+aiosqlite:///./.lease_data/sessions.db"
    vector_store_path: str = "./.lease_data/deal_vectors.db"
    chroma_persist_dir: str = "./.lease_data/chroma"
    compaction_token_threshold: int = 24000
    compaction_event_retention: int = 8
    compaction_interval_turns: int = 6
    compaction_overlap_turns: int = 2
    context_max_invocations: int = 10

    # Guardrails & HITL policies
    max_tool_calls_per_turn: int = 12
    pii_guardrail_mode: str = "block_critical_redact_contact"
    hitl_max_effective_monthly_usd: float = 900.0
    hitl_require_outreach_approval: bool = True

    # Observability & OpenTelemetry
    log_level: str = "INFO"
    log_format: str = "json"
    otel_service_name: str = "lease-negotiator"
    deployment_env: str = "development"
    otel_exporter_otlp_endpoint: str = ""
    otel_console_export: bool = False

    # Regional shopper lease defaults (Livermore 94550 / 100-mi radius)
    zip_code: str = "94550"
    city_state: str = "Livermore, CA"
    search_radius_miles: int = 100
    sales_tax_percent: float = 10.25
    state_code: str = "CA"
    doc_fee_cap_usd: float = 85.0
    target_models: list[str] = field(default_factory=lambda: list(DEFAULT_TARGET_MODELS))

    @property
    def model_name(self) -> str:
        """Primary default workhorse model alias."""
        return self.flash_model

    @classmethod
    def from_env(cls) -> ShopperConfig:
        """Load configuration from environment variables with safe defaults."""
        legacy_override = os.getenv("LEASE_NEGOTIATOR_MODEL", "").strip()
        flash_default = legacy_override or "gemini-3.5-flash"
        return cls(
            pro_model=_env_str("LEASE_PRO_MODEL", legacy_override or "gemini-3.1-pro-preview"),
            flash_model=_env_str("LEASE_FLASH_MODEL", flash_default),
            lite_model=_env_str("LEASE_LITE_MODEL", "gemini-3.5-flash-lite"),
            embedding_model=_env_str("LEASE_EMBEDDING_MODEL", "gemini-embedding-001"),
            enable_dynamic_routing=_env_bool("ENABLE_DYNAMIC_MODEL_ROUTING", True),
            llm_max_retry_attempts=max(1, _env_int("LLM_MAX_RETRY_ATTEMPTS", 4)),
            session_db_url=_env_str(
                "SESSION_DB_URL", "sqlite+aiosqlite:///./.lease_data/sessions.db"
            ),
            vector_store_path=_env_str("VECTOR_STORE_PATH", "./.lease_data/deal_vectors.db"),
            chroma_persist_dir=_env_str("CHROMA_PERSIST_DIR", "./.lease_data/chroma"),
            compaction_token_threshold=max(2000, _env_int("COMPACTION_TOKEN_THRESHOLD", 24000)),
            compaction_event_retention=max(2, _env_int("COMPACTION_EVENT_RETENTION", 8)),
            compaction_interval_turns=max(2, _env_int("COMPACTION_INTERVAL_TURNS", 6)),
            compaction_overlap_turns=max(0, _env_int("COMPACTION_OVERLAP_TURNS", 2)),
            context_max_invocations=max(3, _env_int("CONTEXT_MAX_INVOCATIONS", 10)),
            max_tool_calls_per_turn=max(2, _env_int("MAX_TOOL_CALLS_PER_TURN", 12)),
            pii_guardrail_mode=_env_str(
                "PII_GUARDRAIL_MODE", "block_critical_redact_contact"
            ),
            hitl_max_effective_monthly_usd=max(
                100.0, _env_float("HITL_MAX_EFFECTIVE_MONTHLY_USD", 900.0)
            ),
            hitl_require_outreach_approval=_env_bool("HITL_REQUIRE_OUTREACH_APPROVAL", True),
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
            log_format=_env_str("LOG_FORMAT", "json").lower(),
            otel_service_name=_env_str("OTEL_SERVICE_NAME", "lease-negotiator"),
            deployment_env=_env_str("DEPLOYMENT_ENV", "development"),
            otel_exporter_otlp_endpoint=_env_str("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
            otel_console_export=_env_bool("OTEL_CONSOLE_EXPORT", False),
            zip_code=_env_str("SHOPPER_ZIP_CODE", "94550"),
            city_state=_env_str("SHOPPER_CITY_STATE", "Livermore, CA"),
            search_radius_miles=max(1, _env_int("DEALER_SEARCH_RADIUS_MILES", 100)),
            sales_tax_percent=max(0.0, _env_float("LOCAL_SALES_TAX_PERCENT", 10.25)),
            state_code=_env_str("STATE_CODE", "CA").upper(),
            doc_fee_cap_usd=max(0.0, _env_float("CA_DOC_FEE_CAP_USD", 85.0)),
            target_models=_parse_models(os.getenv("TARGET_MODELS")),
        )


SETTINGS = ShopperConfig.from_env()
