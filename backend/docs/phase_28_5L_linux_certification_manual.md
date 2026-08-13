# CAP Phase 28.5-L — Linux Runtime Certification Manual

> **Certification stop notice (this host):** Phase 28.5-L is a *runtime
> certification* phase. The certification host is a **Windows 11 host with no
> Linux container runtime** (see §0 below). Per the phase contract — *"若
> runtime 不可用：停止认证。不得再次用 mock/unit test 代替。"* — the
> runtime certification is **stopped here**, no gate is upgraded to PASS on
> the strength of unit tests, and this manual documents the EXACT steps to
> certify on any Linux host / CI.
>
> Gate transitions recorded below are conditional: each "→ PASS" row is the
> acceptance criterion, not a claim.

---

## §0. Environment (measured on the certification host)

| Probe | Result |
|---|---|
| `uname -a` | `MINGW64_NT-10.0-22631 DESKTOP-70STOTJ ... x86_64 Msys` (Windows 11 host) |
| `/etc/os-release` | absent (not a Linux host) |
| `docker --version` | Docker 29.6.2 (client only) |
| `docker info` | daemon unreachable: `npipe:////./pipe/dockerDesktopLinuxEngine` not found |
| Docker Desktop | starts, no daemon after 5–10 min, process exits (WSL2 backend cannot initialize) |
| `podman/containerd/nerdctl` | not installed |
| WSL probe | blocked by host security policy (system-level tools disabled) |
| C: free space | ~13 GB (system drive, tight) |

**Verdict: no Linux container runtime on this host; acquisition of one is not
possible under the current security policy / disk constraints. Runtime
certification is stopped. The remainder of this manual is the executable
certification plan for a Linux host (Docker ≥ 24, or Podman ≥ 4).**

## §0b. Target Linux environment requirements

```
Linux kernel >= 5.15  (cgroup v2 preferred; v1 acceptable)
docker >= 24 / podman >= 4 / containerd + nerdctl
iptables or nftables backend (docker info | grep -i iptables)
os-release: any (Debian/Ubuntu/RHEL tested targets)
```

Confirm before starting: `docker run --rm hello-world` must succeed.

---

## §1. Repository fact-check (already verified on this host)

Files reviewed against the 28.5 report: `oci_provider.py`, `oci_protocol.py`,
`oci_shim.py`, `oci_reaper.py`, `egress_proxy.py`, `sandboxed_fetch.py`,
`sandboxed_browser.py`, `worker_main.py`, `health.py`, `store.py`, `gc.py`,
`docker/*`, `docker-compose.yml`, `.env.example`, `tests/test_phase_28_5_*`.
The report and code are consistent; no corrections needed.

## §2. Build real images

```bash
cd backend
bash docker/build_sandbox_images.sh --with-browser
# verify all three images exist
docker images | grep -E "cap-(sandbox|egress)"
# record digests
docker inspect --format '{{index .RepoDigests 0}}' cap-sandbox-http:latest
docker inspect --format '{{index .RepoDigests 0}}' cap-sandbox-browser:latest
docker inspect --format '{{index .RepoDigests 0}}' cap-egress-proxy:latest
```

Expected: three images built; digest pinned in CI output. **Gate 21 (supply
chain)**: record base digests + `pip freeze` inside the image
(`docker run --rm cap-sandbox-http:latest python -m pip list`).

## §3. Run all docker-gated tests

```bash
cd backend
python -m pytest tests/test_phase_28_5_container_integration.py \
  tests/test_phase_28_5_sandbox_image.py -v
```

Record passed / failed / skipped / duration. **No runtime test may be skipped
without a documented reason.** The five integration tests cover: containerized
fetch, validator-bypass network isolation, memory OOM, pid bomb, CPU quota
inspection. The image tests cover: non-root build inspect, read-only rootfs +
tmpfs, shim SSRF.

## §4–§11. Egress topology + adversarial network tests (CRITICAL)

