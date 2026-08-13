#!/usr/bin/env bash
# Phase 28.5-CI -- 500-run correctness benchmark (release-blocking).
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT_DIR="${CAP_CERT_OUT:-outputs/cap-cert}"
mkdir -p "$OUT_DIR"
export CAP_CERTIFICATION_STRICT=1
export CAP284_BENCH_N="${CAP284_BENCH_N:-500}"
export CAP284_BENCH_LAB="${CAP284_BENCH_LAB:-40}"
cd backend
uv run pytest \
  tests/test_phase_28_4_benchmark.py \
  --junitxml="$OUT_DIR/junit-benchmark.xml" \
  -p no:cacheprovider -s
echo "BENCHMARK CERTIFICATION OK"
