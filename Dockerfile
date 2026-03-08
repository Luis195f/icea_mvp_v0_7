# Hardened, reproducible Dockerfile for ICEA+ (v0.7.4 remediation)
# - Aligns runtime with Python 3.12
# - Runs as non-root (uid/gid 10001)
# - Uses a dedicated venv at /opt/venv (optionally writable via named volume in prod)
# - Pre-creates /app/backend/models with correct ownership to avoid PermissionError on first volume mount

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Create a non-root user with a stable UID/GID (fits OpenShift/K8s patterns)
ARG APP_UID=10001
ARG APP_GID=10001

RUN set -eux; \
    groupadd -g "${APP_GID}" icea; \
    useradd -m -u "${APP_UID}" -g "${APP_GID}" -s /usr/sbin/nologin icea

WORKDIR /app

# Minimal system deps
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential ca-certificates; \
    rm -rf /var/lib/apt/lists/*

# Create isolated venv (owned later by non-root) to support optional runtime installs
RUN set -eux; \
    python -m venv "$VIRTUAL_ENV"; \
    "$VIRTUAL_ENV/bin/pip" install --upgrade pip setuptools wheel

COPY requirements.txt /app/requirements.txt
RUN "$VIRTUAL_ENV/bin/pip" install -r /app/requirements.txt

# Copy code
COPY backend /app/backend
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
# Ensure optional deps file is available from /app/backend (entrypoint runs in /app/backend)
COPY requirements-optional.txt /app/backend/requirements-optional.txt

# Pre-create model directory (so Docker volume copy-up preserves non-root ownership)
# + supply-chain hygiene: purge any accidental bytecode from build context
RUN set -eux; \
    mkdir -p /app/backend/models; \
    find /app -type d -name '__pycache__' -prune -exec rm -rf {} +; \
    find /app -type f -name '*.pyc' -delete; \
    chmod 0755 /app/docker-entrypoint.sh; \
    chown -R icea:icea /app "$VIRTUAL_ENV"; \
    chown -R icea:icea /app/backend/models

USER icea

WORKDIR /app/backend
ENV DJANGO_SETTINGS_MODULE=config.settings

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