```bash
# 1. network topology
docker network inspect cap-sandbox-egress   # sandbox containers must ONLY be on this network
docker network inspect cap-network          # worker/API on this; NO overlap with sandbox network

# 2. run a throwaway sandbox container and inspect its view
docker run -d --rm --network cap-sandbox-egress --name cap-netprobe \
  cap-sandbox-http:latest sh -c "sleep 300"
docker exec cap-netprobe ip addr; docker exec cap-netprobe ip route
docker exec cap-netprobe cat /etc/resolv.conf
docker inspect cap-netprobe --format '{{.NetworkSettings.Networks}}'

# 3. DIRECT egress attempts from inside the container (bypass app+proxy)
docker exec cap-netprobe sh -c "env -u HTTP_PROXY -u HTTPS_PROXY python -c '
import socket
for host in [\"93.184.216.34\", \"1.1.1.1\"]:
    s=socket.socket(); s.settimeout(5)
    try: s.connect((host,443)); print(host,\"REACHABLE\"); 
    except Exception as e: print(host,\"BLOCKED\",e)
'"
#   EXPECTED: every line BLOCKED. If any public IP is reachable directly -> GATE 10/11 FAILED.

# 4. private / metadata targets (direct, no proxy)
docker exec cap-netprobe sh -c "python -c '
import socket
for t in [(\"127.0.0.1\",5432),(\"127.0.0.1\",9000),(\"172.17.0.1\",80),
          (\"169.254.169.254\",80),(\"192.168.1.1\",80),(\"::1\",80)]:
    s=socket.socket(); s.settimeout(3)
    try: s.connect(t); print(t,\"REACHABLE\")
    except Exception as e: print(t,\"BLOCKED\")
'"
#   EXPECTED: all BLOCKED (PG / MinIO / metadata / RFC1918 / IPv6 loopback).

# 5. controlled egress path works (via egress proxy)
docker exec cap-netprobe sh -c "curl -x http://egress-proxy:8080 -s -o /dev/null -w '%{http_code}' https://example.com"
#   EXPECTED: 200 (or 3xx), NOT a proxy 403.
```

**§5 proxy bypass attacks** (all must be BLOCKED):

```bash
docker exec cap-netprobe sh -c 'unset HTTP_PROXY HTTPS_PROXY; curl -s --connect-timeout 5 http://1.1.1.1/ | head -c 80'
docker exec cap-netprobe sh -c 'python -c "import socket;s=socket.socket();s.settimeout(5);s.connect((\"1.1.1.1\",80))" 2>&1 | tail -1'
# DNS-rebind style: hostname resolving to private IP must be denied by the proxy AND by route
docker exec cap-netprobe sh -c 'curl -x http://egress-proxy:8080 -s -o /dev/null -w "%{http_code}" http://169.254.169.254/'
#   EXPECTED: 403 from the proxy for the private CONNECT; direct attempts time out.
```

## §6. PostgreSQL isolation

```bash
docker exec cap-netprobe python -c "
import socket
s=socket.socket(); s.settimeout(3)
try: s.connect(('<POSTGRES_IP>',5432)); print('PG REACHABLE -> GATE FAILED')
except Exception as e: print('PG BLOCKED:', e)"
```
Expected: BLOCKED. Worker container must still reach PG (control plane test in
§24 HA uses the real worker).

## §7. MinIO isolation

```bash
docker exec cap-netprobe python -c "
import socket
s=socket.socket(); s.settimeout(3)
try: s.connect(('<MINIO_IP>',9000)); print('MINIO REACHABLE -> GATE FAILED')
except Exception as e: print('MINIO BLOCKED:', e)"
docker exec cap-netprobe env | grep -iE "AWS_|MINIO|CAP_SECRET" || echo "no credentials in sandbox env"
```
Expected: BLOCKED + no credential env.

## §8. Secret docker-inspect audit (CRITICAL)

