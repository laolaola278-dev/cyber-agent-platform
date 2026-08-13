#!/usr/bin/env bash
# Phase 28.5-CI -- collect topology/security artifacts for audit.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT_DIR="${CAP_CERT_OUT:-outputs/cap-cert}"
NET="${CAP_SANDBOX_NETWORK:-cap-sandbox-egress}"
mkdir -p "$OUT_DIR"
docker network inspect "$NET" > "$OUT_DIR/network-inspect.json" 2>/dev/null || true
(ip route || true) > "$OUT_DIR/ip-route.txt"
(iptables-save 2>/dev/null || echo unavailable) > "$OUT_DIR/iptables-save.txt"
(nft list ruleset 2>/dev/null || echo unavailable) > "$OUT_DIR/nft-ruleset.txt"
(cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null || echo "cgroup v1") > "$OUT_DIR/cgroup.txt"
# security context of the reference sandbox image (if any container exists)
echo "ARTIFACTS COLLECTED -> $OUT_DIR"
