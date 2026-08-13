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
RUN adduser --system --uid 10001 --disabled-password --no-create-home --shell /usr/sbin/nologin capuser

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