```bash
# run an execution with a secret through the real provider (or shim with CAP_SECRET_* env)
docker run -d --rm --name cap-secrettest -e CAP_SECRET_cap_db_pass=hunter2secret \
  --network cap-sandbox-egress cap-sandbox-http:latest sh -c "sleep 300"
docker inspect cap-secrettest --format '{{json .Config.Env}}'   # <- check for plaintext secret
docker inspect cap-secrettest --format '{{json .Config.Labels}}'
docker inspect cap-secrettest --format '{{json .Args}}'
docker logs cap-secrettest
docker run --rm cap-sandbox-http:latest env | grep -c CAP_SECRET || echo "image layers carry no secret"
```
**If the secret appears in `Config.Env`/labels/cmdline/logs/image layers, the
Secrets gate stays PARTIAL/FAILED and delivery must move to an ephemeral
tmpfs-mounted secret file or stdin pipe** (delivery redesign is out of scope
for this manual but is the required fix). Record the outcome.

## §9–§10. Docker socket / control-plane threat model

```bash
docker inspect acquisition-worker --format '{{json .Mounts}}'   # confirm /var/run/docker.sock mount
# worker (control plane) can manage containers -- this is by design
docker exec acquisition-worker docker run --rm --privileged alpine id   # worker => docker host access
```
Report MUST split:
- **A. Sandbox workload isolation** (certified by §4–§7, §11–§15, §18)
- **B. Worker/control-plane isolation** — if the worker mounts an unrestricted
  socket, B is **NOT certified** and is a Known Limitation (§29).
Optional hardening: rootless podman / restricted socket proxy / dedicated
sandbox-host; record whichever is adopted.

## §11. Filesystem adversarial test

```bash
docker exec cap-netprobe sh -c 'touch /forbidden 2>&1; echo "rootfs write rc=$?"'
docker exec cap-netprobe sh -c 'echo x >> /etc/passwd 2>&1; echo "etc write rc=$?"'
docker exec cap-netprobe sh -c 'ls /var/run/docker.sock 2>&1'
docker exec cap-netprobe sh -c 'mount /dev/sda1 /mnt 2>&1'
docker exec cap-netprobe sh -c 'echo 1 > /proc/sys/kernel/panic 2>&1'
docker exec cap-netprobe sh -c 'touch /tmp/ok && echo tmp-writable'
docker rm -f cap-netprobe; docker run --rm cap-sandbox-http:latest sh -c 'ls /tmp 2>&1'
```
Expected: all rootfs/system writes fail (read-only), `/tmp` writable, and
after container removal the tmpfs data is gone.

## §12. Memory limit real test

```bash
docker run --rm --memory 64m --memory-swap 64m --network none \
  cap-sandbox-http:latest python -c "x=bytearray(1024*1024*512)"
echo "rc=$?"   # EXPECTED non-zero (OOMKilled)
docker inspect <container> --format '{{.HostConfig.Memory}}'   # 67108864
# host/worker/PG must stay alive: curl the worker /readyz + PG SELECT 1
```

## §13. CPU limit real test

```bash
docker run -d --rm --cpus 0.5 --name cap-cputest --network none cap-sandbox-http:latest sh -c "while :; do :; done"
docker inspect cap-cputest --format '{{.HostConfig.NanoCpus}}'   # 500000000
docker stats --no-stream cap-cputest                              # ~50% CPU, not unbounded
```

## §14. PID limit real test

```bash
docker run --rm --pids-limit 64 --network none cap-sandbox-http:latest sh -c \
  "i=0; while true; do sh -c 'sleep 5' & i=\$((i+1)); done"
# EXPECTED: fork/spawn fails at the limit; process exits; host unaffected
```

## §15. Hard cancellation real test

```bash
# run a long sandbox execution, then CANCEL_REQUESTED through the worker API;
# assert in order: container exited -> removed -> run.status == CANCELLED
# instrument with the 28.4 cancellation suite pointed at the OCI provider, plus:
docker ps -a --filter label=cap.sandbox.execution_id   # empty after cancellation
# record termination latency (CANCEL_REQUESTED commit -> container gone)
```

## §16–§17. Worker kill -9 + reaper fencing (real containers)

