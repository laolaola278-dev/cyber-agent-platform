#!/usr/bin/env bash
# Phase 28.5-CI -- multi-worker HA (100 runs, kill -9 a worker mid-run).
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT_DIR="${CAP_CERT_OUT:-outputs/cap-cert}"
mkdir -p "$OUT_DIR"
export CAP_CERTIFICATION_STRICT=1
export CAP_SANDBOX_NETWORK="${CAP_SANDBOX_NETWORK:-cap-sandbox-egress}"
export CAP284_HA_N="${CAP284_HA_N:-100}"
export CAP_CERT_PG_DSN="${CAP_CERT_PG_DSN:-postgresql+asyncpg://cap:cap@127.0.0.1:55432/cap283}"
cd backend
uv run pytest \
  tests/test_phase_28_4_multi_worker_ha.py \
  --junitxml="$OUT_DIR/junit-ha.xml" \
  -p no:cacheprovider
echo "HA CERTIFICATION OK"
