#!/usr/bin/env bash
# Phase 28.5 -- build the CAP sandbox images (Linux/CI).
#
# Copies the self-contained protocol + shim into each image build context,
# then builds the HTTP and (optionally) the browser image.
#
# Usage: bash docker/build_sandbox_images.sh [--with-browser]
set -euo pipefail

cd "$(dirname "$0")/.."

HTTP_CTX=$(mktemp -d)
BROWSER_CTX=$(mktemp -d)
trap 'rm -rf "$HTTP_CTX" "$BROWSER_CTX"' EXIT

for ctx in "$HTTP_CTX" "$BROWSER_CTX"; do
  mkdir -p "$ctx/sandbox"
  touch "$ctx/sandbox/__init__.py"
  cp app/sandbox/oci_protocol.py "$ctx/sandbox/oci_protocol.py"
  cp app/sandbox/oci_shim.py "$ctx/sandbox/shim.py"
done

echo "==> building cap-sandbox-http:latest"
docker build -t cap-sandbox-http:latest -f docker/sandbox-http/Dockerfile "$HTTP_CTX"

if [[ "${1:-}" == "--with-browser" ]]; then
  echo "==> building cap-sandbox-browser:latest"
  docker build -t cap-sandbox-browser:latest -f docker/sandbox-browser/Dockerfile "$BROWSER_CTX"
fi

echo "==> building cap-egress-proxy:latest"
EGRESS_CTX=$(mktemp -d)
mkdir -p "$EGRESS_CTX/app/sandbox"
cp app/sandbox/egress_proxy.py "$EGRESS_CTX/app/sandbox/egress_proxy.py"
docker build -t cap-egress-proxy:latest -f docker/egress-proxy/Dockerfile "$EGRESS_CTX"
rm -rf "$EGRESS_CTX"

echo "sandbox images built"
