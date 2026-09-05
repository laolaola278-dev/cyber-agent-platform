# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.8.3 AS uv

FROM python:3.13-slim AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml README.md uv.lock ./
COPY app ./app
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim AS runtime

ARG VERSION=1.0.4
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Cyber Agent Platform Backend" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    APP_VERSION=$VERSION
WORKDIR /app

# Refresh OS packages to close known HIGH CVEs in the base image (e.g.
# util-linux/bsdutils CVE-2026-53615) so the Trivy image scan reports
# 0 blocking HIGH/CRITICAL. Removes the apt lists to keep the image lean.
# Also remove the base image's seed build tooling (setuptools/wheel/pip) from
# the SYSTEM Python: the app runs from /app/.venv, so these are unused at
# runtime, and the bundled setuptools is the only remaining Trivy HIGH
# (CVE-2025-47273, CVE-2024-6345). rm -rf (not pip uninstall, which silently
# no-ops because python:3.13-slim no longer seeds pip) is used to be reliable.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/local/lib/python3.13/site-packages/setuptools* \
              /usr/local/lib/python3.13/site-packages/pkg_resources* \
              /usr/local/lib/python3.13/site-packages/wheel* \
              /usr/local/lib/python3.13/site-packages/pip* \
    && addgroup --system --gid 10001 cap && adduser --system --uid 10001 --ingroup cap cap
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY run.py ./
COPY config ./config
COPY alembic.ini ./
COPY alembic ./alembic

# numeric UID: Kubernetes runAsNonRoot requires a resolvable non-root user
# (a named user like "cap" cannot be verified as non-root)
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["python", "run.py"]
