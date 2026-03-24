# ─── Sibyl.ai Dockerfile ───
# Builds a lightweight Python 3.12 container for the Sibyl agent system.
#
# HOW TO BUILD:
#     docker build -t sibyl-ai .
#
# HOW TO RUN:
#     docker run --env-file .env -v ./data:/app/data sibyl-ai
#
# WHAT THIS DOES:
#     1. Installs system dependencies (gcc for cryptography RSA support)
#     2. Installs Python dependencies from pyproject.toml
#     3. Copies the application code and config files
#     4. Creates data/ and logs/ directories for persistence
#     5. Runs the monitor agents by default
#
# VOLUMES:
#     /app/data  — Mount this to persist the SQLite database between restarts
#
# ENVIRONMENT:
#     Pass your .env file with --env-file to provide API credentials.
#     Required: KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_PATH
#     (Note: key file must be accessible inside the container)

FROM python:3.12-slim AS base

# Install gcc and libffi — required by the `cryptography` library for
# compiling C extensions (used for Kalshi RSA-PSS authentication).
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Install Python dependencies ──────────────────────────────────────
# We copy pyproject.toml first (before the code) so that Docker can
# cache the dependency installation layer.  This means rebuilds are
# fast when only code changes (dependencies don't need to reinstall).
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# ── Copy application code ────────────────────────────────────────────
COPY sibyl/ sibyl/
COPY config/ config/

# ── Create directories for runtime data ──────────────────────────────
RUN mkdir -p data logs

# ── Runtime configuration ────────────────────────────────────────────
# PYTHONUNBUFFERED=1: Ensures print/log output appears immediately
#                     (important for Docker log monitoring)
# PYTHONDONTWRITEBYTECODE=1: Don't create .pyc files (saves space)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Declare /app/data as a volume mount point
VOLUME ["/app/data"]

# Expose dashboard port (default: 8088)
EXPOSE 8088

# Default command: run all agents with dashboard enabled
CMD ["python", "-m", "sibyl", "--agents", "all", "--dashboard", "--dashboard-port", "8088"]
