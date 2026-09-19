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

# sandbox egress network (isolated bridge -- NO external route; the sandbox
# can only reach the egress proxy on this network. Docker --internal drops the
# network's default gateway so containers cannot reach the public Internet or
# the host loopback directly.)
# Rebuild unconditionally so a stale Nnetbridge peer or a non-internal leftover
# from a previous run can never leak outbound connectivity.
docker network rm "$NET" >/dev/null 2>&1 || true
docker network create --internal "$NET"
echo "[setup] sandbox network $NET internal=$(docker network inspect "$NET" --format '{{.Internal}}')"

# On GitHub Actions, PG and MinIO are already running as service containers.
# Skip the container startup to avoid port conflicts.
if [ "${GITHUB_ACTIONS:-}" != "true" ]; then
  # control network for PG/MinIO (shared with worker, NOT sandbox)
  docker network inspect cap-network >/dev/null 2>&1 || docker network create cap-network

  docker rm -f "${PREFIX}-pg" >/dev/null 2>&1 || true
  docker run -d --name "${PREFIX}-pg" \
    --network cap-network \
    -e POSTGRES_USER=cap -e POSTGRES_PASSWORD=cap -e POSTGRES_DB=cap283 \
    -p 55432:5432 \
    postgres:16-alpine >/dev/null

  docker rm -f "${PREFIX}-minio" >/dev/null 2>&1 || true
  docker run -d --name "${PREFIX}-minio" \
    --network cap-network \
    -e MINIO_ROOT_USER=capadmin -e MINIO_ROOT_PASSWORD=capadmin123 \
    -p 9000:9000 -p 9001:9001 \
    # MinIO server: the vendor archived the open-source build and its community images
    # are gone from Docker Hub, so this is digest-pinned and pulled from the official
    # MinIO org on Quay. Why + how to upgrade: deployment/third-party-images.json,
    # locked by backend/tests/test_third_party_image_lock.py.
    quay.io/minio/minio@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e server /data --console-address :9001 >/dev/null
fi

# egress proxy (the sandbox's ONLY route out). It sits on the isolated
# sandbox network so sandbox containers can reach it, AND it joins the
# default bridge so it can forward to the public Internet. No direct external
# route exists on the sandbox network itself.
# (re)create the proxy on the isolated sandbox network with an external uplink
docker build -q -t cap-egress-proxy:latest \
  -f backend/docker/egress-proxy/Dockerfile backend/ >/dev/null 2>&1 || true
docker rm -f "${PREFIX}-egress" >/dev/null 2>&1 || true
docker run -d --name "${PREFIX}-egress" \
  --network "$NET" --network-alias "${PREFIX}-egress" \
  -e CAP_EGRESS_ALLOW="${CAP_EGRESS_ALLOW:-}" \
  cap-egress-proxy:latest \
  >/dev/null
docker network connect bridge "${PREFIX}-egress" >/dev/null 2>&1 || true

# wait for readiness (GHA: PG/MinIO are service containers, already healthy)
if [ "${GITHUB_ACTIONS:-}" != "true" ]; then
  for i in $(seq 1 60); do
    if docker exec "${PREFIX}-pg" pg_isready -U cap >/dev/null 2>&1 && \
       curl -sf http://127.0.0.1:9000/minio/health/live >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
fi

echo "SETUP OK (prefix=${PREFIX} net=${NET})"
echo "PG=127.0.0.1:55432 MINIO=127.0.0.1:9000 EGRESS=${PREFIX}-egress" | tee "$OUT_DIR/infra.txt"
