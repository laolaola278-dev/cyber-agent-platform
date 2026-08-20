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
# Release-tag 500-run correctness: OCI/PG durability (28.4, n=500 across 8
# parallel worker processes) + 40-run real lab acquisition with durable MinIO
# blobs. This IS the "500-run OCI correctness" gate. The SQLite durability
# benchmark (28.2) is deliberately NOT part of the release gate: it serializes
# the full acquisition pipeline against SQLite's single-writer lock, making it
# runner-latency sensitive (344s on a fast runner, 20+ min on a contended one,
# exceeding its own 1200s pytest timeout -- observed in the v1.0.0-rc2 run). It
# stays in the test suite as a dev/regression benchmark (general CI deselects
# it for speed).
uv run pytest \
  tests/test_phase_28_4_benchmark.py \
  --timeout=1800 --timeout-method=thread \
  --junitxml="$OUT_DIR/junit-benchmark.xml" \
  -p no:cacheprovider -s
echo "BENCHMARK CERTIFICATION OK"
