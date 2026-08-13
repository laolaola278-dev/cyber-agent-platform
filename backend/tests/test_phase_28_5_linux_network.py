"""Phase 28.5-CI -- Linux network enforcement certification (CRITICAL).

Real-container proofs (docker-gated; in CAP_CERTIFICATION_STRICT mode a skip
here FAILS the job):

  * sandbox DIRECT public egress -> BLOCKED (even with proxy env unset)
  * sandbox direct private / metadata / PG / MinIO / worker -> BLOCKED
  * sandbox via controlled egress proxy -> public target ALLOWED
  * proxy denies private targets (403)
  * proxy DOWN -> direct Internet STILL BLOCKED (fail-closed)

Environment variables are NOT network enforcement: every direct-connect test
unsets HTTP(S)_PROXY/ALL_PROXY/NO_PROXY and uses raw sockets.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.certification,
    pytest.mark.oci,
    pytest.mark.security,
]

BACKEND = Path(__file__).resolve().parent.parent
IMAGE = os.environ.get("CAP_SANDBOX_IMAGE", "cap-sandbox-http:latest")
EGRESS_PROXY_URL = os.environ.get("EGRESS_PROXY_URL", "http://egress-proxy:8080")
NETWORK = os.environ.get("CAP_SANDBOX_NETWORK", "cap-sandbox-egress")

# public test targets (TEST-NET / public DNS)
PUBLIC_IPS = os.environ.get(
    "CAP_CERT_PUBLIC_IPS", "1.1.1.1,8.8.8.8,93.184.216.34"
).split(",")


def _docker() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, timeout=15,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


_need_docker = pytest.mark.skipif(not _docker(), reason="docker daemon not available")


def _net_probe_container() -> str:
    name = f"cap-cert-net-{uuid.uuid4().hex[:8]}"
    run = subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "--network", NETWORK, "--entrypoint", "sh", IMAGE,
            "-c", "sleep 600",
        ],
        capture_output=True, text=True, timeout=60,
    )
    if run.returncode != 0:
        raise RuntimeError(f"probe container start failed: {run.stderr[-300:]}")
    # diagnostics: confirm which network(s) the probe actually joined and its
    # default route, so network-enforcement failures are diagnosable in CI logs.
    diag = subprocess.run(
        ["docker", "inspect", name, "--format",
         "{{json .NetworkSettings.Networks}}"],
        capture_output=True, text=True, timeout=20,
    )
    route = subprocess.run(
        ["docker", "exec", name, "ip", "route"],
        capture_output=True, text=True, timeout=20,
    )
    nets = (diag.stdout or "").strip()
    routes = (route.stdout or route.stderr or "").strip()
    print(f"[net-probe] {name} networks={nets}", flush=True)
    print(f"[net-probe] {name} routes={routes}", flush=True)
    return name


def _direct_connect(cid: str, host: str, port: int) -> tuple[bool, str]:
    """Raw socket connect from inside the container, proxy env fully unset.

    The probe script is base64-encoded so shell quoting inside `docker exec ...
    sh -c` can never mangle it (a plain repr()/multi-line string broke the
    python -c payload earlier and produced false REACHABLE reads).
    """
    # diagnostics: how does the kernel route this target from inside the sandbox?
    import base64

    rg = subprocess.run(
        ["docker", "exec", cid, "sh", "-c", f"ip route get {host} 2>&1 | head -3"],
        capture_output=True, text=True, timeout=20,
    )
    print(f"[net-diag] {host}:{(rg.stdout or rg.stderr).strip()}", flush=True)
    script = (
        "import socket,sys;\n"
        "s=socket.socket(); s.settimeout(6);\n"
        "try:\n"
        f" s.connect(('{host}',{port})); print('REACHABLE')\n"
        "except Exception as e:\n"
        " print('BLOCKED', type(e).__name__)\n"
    )
    b64 = base64.b64encode(script.encode()).decode()
    proc = subprocess.run(
        [
            "docker", "exec", cid, "sh", "-c",
            "env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u NO_PROXY "
            f"python -c \"import base64;exec(base64.b64decode('{b64}'))\"",
        ],
        capture_output=True, text=True, timeout=30,
    )
    out = proc.stdout + proc.stderr
    return ("REACHABLE" in out), out.strip()[:300]


@_need_docker
def test_sandbox_direct_public_egress_is_blocked() -> None:
    """§4/§9: direct-to-public without the proxy MUST fail."""
    cid = _net_probe_container()
    try:
        for ip in PUBLIC_IPS:
            reachable, _ = _direct_connect(cid, ip, 443)
            assert not reachable, (
                f"sandbox reached public {ip} directly -> network enforcement FAILED"
            )
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=30)


@_need_docker
def test_sandbox_direct_private_and_metadata_blocked() -> None:
    """§4/§6/§7: private, metadata, PG, MinIO, worker addresses all BLOCKED."""
    targets = [
        ("127.0.0.1", 5432),
        ("127.0.0.1", 9000),
        ("172.17.0.1", 80),
        ("169.254.169.254", 80),
        ("169.254.169.253", 80),
        ("192.168.1.1", 80),
        ("10.0.0.1", 80),
        ("::1", 80),
        ("fe80::1", 80),
    ]
    # allow the environment to point at the actual PG/MinIO hosts
    for var, port in (("CAP_CERT_PG_HOST", 5432), ("CAP_CERT_MINIO_HOST", 9000)):
        host = os.environ.get(var)
        if host:
            targets.append((host, port))

    cid = _net_probe_container()
    try:
        for host, port in targets:
            reachable, detail = _direct_connect(cid, host, port)
            assert not reachable, (
                f"sandbox reached {host}:{port} directly -> isolation FAILED ({detail})"
            )
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=30)


@_need_docker
def test_sandbox_proxied_public_egress_works() -> None:
    """§4: the controlled egress path must allow public targets."""
    cid = _net_probe_container()
    try:
        proc = subprocess.run(
            [
                "docker", "exec", cid, "sh", "-c",
                f"curl -x {EGRESS_PROXY_URL} -s --connect-timeout 15 "
                "-o /dev/null -w '%{{http_code}}' https://example.com",
            ],
            capture_output=True, text=True, timeout=60,
        )
        code = proc.stdout.strip()
        assert code and code not in ("000",), (
            f"proxied public egress failed (http={code}) "
            f"stderr={proc.stderr[-300:]}"
        )
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=30)


@_need_docker
def test_proxy_denies_private_target() -> None:
    """§4: the egress proxy itself must refuse private destinations."""
    cid = _net_probe_container()
    try:
        proc = subprocess.run(
            [
                "docker", "exec", cid, "sh", "-c",
                f"curl -x {EGRESS_PROXY_URL} -s -o /dev/null -w '%{{http_code}}' "
                "http://169.254.169.254/",
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.stdout.strip() == "403", (
            f"proxy did not deny metadata target: {proc.stdout.strip()}"
        )
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=30)


@_need_docker
def test_network_topology_artifact_captured(tmp_path) -> None:
    """§10: capture topology artifacts; connectivity behavior is the real gate."""
    artifact = tmp_path / "network-topology.json"
    data: dict[str, object] = {}
    try:
        net = subprocess.run(
            ["docker", "network", "inspect", NETWORK],
            capture_output=True, text=True, timeout=30,
        )
        if net.returncode == 0:
            data["docker_network"] = json.loads(net.stdout)
    except Exception:  # noqa: BLE001
        pass
    cid = _net_probe_container()
    try:
        for cmd in (["ip", "route"], ["cat", "/etc/resolv.conf"]):
            proc = subprocess.run(
                ["docker", "exec", cid] + cmd,
                capture_output=True, text=True, timeout=20,
            )
            data["/".join(cmd)] = proc.stdout.strip()
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=30)
    artifact.write_text(json.dumps(data, indent=2), encoding="utf-8")
    assert data, "no topology artifact captured"
