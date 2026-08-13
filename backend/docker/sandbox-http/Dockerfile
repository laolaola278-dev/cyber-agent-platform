# Phase 28.5 -- CAP acquisition HTTP sandbox image
#
# Minimal, pinned, non-root, reproducible. NO docker socket, NO host mounts,
# NO privileged mode. The image carries only the execution shim + httpx; the
# runtime enforces read-only rootfs + tmpfs + resource limits at run time.

# Pin the base image (reproducible builds; replace with a pinned digest in CI)
FROM python:3.13.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# non-root execution user (fixed uid, no home, no shell)
RUN adduser --system --uid 10001 --disabled-password --no-create-home --shell /usr/sbin/nologin capuser \
    # harden credential files so the non-root user cannot read host-style
    # password databases even inside the container (root:shadow 0640)
    && chown root:shadow /etc/shadow /etc/gshadow 2>/dev/null || true \
    && chmod 640 /etc/shadow /etc/gshadow 2>/dev/null || true

# network probes for certification (raw-socket/curl checks are run via exec;
# the tools themselves do not grant egress -- network policy is applied at
# the container/network layer)
RUN apt-get update && apt-get install -y --no-install-recommends curl iproute2 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# minimal HTTP dependency (pinned to a known-good release)
RUN pip install --no-cache-dir --disable-pip-version-check "httpx==0.27.2" "pydantic==2.9.2"

# the typed protocol + shim (self-contained copy, no worker code)
WORKDIR /sandbox
RUN mkdir -p /sandbox/sandbox
COPY sandbox/__init__.py /sandbox/sandbox/__init__.py
COPY sandbox/oci_protocol.py /sandbox/sandbox/oci_protocol.py
COPY sandbox/shim.py /sandbox/sandbox/shim.py
ENV PYTHONPATH=/sandbox

# no source tree, no host paths, no credentials
USER capuser
WORKDIR /sandbox
ENTRYPOINT ["python", "-m", "sandbox.shim"]
