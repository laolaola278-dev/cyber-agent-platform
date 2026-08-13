#!/usr/bin/env bash
# Phase 28.5-CI -- build real sandbox images + emit sandbox-images.json.
set -euo pipefail
cd "$(dirname "$0")/../.."
BACKEND_DIR="backend"
OUT_DIR="${CAP_CERT_OUT:-outputs/cap-cert}"
mkdir -p "$OUT_DIR"

bash backend/docker/build_sandbox_images.sh --with-browser

python - "$OUT_DIR" <<'PYEOF'
import json, os, subprocess, sys, time
out = sys.argv[1]
def inspect(img, key):
    r = subprocess.run(["docker", "inspect", img, "--format", "{{.%s}}" % key],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None
def df_hash(path):
    import hashlib
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return h
images = {}
for name, df in [
    ("http", "backend/docker/sandbox-http/Dockerfile"),
    ("browser", "backend/docker/sandbox-browser/Dockerfile"),
    ("egress_proxy", "backend/docker/egress-proxy/Dockerfile"),
]:
    tag = {"http": "cap-sandbox-http:latest",
           "browser": "cap-sandbox-browser:latest",
           "egress_proxy": "cap-egress-proxy:latest"}[name]
    base = {"http": "python:3.13.12-slim-bookworm",
            "browser": "cap-sandbox-http:latest",
            "egress_proxy": "python:3.13.12-slim-bookworm"}[name]
    images[name] = {
        "image_id": inspect(tag, "Id"),
        "repo_digest": inspect(tag, "RepoDigests"),
        "size_bytes": inspect(tag, "Size"),
        "created": inspect(tag, "Created"),
        "base_image": base,
        "base_digest": inspect(base, "RepoDigests") or inspect(base, "Id"),
        "dockerfile_sha256": df_hash(df),
    }
payload = {"images": images, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
with open(os.path.join(out, "sandbox-images.json"), "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
print(json.dumps(payload, indent=2))
PYEOF
echo "IMAGES BUILT + sandbox-images.json emitted"
