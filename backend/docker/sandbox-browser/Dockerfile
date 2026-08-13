# Phase 28.5 -- CAP acquisition BROWSER sandbox image
#
# Extends the HTTP sandbox with Playwright + Chromium. Still non-root, pinned,
# no socket / host mounts / privileged. Chromium runs INSIDE the container's
# own PID/network namespaces; the container IS the sandbox (--no-sandbox is
# safe here because there is no host exposure).

FROM cap-sandbox-http:latest

# Switch back to root for installation (the http sandbox ends with USER capuser).
# We need root to install system packages and pip packages globally.
USER root

# Playwright + pinned Chromium build. Browsers live under /opt (read-only at
# runtime, executable is fine); the Chromium PROFILE goes to /tmp (tmpfs).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir --disable-pip-version-check "playwright==1.49.1" \
    && python -m playwright install --with-deps chromium \
    && rm -rf /root/.cache

# runtime: ephemeral home/profile on the tmpfs mount
ENV HOME=/tmp

USER capuser
WORKDIR /sandbox
ENTRYPOINT ["python", "-m", "sandbox.shim"]