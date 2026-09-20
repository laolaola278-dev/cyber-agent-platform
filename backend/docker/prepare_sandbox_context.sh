#!/usr/bin/env bash
# Stage the self-contained build context for one CAP sandbox image.
#
# The sandbox images deliberately carry the protocol/shim files, not the worker
# tree, so their build context has to be assembled. That used to be inline in
# build_sandbox_images.sh, which left the release pipeline with no way to build
# the same bytes without copying the staging logic -- and a copied staging block
# is how a release image ends up built from a different file list than the
# certified one. Both callers source this file, so there is exactly one answer.
#
# Usage: prepare_sandbox_context.sh <context-dir> <http|browser|egress-proxy>
set -euo pipefail

CTX_DIR="${1:?usage: prepare_sandbox_context.sh <context-dir> <http|browser|egress-proxy>}"
ROLE="${2:?usage: prepare_sandbox_context.sh <context-dir> <http|browser|egress-proxy>}"

# Repo root of this script: backend/ (the files below are named from there).
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "$ROLE" in
  http|browser)
    mkdir -p "$CTX_DIR/sandbox"
    : > "$CTX_DIR/sandbox/__init__.py"
    cp "$BACKEND_DIR/app/sandbox/oci_protocol.py" "$CTX_DIR/sandbox/oci_protocol.py"
    cp "$BACKEND_DIR/app/sandbox/oci_shim.py" "$CTX_DIR/sandbox/shim.py"
    ;;
  egress-proxy)
    mkdir -p "$CTX_DIR/app/sandbox"
    cp "$BACKEND_DIR/app/sandbox/egress_proxy.py" "$CTX_DIR/app/sandbox/egress_proxy.py"
    ;;
  *)
    echo "unknown sandbox role: $ROLE" >&2
    exit 2
    ;;
esac
