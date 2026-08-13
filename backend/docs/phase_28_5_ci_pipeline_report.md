# CAP Phase 28.5-CI — Certification Pipeline Report

| | |
|---|---|
| **Phase** | 28.5-CI — Reproducible Linux Certification Pipeline |
| **Date** | 2026-08-13 |
| **Current host** | Windows 11 (no Linux container runtime — see §"Current execution result") |
| **Target** | GitHub Actions `ubuntu-24.04` (real Linux + docker daemon) |

---

## 1. CI Architecture

A single workflow (`.github/workflows/cap-linux-certification.yml`) with three
cost layers and one **release gate job**:

| Job | Trigger | Content |
|---|---|---|
| `fast-certification` | PR | unit + SQLite/PG subset + OCI smoke + network isolation + secret audit + 20-run HA |
| `full-certification` | push main | full regression + security certification + 100-run HA + report gate |
| `cap-production-certification` | release tag `v*` | **RELEASE GATE**: preflight + full adversarial security + 100-run HA + **500-run OCI benchmark** + full regression + machine-readable artifact assertion |

`release.yml` must list `cap-production-certification` as a required job
before publishing. Any failure / critical skip / missing artifact field →
job non-zero → release blocked.

## 2. Strict Runtime Requirements (no false green)

- `CAP_CERTIFICATION_STRICT=1` — new conftest hook converts **SKIPs on
  `certification`-marked tests into FAILURES**. Verified on this host:
  `docker daemon not available` → `Failed: CAP_CERTIFICATION_STRICT: critical
  certification test skipped` (exit 1), not a green "5 skipped".
- `scripts/certification/preflight.sh` exits non-zero when: docker CLI or
  daemon missing, any required image missing, sandbox network missing.
  Records uname/os-release/docker version/cgroup/iptables/nft/resources.
- Markers registered: `postgres`, `object_store`, `sandbox`, `oci`,
  `browser`, `security`, `certification`, `benchmark`.

## 3. Image Pipeline

`scripts/certification/build_images.sh` → `docker/build_sandbox_images.sh
--with-browser` + emits `sandbox-images.json`:

```json
{ "images": { "http": {"image_id","repo_digest","size_bytes","created",
                        "base_image","base_digest","dockerfile_sha256"},
              "browser": {...}, "egress_proxy": {...} } }
```

Static Dockerfile safety checks run always (non-root, no privileged, no
socket, no host mounts); build + inspect checks are docker-gated and required
in the certification jobs.

## 4. Network Certification (highest-priority gate)

`tests/test_phase_28_5_linux_network.py` (certification + oci + security):

- sandbox **direct public egress BLOCKED** (raw socket, proxy env fully unset)
- sandbox direct private / metadata / PG / MinIO / worker BLOCKED
- sandbox **via egress proxy** → public target allowed
- proxy denies private target (403)
- network topology artifact captured (network inspect / ip route / resolv.conf)

`scripts/certification/collect_artifacts.sh` saves docker network inspect,
ip route, iptables-save, nft ruleset, cgroup — the §10 topology artifact.

## 5. Secret Certification

`tests/test_phase_28_5_linux_secrets.py`:

- random sentinel `CAP_PHASE_285_SECRET_SENTINEL_<hex>` delivered the current
  way (env), then scans `docker inspect` (Env/Labels/Cmd/Args), `docker
  logs`, image history, and the protocol body. **Any hit → Secrets FAIL.**
- canary scanner (`generate_report.py -- secret_canary_scan`) re-scans all
  generated logs/artifacts; a sentinel hit flips `secrets` to FAIL and fails
  the job.
- sandbox env must not inherit AWS/MinIO/root credentials.

If the env-delivery fails the inspect audit on Linux, the fix path is
stdin pipe / tmpfs-mounted secret file / runtime secret mechanism (recorded
in the report, not silently kept).

## 6. Resource Certification

`tests/test_phase_28_5_linux_resources.py`:

- memory: 64m cap + 512MB hog → OOM-killed; `HostConfig.Memory` asserted
  (`configured_limit`/`observed_exit` recorded)
- cpu: `--cpus 0.5` → `NanoCpus == 500_000_000` asserted + bounded observed
  usage
- pids: bounded spawn exceeds `--pids-limit 64` → container stopped, host
  survives
