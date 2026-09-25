# ==============================================================================
# NovaConnect Telecoms Conversational IVR — engine container
# ------------------------------------------------------------------------------
# Multi-stage: dependencies are resolved in a builder and the runtime image
# carries only the installed packages and the app, so build toolchains never
# ship to production.
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1 — build dependencies into a virtualenv
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ------------------------------------------------------------------------------
# Stage 2 — runtime
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# ffmpeg and libsndfile1 handle the audio containers callers upload; curl backs
# the HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Create the unprivileged user before copying, and copy with ownership set, so
# there is no duplicated image layer from a later recursive chown.
RUN useradd --create-home --uid 1000 appuser
COPY --chown=appuser:appuser app ./app
USER appuser

ENV HOST=0.0.0.0 \
    PORT=8000 \
    WEB_CONCURRENCY=2

EXPOSE 8000

# Readiness, not liveness: /readyz verifies the state backend responds and the
# persona templates render. A container whose Redis is unreachable should not be
# handed traffic just because its process is running.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/readyz || exit 1

# The pipeline is I/O-bound (three Sarvam round-trips per turn), so a small
# worker count with async handlers serves concurrent calls comfortably.
# WEB_CONCURRENCY > 1 requires REDIS_URL — app.main refuses to boot otherwise,
# because unshared conversation state loses a caller's history mid-call.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY}"]
