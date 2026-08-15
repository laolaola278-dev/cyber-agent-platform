"""Phase 28.5-RC -- certification artifact truthfulness (docker.sock).

The machine-readable certification JSON must NOT falsely report worker-to-host
control-plane isolation as PASS / boolean-true when the production worker
mounts the container runtime control socket. These are pure sync tests.
"""

from __future__ import annotations

import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_GEN = _REPO_ROOT / "scripts" / "certification" / "generate_report.py"


def _load_gen():
    spec = importlib.util.spec_from_file_location("cert_gen", _GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compose_declares_worker_control_socket() -> None:
    gen = _load_gen()
    assert gen._worker_mounts_control_socket() is True, (
        "docker-compose.yml declares /var/run/docker.sock on the acquisition "
        "worker; the truthfulness detector must see it"
    )


def test_worker_control_plane_is_not_certified() -> None:
    gen = _load_gen()
    ctx = gen.docker_socket_control_plane()
    assert ctx["worker_control_plane_isolation"] == "NOT_CERTIFIED"
    assert ctx["unrestricted_docker_socket_mounted"] is True


def test_isolation_is_multi_state_not_boolean() -> None:
    gen = _load_gen()
    ctx = gen.docker_socket_control_plane()
    # PASS / NOT_CERTIFIED / PARTIAL are the enum; a boolean true/false here is
    # the exact false claim this phase must eliminate.
    assert ctx["worker_control_plane_isolation"] in ("PASS", "NOT_CERTIFIED", "PARTIAL")


def test_json_and_human_report_are_consistent(tmp_path, monkeypatch) -> None:
    # CAP_CERT_OUT is read at module import time (module-level OUT_DIR), so it
    # must be set BEFORE the module is loaded.
    out = tmp_path / "outputs" / "cap-cert"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAP_CERT_OUT", str(out))
    monkeypatch.setenv("CAP_CERT_SECRET_SENTINEL", "")

    gen = _load_gen()

    # write a junit where every required gate's test passes
    suite = ET.Element("testsuite", {"name": "pytest", "tests": "12"})
    for _gate, (module, sub) in gen.GATE_TESTS.items():
        ET.SubElement(
            suite,
            "testcase",
            {"classname": f"tests.{module}", "name": f"{sub}_certified", "time": "1.0"},
        )
    (out / "junit-security.xml").write_text(
        ET.tostring(suite, encoding="unicode"), encoding="utf-8"
    )

    rc = gen.main()
    assert rc == 0, "all gates pass -> main() should exit 0"

    data = json.loads((out / "cap-28.5-linux-certification.json").read_text(encoding="utf-8"))
    # sandbox workload isolation is certified by the 12 gates
    assert data["sandbox_workload_isolation"] == "PASS"
    # worker-to-host control-plane isolation is NOT certified (docker.sock)
    assert data["worker_control_plane_isolation"] == "NOT_CERTIFIED"
    assert data["unrestricted_docker_socket_mounted"] is True

    report = (out / "CAP Phase 28.5-L Linux Runtime Certification Report.md").read_text(
        encoding="utf-8"
    )
    assert "worker_control_plane_isolation: NOT_CERTIFIED" in report
    # meta-test: human report and JSON must agree (a report saying NOT_CERTIFIED
    # while the JSON says PASS would be an inconsistency).
    assert "sandbox_workload_isolation: PASS" in report
