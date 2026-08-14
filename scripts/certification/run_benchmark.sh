#!/usr/bin/env bash
# Phase 28.5-CI -- 500-run correctness benchmark (release-blocking).
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT_DIR="${CAP_CERT_OUT:-outputs/cap-cert}"
mkdir -p "$OUT_DIR"
export CAP_CERTIFICATION_STRICT=1
export CAP284_BENCH_N="${CAP284_BENCH_N:-500}"
export CAP284_BENCH_LAB="${CAP284_BENCH_LAB:-40}"
export CAP_CERT_PG_DSN="${CAP_CERT_PG_DSN:-postgresql+asyncpg://cap:cap@127.0.0.1:55432/cap283}"
cd backend
# Release-tag 500-run correctness: OCI/PG durability (28.4) plus the SQLite
# durability benchmark (28.2) that is excluded from the main full regression
# because it is runner-latency sensitive. Both must reach 500 terminal / 0
# stuck for the release gate.
uv run pytest \
  tests/test_phase_28_4_benchmark.py \
  tests/test_phase_28_2_500_benchmark.py \
  --timeout=1800 --timeout-method=thread \
  --junitxml="$OUT_DIR/junit-benchmark.xml" \
  -p no:cacheprovider -s
echo "BENCHMARK CERTIFICATION OK"
