#!/usr/bin/env bash
# Phase 28.5 -- build the CAP sandbox images (Linux/CI).
#
# Stages the self-contained protocol + shim contexts through
# prepare_sandbox_context.sh (the same staging the release pipeline uses, so a
# release image cannot be built from a different file list than the certified
# one), then builds the HTTP and (optionally) the browser image.
#
# Usage: bash docker/build_sandbox_images.sh [--with-browser]
#
# Environment:
#   CAP_SANDBOX_IMAGE_TAG    tag to build, default "latest" (the local and
#                            certification convention: the kind cluster and
#                            compose run these images without a registry).
#   CAP_SANDBOX_REPO_PREFIX  registry/repository prefix for the images and for
#                            the browser's base, default "" (build names stay
#                            cap-sandbox-http:<tag> etc).
set -euo pipefail

cd "$(dirname "$0")/.."

TAG="${CAP_SANDBOX_IMAGE_TAG:-latest}"
PREFIX="${CAP_SANDBOX_REPO_PREFIX:-}"

HTTP_CTX=$(mktemp -d)
BROWSER_CTX=$(mktemp -d)
EGRESS_CTX=$(mktemp -d)
trap 'rm -rf "$HTTP_CTX" "$BROWSER_CTX" "$EGRESS_CTX"' EXIT

bash docker/prepare_sandbox_context.sh "$HTTP_CTX" http
bash docker/prepare_sandbox_context.sh "$BROWSER_CTX" browser
bash docker/prepare_sandbox_context.sh "$EGRESS_CTX" egress-proxy

echo "==> building ${PREFIX}cap-sandbox-http:${TAG}"
docker build -t "${PREFIX}cap-sandbox-http:${TAG}" -f docker/sandbox-http/Dockerfile "$HTTP_CTX"

if [[ "${1:-}" == "--with-browser" ]]; then
  # The browser image layers on the HTTP image built just above, named here
  # rather than left to a :latest default in the Dockerfile: the ARG has no
  # default precisely so an unqualified build cannot pass silently.
  echo "==> building ${PREFIX}cap-sandbox-browser:${TAG}"
  docker build -t "${PREFIX}cap-sandbox-browser:${TAG}" \
    --build-arg "SANDBOX_HTTP_BASE=${PREFIX}cap-sandbox-http:${TAG}" \
    -f docker/sandbox-browser/Dockerfile "$BROWSER_CTX"
fi

echo "==> building ${PREFIX}cap-egress-proxy:${TAG}"
docker build -t "${PREFIX}cap-egress-proxy:${TAG}" \
  -f docker/egress-proxy/Dockerfile "$EGRESS_CTX"

echo "sandbox images built (tag ${TAG})"
