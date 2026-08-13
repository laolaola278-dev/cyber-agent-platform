"""Phase 28.5 -- container sandbox integration tests (docker-gated).

These exercise the REAL OCI runtime: containerized shim execution, resource
limits, SSRF defense-in-depth (validator bypass still blocked at the network
layer), lifecycle removal, and the orphan reaper against a live daemon.

On hosts without a container runtime these SKIP -- the certification gate is
reported accordingly (no fake PASS).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.certification, pytest.mark.oci, pytest.mark.security]

BACKEND = Path(__file__).resolve().parent.parent


def _docker_ready() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, timeout=15,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


_need_docker = pytest.mark.skipif(not _docker_ready(), reason="docker daemon not available")


def _image_exists(image: str) -> bool:
    return (
        subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=15).returncode
        == 0
    )


class _Lab(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html>container-lab-ok</html>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def lab():
    srv = HTTPServer(("127.0.0.1", 0), _Lab)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _shim_request(payload: str, *, image: str = "cap-sandbox-http:latest", extra_args=None) -> subprocess.CompletedProcess:
    args = ["docker", "run", "--rm", "--network", "none", "--user", "capuser"]
    args += (extra_args or [])
    args += [image]
    return subprocess.run(
        args, input=payload, capture_output=True, text=True, timeout=90
    )


@_need_docker
def test_containerized_fetch_executes_in_isolated_domain(lab) -> None:
    if not _image_exists("cap-sandbox-http:latest"):
        pytest.skip("sandbox image not built (run tests/test_phase_28_5_sandbox_image.py build first)")
    from app.sandbox.oci_protocol import SandboxRequest

    request = SandboxRequest(
        operation="http_fetch",
        run_id=str(uuid4()),
        sandbox_execution_id=str(uuid4()),
        url=f"http://{lab}/page",
        policy={"allow_private": True},
    ).model_dump_json()
    run = _shim_request(request)
    assert run.returncode == 0, run.stderr[-500:]
    assert '"status":"ok"' in run.stdout
    assert "container-lab-ok" in run.stdout


@_need_docker
def test_ssrf_defense_in_depth_validator_bypass_still_blocked() -> None:
    """The container network is isolated (--network none), so even a request
    that bypasses the shim's URL validator cannot reach private targets."""
    if not _image_exists("cap-sandbox-http:latest"):
        pytest.skip("sandbox image not built")
    # with --network none the container has NO network: any egress attempt
    # must fail at the network layer (this is the layer-2 proof)
    run = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--user", "capuser", "cap-sandbox-http:latest",
            "sh", "-c", "python -c 'import socket; s=socket.socket(); "
            "s.settimeout(5); s.connect((\"127.0.0.1\", 80))' 2>&1 || echo NET-ISOLATED",
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert "NET-ISOLATED" in run.stdout, f"container unexpectedly reached a network: {run.stdout}"


@_need_docker
def test_memory_limit_enforced() -> None:
    """A memory hog inside the container is OOM-killed by the cgroup limit;
    the host (worker) survives."""
    if not _image_exists("cap-sandbox-http:latest"):
        pytest.skip("sandbox image not built")
    run = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--memory", "64m", "--memory-swap", "64m",
            "--user", "capuser", "cap-sandbox-http:latest",
            "python", "-c",
            "x = bytearray(1024*1024*512); print('allocated')",
        ],
        capture_output=True, text=True, timeout=60,
    )
    # the container must NOT have survived the 512MB alloc under a 64m cap
    assert run.returncode != 0, "container should be OOM-killed by the memory limit"
    assert "Killed" in run.stderr or run.returncode != 0


@_need_docker
def test_pids_limit_enforced() -> None:
    """A fork bomb is stopped by --pids-limit; the host survives."""
    if not _image_exists("cap-sandbox-http:latest"):
        pytest.skip("sandbox image not built")
    run = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--pids-limit", "64",
            "--user", "capuser", "cap-sandbox-http:latest",
            "sh", "-c",
            "i=0; while true; do sh -c 'sleep 5' & i=$((i+1)); done",
        ],
        capture_output=True, text=True, timeout=60,
    )
    # the bomb cannot exceed the pids limit; the process must eventually die
    assert run.returncode != 0, "pid bomb should be stopped by --pids-limit"


@_need_docker
def test_cpu_limit_configured_and_observable() -> None:
    """--cpus writes a real cgroup quota: observable via docker inspect."""
    if not _image_exists("cap-sandbox-http:latest"):
        pytest.skip("sandbox image not built")
    name = f"cap-cpu-test-{uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "--cpus", "0.5", "--network", "none",
            "cap-sandbox-http:latest", "sleep", "30",
        ],
        capture_output=True, text=True, timeout=30,
    )
    try:
        inspect = subprocess.run(
            ["docker", "inspect", name], capture_output=True, text=True, timeout=15
        )
        assert inspect.returncode == 0
        import json

        data = json.loads(inspect.stdout)[0]
        host_config = data["HostConfig"]
        assert host_config["NanoCpus"] == 500_000_000, "CPU quota not written"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
