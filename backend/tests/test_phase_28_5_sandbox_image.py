"""Phase 28.5 -- sandbox image definition tests.

Static checks run ALWAYS (Dockerfile content): minimal, pinned, non-root,
no socket, no privileged mode, no host mounts. Build + runtime-inspect checks
are gated on a live docker daemon.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.certification, pytest.mark.oci]

BACKEND = Path(__file__).resolve().parent.parent
HTTP_DF = BACKEND / "docker" / "sandbox-http" / "Dockerfile"
BROWSER_DF = BACKEND / "docker" / "sandbox-browser" / "Dockerfile"


def _read(df: Path) -> str:
    assert df.exists(), f"missing Dockerfile: {df}"
    return df.read_text(encoding="utf-8")


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


# -- static checks (always run) ----------------------------------------------


def test_http_dockerfile_is_minimal_and_safe() -> None:
    content = _read(HTTP_DF).lower()
    # pinned base
    assert "from python:" in content
    assert "@sha256" in content or "3.13.12-slim-bookworm" in content, "base not pinned"
    # non-root
    assert "user" in content and "capuser" in content
    assert "root" not in content.split("user capuser")[1].split("entrypoint")[0]
    # no privileged / no socket / no host mounts
    assert "--privileged" not in content
    assert "docker.sock" not in content
    assert " -v /" not in content and "volume" not in content
    # entrypoint is the shim
    assert "sandbox.shim" in content


def test_browser_dockerfile_is_minimal_and_safe() -> None:
    content = _read(BROWSER_DF).lower()
    assert "from cap-sandbox-http" in content
    assert "playwright" in content
    assert "chromium" in content
    assert "--privileged" not in content
    assert "docker.sock" not in content
    assert "user capuser" in content


def test_shim_files_are_self_contained() -> None:
    """The image carries ONLY the protocol + shim, no worker code."""
    from app.sandbox import oci_protocol as proto

    # the shim must not import app.* at module level (except the fallback)
    shim = (BACKEND / "app" / "sandbox" / "oci_shim.py").read_text(encoding="utf-8")
    assert "import app." not in shim or "app.sandbox.oci_protocol" in shim
    assert "sqlalchemy" not in shim
    assert "async_sessionmaker" not in shim
    assert "AcquisitionService" not in shim
    # protocol has no worker imports
    proto_src = (BACKEND / "app" / "sandbox" / "oci_protocol.py").read_text(encoding="utf-8")
    assert "sqlalchemy" not in proto_src


# -- build + runtime checks (docker-gated) -----------------------------------


@_need_docker
def test_build_http_image_and_inspect() -> None:
    """Build the image, verify non-root user, and run the shim with a request."""
    import json
    import tempfile

    from app.sandbox.oci_protocol import SandboxRequest

    from uuid import uuid4

    build_dir = HTTP_DF.parent
    # copy the protocol + shim into the build context (mirrors CI)
    with tempfile.TemporaryDirectory() as ctx:
        ctxp = Path(ctx)
        (ctxp / "sandbox").mkdir()
        (ctxp / "sandbox" / "__init__.py").write_text("", encoding="utf-8")
        (ctxp / "sandbox" / "oci_protocol.py").write_text(
            (BACKEND / "app" / "sandbox" / "oci_protocol.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (ctxp / "sandbox" / "oci_shim.py").write_text(
            (BACKEND / "app" / "sandbox" / "oci_shim.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        tag = f"cap-sandbox-http-test:{uuid4().hex[:8]}"
        build = subprocess.run(
            ["docker", "build", "-t", tag, "-f", str(HTTP_DF), str(ctxp)],
            capture_output=True, text=True, timeout=600,
        )
        assert build.returncode == 0, build.stderr[-1000:]

    # inspect: non-root user + no privileged
    inspect = subprocess.run(
        ["docker", "image", "inspect", tag],
        capture_output=True, text=True, timeout=30,
    )
    assert inspect.returncode == 0
    data = json.loads(inspect.stdout)[0]
    assert data["Config"].get("User") == "capuser"
    # run the shim with a private target -> shim L7 blocks it
    request = SandboxRequest(
        operation="http_fetch",
        run_id=str(uuid4()),
        sandbox_execution_id=str(uuid4()),
        url="http://127.0.0.1:9/x",
    ).model_dump_json()
    run = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--user", "capuser", tag,
        ],
        input=request, capture_output=True, text=True, timeout=60,
    )
    assert run.returncode == 0, run.stderr[-500:]
    assert '"status":"ok"' in run.stdout
    assert "SSRF_BLOCKED" in run.stdout
    subprocess.run(["docker", "rmi", tag], capture_output=True, timeout=30)


@_need_docker
def test_container_enforces_read_only_rootfs_and_tmpfs() -> None:
    from uuid import uuid4

    from app.sandbox.oci_protocol import SandboxRequest

    request = SandboxRequest(
        operation="http_fetch",
        run_id=str(uuid4()),
        sandbox_execution_id=str(uuid4()),
        url="http://127.0.0.1:9/x",
    ).model_dump_json()
    # read-only rootfs + tmpfs /tmp: writes to / should fail, /tmp should work
    run = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--read-only", "--tmpfs", "/tmp",
            "cap-sandbox-http:latest",
            "sh", "-c", "touch /tmp/ok && (touch /write-test 2>/dev/null && echo WRITABLE || echo RO-OK)",
        ],
        capture_output=True, text=True, timeout=60,
    )
    # --read-only forces RO on the rootfs: the touch must fail
    assert "RO-OK" in run.stdout, f"rootfs should be read-only: {run.stdout} {run.stderr}"
