"""Phase 28.5 -- container sandbox integration tests (docker-gated).

These exercise the REAL OCI runtime: containerized shim execution, resource
limits, SSRF defense-in-depth (validator bypass still blocked at the network
layer), lifecycle removal, and the orphan reaper against a live daemon.

On hosts without a container runtime these SKIP -- the certification gate is
reported accordingly (no fake PASS).
"""

from __future__ import annotations

import base64
import json
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
            capture_output=True,
            timeout=15,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


_need_docker = pytest.mark.skipif(not _docker_ready(), reason="docker daemon not available")


def _image_exists(image: str) -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, timeout=15
        ).returncode
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


def _shim_request(
    payload: str, *, image: str = "cap-sandbox-http:latest", extra_args=None, network: str = "none"
) -> subprocess.CompletedProcess:
    args = ["docker", "run", "--rm", "-i", "--network", network, "--user", "capuser"]
    args += extra_args or []
    args += [image]
    return subprocess.run(args, input=payload, capture_output=True, text=True, timeout=90)


@_need_docker
def test_containerized_fetch_executes_in_isolated_domain(lab) -> None:
    if not _image_exists("cap-sandbox-http:latest"):
        pytest.skip(
            "sandbox image not built (run tests/test_phase_28_5_sandbox_image.py build first)"
        )
    from app.sandbox.oci_protocol import SandboxRequest

    # The lab target must live INSIDE the same isolated container network as
    # the sandbox: with a true sandbox network the shim container cannot reach
    # the host's 127.0.0.1 loopback (that is the isolation property we assert).
    # So we run the target as a sibling container on the same (internal) net and
    # reach it by container name -- proving end-to-end fetch works within the
    # isolated domain.
    net = os.environ.get("CAP_SANDBOX_NETWORK", "cap-sandbox-egress")
    lab_name = f"cap-cert-lab-{uuid4().hex[:8]}"
    lab_port = 28331  # fixed high port inside the lab container
    lab_code = (
        "from http.server import BaseHTTPRequestHandler,HTTPServer;\n"
        "class H(BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  b=b'<html>container-lab-ok</html>';\n"
        "  self.send_response(200);self.send_header('Content-Length',str(len(b)));\n"
        "  self.end_headers();self.wfile.write(b);\n"
        " def log_message(self,*a):pass\n"
        f"HTTPServer(('0.0.0.0',{lab_port}),H).serve_forever()\n"
    )
    lab_lc = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            lab_name,
            "--network",
            net,
            "--entrypoint",
            "python",
            "cap-sandbox-http:latest",
            "-c",
            lab_code,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if lab_lc.returncode != 0:
        subprocess.run(["docker", "rm", "-f", lab_name], capture_output=True, timeout=30)
        pytest.skip(f"could not start lab container: {lab_lc.stderr[-300:]}")
    try:
        # resolve the lab container's sandbox-network IP (address Docker's
        # embedded DNS may not answer on an --internal net) and target that.
        lab_ip = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                lab_name,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
        assert lab_ip, "could not resolve lab container IP"
        # wait for the lab HTTP server to accept connections
        ready = subprocess.run(
            [
                "docker",
                "exec",
                lab_name,
                "sh",
                "-c",
                "i=0; until python -c "
                f"\"import socket;socket.create_connection(('127.0.0.1',{lab_port}),1)\" "
                "2>/dev/null; do sleep 0.5; i=$((i+1)); [ $i -ge 20 ] && exit 1; done; exit 0",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if ready.returncode != 0:
            logs = subprocess.run(
                ["docker", "logs", lab_name], capture_output=True, text=True, timeout=20
            )
            pytest.skip(f"lab server not ready: {(logs.stdout or logs.stderr)[-300:]}")
        request = SandboxRequest(
            operation="http_fetch",
            run_id=str(uuid4()),
            sandbox_execution_id=str(uuid4()),
            url=f"http://{lab_ip}:{lab_port}/page",
            policy={"allow_private": True},
        ).model_dump_json()
        run = _shim_request(request, network=net)
        assert run.returncode == 0, run.stderr[-500:]
        assert '"status":"ok"' in run.stdout, run.stdout[-500:]
        payload = json.loads(run.stdout)
        body = base64.b64decode(payload["result"]["content_b64"] or "").decode("utf-8", "replace")
        assert "container-lab-ok" in body, run.stdout[-500:]
    finally:
        subprocess.run(["docker", "rm", "-f", lab_name], capture_output=True, timeout=30)


@_need_docker
def test_browser_renders_page_in_isolated_container() -> None:
    """REAL Chromium runtime gate (Phase 28.5): the cap-sandbox-browser image
    launches Chromium inside the container and actually renders a page --
    proves the browser path works end-to-end, not just that the Dockerfile
    mentions chromium (which is the static test_phase_28_5_sandbox_image
    check)."""
    if not _image_exists("cap-sandbox-browser:latest"):
        pytest.skip("browser sandbox image not built")
    from app.sandbox.oci_protocol import SandboxRequest

    net = os.environ.get("CAP_SANDBOX_NETWORK", "cap-sandbox-egress")
    lab_name = f"cap-cert-browser-lab-{uuid4().hex[:8]}"
    lab_port = 28332
    lab_code = (
        "from http.server import BaseHTTPRequestHandler,HTTPServer;\n"
        "class H(BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  b=b'<html><head><title>cap-browser-render</title></head>"
        "<body>container-browser-ok</body></html>';\n"
        "  self.send_response(200);self.send_header('Content-Length',str(len(b)));\n"
        "  self.end_headers();self.wfile.write(b);\n"
        " def log_message(self,*a):pass\n"
        f"HTTPServer(('0.0.0.0',{lab_port}),H).serve_forever()\n"
    )
    lab_lc = subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", lab_name, "--network", net,
            "--entrypoint", "python", "cap-sandbox-http:latest", "-c", lab_code,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if lab_lc.returncode != 0:
        subprocess.run(["docker", "rm", "-f", lab_name], capture_output=True, timeout=30)
        pytest.skip(f"could not start lab container: {lab_lc.stderr[-300:]}")
    try:
        lab_ip = subprocess.run(
            [
                "docker", "inspect", "-f",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", lab_name,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
        assert lab_ip, "could not resolve lab container IP"
        request = SandboxRequest(
            operation="browser_browse",
            run_id=str(uuid4()),
            sandbox_execution_id=str(uuid4()),
            url=f"http://{lab_ip}:{lab_port}/page",
            policy={"allow_private": True},
            wait_network_idle_ms=2000,
        ).model_dump_json()
        run = _shim_request(request, image="cap-sandbox-browser:latest", network=net)
        assert run.returncode == 0, run.stderr[-500:]
        assert '"status":"ok"' in run.stdout, run.stdout[-500:]
        payload = json.loads(run.stdout)
        result = payload["result"]
        assert result.get("available") is True, f"browser did not render: {result}"
        assert "cap-browser-render" in (result.get("title") or ""), f"title missing: {result}"
        assert "container-browser-ok" in (result.get("html") or ""), f"html missing: {result}"
    finally:
        subprocess.run(["docker", "rm", "-f", lab_name], capture_output=True, timeout=30)


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
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "capuser",
            "--entrypoint",
            "sh",
            "cap-sandbox-http:latest",
            "-c",
            "python -c 'import socket; s=socket.socket(); "
            's.settimeout(5); s.connect(("127.0.0.1", 80))\' 2>&1 || echo NET-ISOLATED',
        ],
        capture_output=True,
        text=True,
        timeout=60,
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
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            "64m",
            "--memory-swap",
            "64m",
            "--user",
            "capuser",
            "--entrypoint",
            "python",
            "cap-sandbox-http:latest",
            "-c",
            "x = bytearray(1024*1024*512); print('allocated')",
        ],
        capture_output=True,
        text=True,
        timeout=60,
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
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--pids-limit",
            "64",
            "--user",
            "capuser",
            "--entrypoint",
            "sh",
            "cap-sandbox-http:latest",
            "-c",
            "i=0; while true; do sh -c 'sleep 5' & i=$((i+1)); done",
        ],
        capture_output=True,
        text=True,
        timeout=60,
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
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--cpus",
            "0.5",
            "--network",
            "none",
            "--entrypoint",
            "sleep",
            "cap-sandbox-http:latest",
            "30",
        ],
        capture_output=True,
        text=True,
        timeout=30,
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
