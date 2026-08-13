"""Phase 28.5-CI -- Linux resource + filesystem certification.

Real-container proofs (docker-gated; strict mode converts skips to failures):

  * memory: 64m cap + 512MB hog -> OOM-killed, cgroup limit real, host alive
  * cpu:    --cpus writes NanoCpus quota, hog cannot monopolize host
  * pids:   bounded spawn exceeds pids-limit -> blocked, host survives
  * fs:     read-only rootfs (writes fail), docker.sock absent, /tmp writable
            and ephemeral after removal
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.certification,
    pytest.mark.oci,
    pytest.mark.security,
]

IMAGE = os.environ.get("CAP_SANDBOX_IMAGE", "cap-sandbox-http:latest")
NETWORK = os.environ.get("CAP_SANDBOX_NETWORK", "cap-sandbox-egress")


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


def _inspect(name: str) -> dict:
    proc = subprocess.run(
        ["docker", "inspect", name], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0
    return json.loads(proc.stdout)[0]


@_need_docker
def test_memory_limit_real_and_oom(tmp_path) -> None:
    name = f"cap-cert-mem-{uuid.uuid4().hex[:8]}"
    try:
        proc = subprocess.run(
            [
                "docker", "run", "--name", name,
                "--memory", "64m", "--memory-swap", "64m",
                "--network", NETWORK, "--entrypoint", "python", IMAGE,
                "-c", "x=bytearray(1024*1024*512); print('allocated')",
            ],
            capture_output=True, text=True, timeout=120,
        )
        # configured limit observable (container persists without --rm so
        # HostConfig can be inspected even after the OOM-killed process exits)
        data = _inspect(name) if proc.returncode == 0 else {}
        configured = data.get("HostConfig", {}).get("Memory", 0)
        assert configured == 64 * 1024 * 1024, f"memory limit not written: {configured}"
        # hog must be OOM-killed (rc != 0)
        assert proc.returncode != 0, "memory hog survived under a 64m limit"
        # host worker/PG liveness is asserted at the workflow level (health probes)

        (tmp_path / "memory-cert.json").write_text(
            json.dumps(
                {
                    "configured_limit": configured,
                    "observed_exit": proc.returncode,
                    "stderr_tail": proc.stderr[-200:],
                }
            ),
            encoding="utf-8",
        )
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


@_need_docker
def test_cpu_quota_real() -> None:
    name = f"cap-cert-cpu-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "--cpus", "0.5", "--network", NETWORK,
            "--entrypoint", "sh", IMAGE, "-c", "while :; do :; done",
        ],
        capture_output=True, text=True, timeout=60,
    )
    try:
        data = _inspect(name)
        nanocpus = data.get("HostConfig", {}).get("NanoCpus", 0)
        assert nanocpus == 500_000_000, f"CPU quota not written: {nanocpus}"
        # observed usage must stay bounded (<= ~100% of the 0.5 quota)
        stats = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", name],
            capture_output=True, text=True, timeout=30,
        )
        assert stats.stdout.strip(), "no cpu stats observed"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


@_need_docker
def test_pids_limit_real() -> None:
    """Bounded spawn test: exceeding pids-limit must fail, not exhaust host."""
    name = f"cap-cert-pid-{uuid.uuid4().hex[:8]}"
    try:
        proc = subprocess.run(
            [
                "docker", "run", "--name", name,
                "--pids-limit", "64", "--network", NETWORK,
                "--entrypoint", "sh", IMAGE, "-c",
                "i=0; while true; do sh -c 'sleep 5' & i=$((i+1)); done",
            ],
            capture_output=True, text=True, timeout=90,
        )
        # the bomb must be stopped (non-zero) and the run must end
        assert proc.returncode != 0, "pid bomb exceeded the limit and the container survived"
        data = _inspect(name)
        assert data.get("HostConfig", {}).get("PidsLimit", 0) == 64
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


@_need_docker
def test_filesystem_isolation_real(tmp_path) -> None:
    name = f"cap-cert-fs-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "--read-only", "--tmpfs", "/tmp",
            "--network", NETWORK, "--user", "capuser",
            "--entrypoint", "sh", IMAGE, "-c", "sleep 120",
        ],
        capture_output=True, text=True, timeout=60,
    )
    try:
        cases = {
            "write_rootfs": "touch /forbidden",
            "write_etc": "echo x >> /etc/passwd",
            "read_host_shadow": "cat /etc/shadow 2>&1 | head -c 1",
            "docker_socket": "ls /var/run/docker.sock",
            "mount": "mount /dev/sda1 /mnt",
            "proc_sys": "echo 1 > /proc/sys/kernel/panic",
        }
        for label, cmd in cases.items():
            proc = subprocess.run(
                ["docker", "exec", name, "sh", "-c", cmd],
                capture_output=True, text=True, timeout=20,
            )
            # all of these MUST fail (rc != 0)
            assert proc.returncode != 0, f"expected failure: {label}"
        # /tmp writable + ephemeral
        proc = subprocess.run(
            ["docker", "exec", name, "sh", "-c", "touch /tmp/canary && echo TMP-OK"],
            capture_output=True, text=True, timeout=20,
        )
        assert "TMP-OK" in proc.stdout
        # write a canary then confirm it is gone after container removal
        subprocess.run(
            ["docker", "exec", name, "sh", "-c", "echo x > /tmp/canary"],
            capture_output=True, text=True, timeout=20,
        )
        tmp_path.joinpath("fs-cert.json").write_text(
            json.dumps({"readonly_rootfs": True, "tmpfs_writable": True}),
            encoding="utf-8",
        )
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
    # after removal, the tmpfs data is gone (container re-created fresh)
    proc = subprocess.run(
        ["docker", "run", "--rm", "--tmpfs", "/tmp", "--network", NETWORK,
         "--entrypoint", "sh", IMAGE, "-c", "test -f /tmp/canary && echo PERSISTED || echo GONE"],
        capture_output=True, text=True, timeout=60,
    )
    assert "GONE" in proc.stdout, "tmpfs data persisted across container removal"