```bash
# start worker A + worker B (OCI mode); A runs a long acquisition
kill -9 <A pid>
# watch: A's container lingers -> lease expires -> B reclaims -> B starts a NEW
# container (different execution_id) -> reaper removes ONLY A's container
docker ps -a --filter label=cap.sandbox.execution_id
# assert: B's container present, A's removed, run terminal, recovery_count >= 1
```
Reaper adversarial race: keep A's old container alive, let B reclaim and start
a new container, run `reconcile_once()`; assert only A's container id is
removed (this is already unit-certified; §17 requires real container ids).

## §18. Real browser certification

```bash
# browser image, non-root, ephemeral profile, pids/mem limits
docker run --rm --network cap-sandbox-egress --pids-limit 256 --memory 1g \
  cap-sandbox-browser:latest python -c "print('browser image ok')"
# run the 28.4 browser_isolation suite against the OCI browser path:
python -m pytest tests/test_phase_28_4_browser_isolation.py -v
# success / cancel / timeout / worker kill / container kill scenarios; end state:
docker ps -a --filter label=cap.sandbox.execution_id   # empty
pgrep -a chrome | grep -c sandbox || echo "no orphan chromium attributable to sandbox"
```

## §19. Security context inspect

```bash
docker inspect <sandbox> --format 'Privileged={{.HostConfig.Privileged}} Readonly={{.HostConfig.ReadonlyRootfs}} User={{.Config.User}} Pids={{.HostConfig.PidsLimit}} Mem={{.HostConfig.Memory}} Nano={{.HostConfig.NanoCpus}}'
docker inspect <sandbox> --format '{{json .HostConfig.CapAdd}} {{json .HostConfig.CapDrop}} {{json .HostConfig.SecurityOpt}}'
```
Expected: Privileged=false, Readonly=true, User=capuser, Pids>0, Mem>0,
Nano>0, CapAdd=null, SecurityOpt contains `no-new-privileges:true` (add
`--security-opt no-new-privileges` to the provider if absent, then rebuild +
retest).

## §20. OCI protocol attack test

```bash
# feed hostile payloads to the shim (already unit-tested; runtime re-confirm)
echo '{"version":99,"operation":"http_fetch"}' | docker run --rm -i cap-sandbox-http:latest
echo '{"operation":"unknown","run_id":"x","sandbox_execution_id":"y","url":"z"}' | docker run --rm -i cap-sandbox-http:latest
# oversized / malformed / path-injection / command-injection / secret-like fields
python - <<'EOF' | docker run --rm -i cap-sandbox-http:latest
import json
bad = [
  {"version":1,"operation":"http_fetch","run_id":"r","sandbox_execution_id":"00000000-0000-0000-0000-000000000001","url":"http://x; touch /pwned"},
  {"version":1,"operation":"http_fetch","run_id":"r","sandbox_execution_id":"00000000-0000-0000-0000-000000000001","url":"http://x","fencing_token":"SECRET"},
  "{malformed json",
]
for p in bad: print(json.dumps(p) if not isinstance(p,str) else p)
EOF
# EXPECTED: every case returns a protocol error; no shell execution; no worker-side code execution
```

## §21. Image supply chain

Record base image digests (`docker inspect --format '{{index .RepoDigests 0}}'`),
pinned package versions (`pip list` inside image), Playwright/Chromium
compatibility (browser image `python -m playwright --version`). Pin digests in
the Dockerfiles for CI reproducibility.

## §22–§23. Dependency fail-closed

```bash
# stop each dependency and confirm worker readiness flips + no claims happen:
#   docker stop cap-egress-proxy   -> /readyz 503, no new claims, NO unrestricted fallback
#   stop MinIO                     -> /readyz 503
#   stop PostgreSQL                -> /readyz 503
# restart -> worker safely resumes
# CRITICAL: with the egress proxy DOWN, a sandbox must NOT gain direct Internet
docker exec <sandbox> curl -s --connect-timeout 5 http://1.1.1.1/ | head -c 40
#   EXPECTED: still BLOCKED (fail-closed), otherwise network architecture FAILED
```

