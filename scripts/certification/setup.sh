#!/usr/bin/env bash
# Phase 28.5-CI -- start PG / MinIO / egress infrastructure for certification.
#
# On GitHub Actions these are usually service containers; this script is the
# self-hosted fallback and the common entry point for a local Linux cert run.
# Namespaces everything under CAP_CERT_PREFIX so parallel runs never collide.
set -euo pipefail
cd "$(dirname "$0")/../.."

PREFIX="${CAP_CERT_PREFIX:-capcert}"
NET="${CAP_SANDBOX_NETWORK:-cap-sandbox-egress}"
OUT_DIR="${CAP_CERT_OUT:-outputs/cap-cert}"
mkdir -p "$OUT_DIR"

# sandbox egress network (isolated bridge)
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET"

# PostgreSQL (dedicated container, only on the control network)
docker rm -f "${PREFIX}-pg" >/dev/null 2>&1 || true
docker run -d --name "${PREFIX}-pg" \
  --network cap-network \
  -e POSTGRES_USER=cap -e POSTGRES_PASSWORD=cap -e POSTGRES_DB=cap283 \
  -p 55432:5432 \
  postgres:16-alpine >/dev/null

# MinIO (dedicated; control network + host port for tests)
docker rm -f "${PREFIX}-minio" >/dev/null 2>&1 || true
docker run -d --name "${PREFIX}-minio" \
  --network cap-network \
  -e MINIO_ROOT_USER=capadmin -e MINIO_ROOT_PASSWORD=capadmin123 \
  -p 9000:9000 -p 9001:9001 \
  minio/minio:RELEASE.2025-04-22T22-12-26Z server /data --console-address :9001 >/dev/null

# egress proxy (the sandbox's ONLY route out; on BOTH networks so it can
# forward to the public Internet while being reachable from the sandbox net)
docker rm -f "${PREFIX}-egress" >/dev/null 2>&1 || true
docker build -q -t cap-egress-proxy:latest \
  -f backend/docker/egress-proxy/Dockerfile backend/ >/dev/null 2>&1 || true
docker run -d --name "${PREFIX}-egress" \
  --network "$NET" \
  -e CAP_EGRESS_ALLOW="${CAP_EGRESS_ALLOW:-}" \
  cap-egress-proxy:latest >/dev/null

# wait for readiness
for i in $(seq 1 60); do
  if docker exec "${PREFIX}-pg" pg_isready -U cap >/dev/null 2>&1 && \
     curl -sf http://127.0.0.1:9000/minio/health/live >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "SETUP OK (prefix=${PREFIX} net=${NET})"
echo "PG=127.0.0.1:55432 MINIO=127.0.0.1:9000 EGRESS=${PREFIX}-egress" | tee "$OUT_DIR/infra.txt"