- filesystem: write `/`, write `/etc`, docker.sock, mount, `/proc/sys` all
  fail; `/tmp` writable; data gone after container removal

## 7. Cancellation / Reaper / Browser Certification

`tests/test_phase_28_5_linux_reaper.py`:

- cancellation ordering: `t_cancel <= t_container_exit <= t_cancelled`
  asserted on real containers
- reaper fencing on real container ids: stale A removed, current B untouched

Browser: `test_phase_28_5_container_integration.py` + browser image build;
zero-orphan assertions (managed containers + Chromium processes) run in the
full jobs.

## 8. HA Pipeline

`scripts/certification/run_ha.sh` → `CAP284_HA_N=100
test_phase_28_4_multi_worker_ha.py` (kill -9 a worker mid-run; survivor
reclaims; 100 terminal; 0 stuck; 0 stale attach; recovery counts recorded).

## 9. Benchmark Strategy

`scripts/certification/run_benchmark.sh`:

- **A. Required correctness** (release-blocking): 500 submitted → 500
  terminal, 0 stuck, 0 stale attachment, 0 managed orphan containers.
- **B. Optional performance trend** (recorded, not initially blocking):
  throughput, p50/p95/p99, container startup latency → `benchmark.json`
  baseline for Phase 28.6 SLOs. CI-runner noise must not become a false
  release blocker.

## 10. Machine-Readable Gate Format

`scripts/certification/generate_report.py` → `outputs/cap-28.5-linux-certification.json`:

```json
{
  "phase": "28.5-L",
  "generated_at": "...",
  "environment": {...}, "images": {...},
  "gates": {"container_isolation":"PASS", ..., "secrets":"PASS|FAIL",
            "worker_control_plane_isolation":"PARTIAL"},
  "tests": {"total":N,"outcomes":{...}},
  "secret_canary_leaks": []
}
```

Gate values are **derived from JUnit outcomes** (never hardcoded). The
workflow's final step asserts the JSON contains all 12 required gates with no
NOT_RUN/SKIPPED/FAIL — otherwise the release job fails.

## 11. Release-Blocking Rules

`cap-production-certification` fails (non-zero) when ANY of: docker
unavailable, required image missing, PG/MinIO unavailable, critical test
skipped (strict mode), direct public egress succeeds, PG/MinIO reachable from
sandbox, secret appears in docker inspect, memory/CPU/PID limit not real,
CANCELLED before container exit, orphan container/browser remains,
certification JSON missing fields, artifact generation error.

## 12. Artifacts

Uploaded on `always()` (failures included): JUnit XML (security/ha/benchmark/
regression), `sandbox-images.json`, `cap-28.5-linux-certification.json`,
human report, network inspect, ip route, iptables/nft, cgroup, uname,
os-release, docker version/info.

## 13. Docker Socket Threat Model

`generate_report.py` detects the worker's docker.sock mount and emits
`worker_control_plane_isolation: false` when mounted. The report must state
BOTH: "Sandbox workload isolation: certified" AND "Worker-to-host
control-plane isolation: NOT certified" — never a blanket "host isolation
certified". Known limitation (optional hardening: rootless podman / socket
proxy / dedicated sandbox-host).

## 14. Current Execution Result

**Host: Windows 11, no Linux container runtime** (docker daemon unreachable,
podman/containerd absent, WSL probe blocked by host policy). Therefore:

- Pipeline implementation: **COMPLETE**
  - workflow (3 layers + release gate) ✓
  - preflight / build / setup / run_* / collect / generate_report scripts ✓
  - strict markers + strict-mode skip→fail hook (verified: FAIL on missing
    docker) ✓
  - Linux certification tests (network/secrets/resources/reaper) ✓
  - meta-gate completeness check (verified: all 12 gates NOT_RUN → exit 1) ✓
  - machine-readable artifact + human report generator ✓
- Regressions on this host: 28.5 unit 31/31 + 2 docker-gated skip;
  strict-mode behavior proven; meta-gate blocking proven.
- **LINUX RUNTIME CERTIFICATION: NOT EXECUTED**
- **Phase 28.5 runtime-bound gates: remain PARTIAL**

The pipeline is release-blocking by construction: until
`cap-production-certification` runs on a real Linux runner and produces a
certification JSON with all 12 gates PASS, no release can pass the gate.
