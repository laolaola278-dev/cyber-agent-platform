# Phase 28.5 -- CAP acquisition BROWSER sandbox image
#
# Extends the HTTP sandbox with Playwright + Chromium. Still non-root, pinned,
# no socket / host mounts / privileged. Chromium runs INSIDE the container's
# own PID/network namespaces; the container IS the sandbox (--no-sandbox is
# safe here because there is no host exposure).

# The HTTP sandbox image this one layers on, passed in by the caller:
#
#   backend/docker/build_sandbox_images.sh   local + certification builds
#   scripts/release/build_release_image.sh   release build (the sandbox-http
#                                            digest published in the same run)
#
# There is deliberately no default. `FROM cap-sandbox-http:latest` built
# successfully against whatever the machine already had and could not say which
# bytes the published image started from, so an unset base now fails at the FROM
# line -- loudly, at build time, instead of quietly in the artifact.
ARG SANDBOX_HTTP_BASE
FROM ${SANDBOX_HTTP_BASE}

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

# numeric uid -- see sandbox-http Dockerfile: runAsNonRoot cannot verify a
# symbolic user name and rejects container creation (CreateContainerConfigError).
USER 10001
WORKDIR /sandbox
ENTRYPOINT ["python", "-m", "sandbox.shim"]