## §24. Multi-worker real OCI HA (100 runs)

```bash
cd backend
CAP285_HA_N=100 python -m pytest tests/test_phase_28_4_multi_worker_ha.py -v
# with the worker daemon in OCI mode + egress enforcement + PG + MinIO + API
# mid-run: kill -9 worker A
# EXPECTED: 100 terminal, 0 stuck, 0 stale evidence attach, 0 managed orphan
# container, 0 browser orphan, recovery_count == actual crashes
```

## §25. 500-run OCI benchmark

```bash
cd backend
CAP284_BENCH_N=500 CAP284_BENCH_LAB=40 python -m pytest tests/test_phase_28_4_benchmark.py -v
# with SANDBOX_PROVIDER=oci-sandbox; 2+ workers; PG + MinIO + real containers +
# synthetic lab (HTTP + redirect + pagination; browser subset via
# CAP284_BENCH_BROWSER=1 if wired)
# record: enqueue/execution throughput, p50/p95/p99 latency, container startup
# latency, CPU/memory, claim conflicts, recovery count, stale reject, network
# deny count, OOM/PID events, orphan containers, orphan blobs; kill a worker mid-run.
# EXPECTED: 500 terminal, 0 stuck, 0 stale attach, 0 managed orphan container.
```

## §26. Full regression

```bash
cd backend
# SQLite 28.1-28.3
python -m pytest tests/test_phase_28_1_worker_path.py tests/test_phase_28_2_*.py \
  tests/test_phase_28_3_*.py
# PG + object store + sandbox 28.4
python -m pytest tests/test_phase_28_4_*.py tests/test_phase_28_3_postgres_concurrency.py \
  tests/test_phase_28_3_worker_daemon.py tests/test_phase_28_3_process_isolation.py
# 28.5 unit
python -m pytest tests/test_phase_28_5_*.py
# 28.5 OCI runtime (docker-gated)
python -m pytest tests/test_phase_28_5_container_integration.py tests/test_phase_28_5_sandbox_image.py
```

## §27. Gate transition criteria (no PASS without real runs)

| Gate | Condition to flip PARTIAL → PASS (this manual's §) |
|---|---|
| Real Isolation | §2–§3 images + §19 security context + §4–§7 network/fs isolation real |
| Resource Enforcement | §12 memory OOM + §13 NanoCpus + §14 pids limit, worker/PG alive |
| Defense in Depth | §4–§5 direct egress blocked + proxy path works + §23 fail-closed |
| Browser Containerization | §18 real Chromium contained, 0 orphans |
| Real Integration | §24 100-run HA + §25 500-run benchmark green |
| Secrets | §8 docker-inspect audit: no secret in inspect/labels/cmdline/logs/image |

## §28. Critical pass conditions (checklist)

- [ ] direct sandbox Internet path blocked
- [ ] proxy-enforced public egress works
- [ ] private/metadata denied
- [ ] sandbox cannot reach PG / MinIO internal
- [ ] filesystem isolation real (read-only rootfs)
- [ ] memory / CPU / PID cgroup real
- [ ] CANCELLED only after container gone
- [ ] worker kill leaves no permanent sandbox orphan (reaper)
- [ ] stale reaper cannot kill new owner (execution/lease fencing)
- [ ] Chromium contained
- [ ] no silent provider downgrade in production config
- [ ] secret does not leak via inspect/log/image/protocol

## §29. Docker socket report (final report requirement)

On this host the report must state BOTH:

```
Sandbox workload isolation: certified          (after §4–§7, §11–§15, §18 pass on Linux)
Worker-to-host control-plane isolation: NOT certified   (worker mounts unrestricted /var/run/docker.sock)
```

## §30. Final report deliverable

After the manual executes on Linux, produce `CAP Phase 28.5-L — Linux
Runtime Certification Report` with the 29 sections of the phase spec, every
PASS backed by a real command/runtime observation + test + result. Until then,
the Phase 28.5 gates that are runtime-bound remain **PARTIAL** by design.
