#!/usr/bin/env bash
# Phase 28.5-CI -- security certification (network/secrets/resources/browser/
# cancellation/reaper). STRICT: any critical skip FAILS.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT_DIR="${CAP_CERT_OUT:-outputs/cap-cert}"
mkdir -p "$OUT_DIR"
export CAP_CERTIFICATION_STRICT=1
export CAP_CERT_OUT="$OUT_DIR"
export EGRESS_PROXY_URL="${EGRESS_PROXY_URL:-http://${CAP_CERT_PREFIX:-capcert}-egress:8080}"
export CAP_SANDBOX_NETWORK="${CAP_SANDBOX_NETWORK:-cap-sandbox-egress}"
# PG service container credentials (postgres:16-alpine, user=cap db=cap283 pw=cap)
export CAP_CERT_PG_DSN="${CAP_CERT_PG_DSN:-postgresql+asyncpg://cap:cap@127.0.0.1:55432/cap283}"

cd backend
uv run pytest \
  tests/test_phase_28_5_linux_network.py \
  tests/test_phase_28_5_linux_secrets.py \
  tests/test_phase_28_5_linux_resources.py \
  tests/test_phase_28_5_linux_reaper.py \
  tests/test_phase_28_5_container_integration.py \
  tests/test_phase_28_5_sandbox_image.py \
  tests/test_phase_28_5_oci_reaper.py \
  -m certification \
  --junitxml="$OUT_DIR/junit-security.xml" \
  -p no:cacheprovider
echo "SECURITY CERTIFICATION OK"
