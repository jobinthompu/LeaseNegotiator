# syntax=docker/dockerfile:1.7
# ==============================================================================
# Stage 1: Wheel Builder (isolated build toolchain, never shipped to runtime)
# ==============================================================================
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY lease_negotiator ./lease_negotiator

RUN pip wheel --wheel-dir /wheels ".[db]"

# ==============================================================================
# Stage 2: Hardened Non-Root Production Runtime (Cloud Run / Kubernetes ready)
# ==============================================================================
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="lease-negotiator" \
      org.opencontainers.image.description="Multi-agent Google ADK + Gemini car lease negotiation service" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    LOG_FORMAT=json \
    LOG_LEVEL=INFO \
    DEPLOYMENT_ENV=production \
    SESSION_DB_URL=sqlite+aiosqlite:////app/.lease_data/sessions.db \
    VECTOR_STORE_PATH=/app/.lease_data/deal_vectors.db \
    CHROMA_PERSIST_DIR=/app/.lease_data/chroma

# Create dedicated unprivileged system user & group + writable state directory
RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/.lease_data \
    && chown -R appuser:appgroup /app

WORKDIR /app

COPY --from=builder /wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

COPY --chown=appuser:appgroup lease_negotiator ./lease_negotiator

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()" || exit 1

CMD ["lease-negotiator-server"]
