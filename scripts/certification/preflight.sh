#!/usr/bin/env bash
# Phase 28.5-CI -- environment preflight.
# Exits NON-ZERO if any hard runtime requirement is missing. A certification
# job must never run green on a broken environment.
set -uo pipefail
OUT_DIR="${CAP_CERT_OUT:-outputs/cap-cert}"
mkdir -p "$OUT_DIR"

echo "=== preflight: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
uname -a | tee "$OUT_DIR/uname.txt"
(cat /etc/os-release 2>/dev/null || echo "no os-release") | tee "$OUT_DIR/os-release.txt"

# 1. container runtime REQUIRED
if ! command -v docker >/dev/null 2>&1; then
  echo "FATAL: docker CLI not found"; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "FATAL: docker daemon not reachable"; exit 1
fi
docker version | tee "$OUT_DIR/docker-version.txt"
docker info 2>&1 | tee "$OUT_DIR/docker-info.txt"

# 2. required images (checked again before tests; fail fast here)
for img in "${CAP_SANDBOX_IMAGE:-cap-sandbox-http:latest}" \
           "${CAP_SANDBOX_BROWSER_IMAGE:-cap-sandbox-browser:latest}" \
           "cap-egress-proxy:latest"; do
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    echo "FATAL: required image missing: $img"; exit 1
  fi
done

# 3. cgroup version + network backend (informational, recorded)
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then echo "cgroup v2" ; else echo "cgroup v1"; fi | tee "$OUT_DIR/cgroup.txt"
(ip route 2>/dev/null || true) | tee "$OUT_DIR/ip-route.txt"
(iptables-save 2>/dev/null || echo "iptables-save unavailable") | head -50 | tee "$OUT_DIR/iptables-save.txt"
(nft list ruleset 2>/dev/null || echo "nft unavailable") | head -30 | tee "$OUT_DIR/nft-ruleset.txt"

# 4. resources (recorded)
echo "cpus=$(nproc)" | tee "$OUT_DIR/resources.txt"
echo "mem=$(free -m | awk '/Mem:/{print $2}')MB" | tee -a "$OUT_DIR/resources.txt"
echo "disk=$(df -BG . | awk 'NR==2{print $4}')" | tee -a "$OUT_DIR/resources.txt"

# 5. required network pieces
if ! docker network inspect "${CAP_SANDBOX_NETWORK:-cap-sandbox-egress}" >/dev/null 2>&1; then
  echo "FATAL: sandbox network missing: ${CAP_SANDBOX_NETWORK:-cap-sandbox-egress}"; exit 1
fi

echo "PREFLIGHT OK"
