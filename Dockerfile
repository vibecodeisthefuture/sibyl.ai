# ─── Sibyl.ai Dockerfile ───
# Multi-stage build for the autonomous prediction market system.

FROM python:3.12-slim AS base

# System dependencies for cryptography (Kalshi RSA auth)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Install dependencies ──────────────────────────────────────────────
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# ── Copy application ─────────────────────────────────────────────────
COPY sibyl/ sibyl/
COPY config/ config/

# ── Create data directory ────────────────────────────────────────────
RUN mkdir -p data logs

# ── Runtime ──────────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

VOLUME ["/app/data"]

CMD ["python", "-m", "sibyl", "--agents", "monitor"]
