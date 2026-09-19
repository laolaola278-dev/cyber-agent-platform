"""The container-runtime socket boundary, per deployment path, cannot drift silently.

Phase 28.6 removed the runtime control socket from the *production* worker; the
compose worker still mounts ``/var/run/docker.sock`` because its ``oci-sandbox``
provider starts sandboxes through the docker CLI. Both facts are true, they are not
interchangeable, and until now nothing enforced the difference:
``scripts/certification/generate_report.py`` substring-grepped compose and
``.env.example`` and printed one PASS/NOT_CERTIFIED word, and
``docs/known-issues.md`` said the worker "never mounts a container-runtime socket",
which is false on the path an operator reaches first from the README quickstart.

So these tests bind the three things that must agree: the chart's mount lines, the
compose worker's volumes, and the sentences that describe them.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = PROJECT_ROOT / "docker-compose.yml"
WORKER_SERVICE = "acquisition-worker"
SOCKETS = ("docker.sock", "podman.sock", "containerd.sock", "crio.sock")

GENERATE_REPORT = PROJECT_ROOT / "scripts" / "certification" / "generate_report.py"

_SPEC = importlib.util.spec_from_file_location(
    "cap_generate_report", str(GENERATE_REPORT)
)
generate_report = importlib.util.module_from_spec(_SPEC)
sys.modules["cap_generate_report"] = generate_report
_SPEC.loader.exec_module(generate_report)


def _mount_lines(text: str) -> list[str]:
    """Kubernetes manifest lines that declare a mount, ignoring prose.

    The template is Helm YAML, so it is not safe to parse with a YAML loader; a
    mount cannot be expressed on a comment line, and the detector that treated
    comments as mounts could have its security verdict flipped by a docstring.
    """
    tokens = ("mountPath", "hostPath", "persistentVolumeClaim", "emptyDir", "volumeMounts")
    return [
        line
        for line in text.splitlines()
        if not line.lstrip().startswith("#") and any(token in line for token in tokens)
    ]


def _compose_worker_volumes() -> list[str]:
    document = yaml.safe_load(COMPOSE.read_text("utf-8")) or {}
    service = (document.get("services") or {}).get(WORKER_SERVICE) or {}
    return [str(entry) for entry in service.get("volumes") or []]


def test_production_chart_mounts_no_container_runtime_socket() -> None:
    templates = sorted((PROJECT_ROOT / "deployment" / "helm" / "cap" / "templates").glob("*.yaml"))
    assert templates, "no chart templates found -- the scan would pass vacuously"
    offenders: list[str] = []
    for template in templates:
        mounts = _mount_lines(template.read_text("utf-8"))
        offenders.extend(
            f"{template.name}: {line.strip()}"
            for line in mounts
            if any(socket in line for socket in SOCKETS)
        )
    assert not offenders, (
        "the production chart mounts a container-runtime control socket, which makes "
        "a worker that fetches untrusted content host-root-equivalent: "
        + "; ".join(offenders)
    )


def test_compose_worker_socket_and_its_documentation_agree() -> None:
    """Whichever way the mount goes, the prose must say so.

    Both directions are asserted: an undocumented mount is a hidden privilege
    grant, and a document that still promises one after it was removed is a lie
    about the deployment an operator is reading.
    """
    mounted = any(
        any(socket in volume for socket in SOCKETS) for volume in _compose_worker_volumes()
    )
    known_issues = (PROJECT_ROOT / "docs" / "known-issues.md").read_text("utf-8")
    env_example = (PROJECT_ROOT / ".env.example").read_text("utf-8")
    compose_text = COMPOSE.read_text("utf-8")

    if mounted:
        assert "host-root-equivalent" in known_issues, (
            "the compose worker mounts the runtime socket but known-issues.md does not "
            "state the consequence"
        )
        assert "host-root-equivalent" in env_example
        assert "host-root" in compose_text, (
            "the mount needs a warning where the mount is declared, not only in the docs"
        )
    else:
        assert "mounts `/var/run/docker.sock` into `acquisition-worker`" not in known_issues, (
            "the mount is gone; docs/known-issues.md still tells operators it exists"
        )


def test_certification_artifact_reports_both_paths_separately() -> None:
    """One PASS word over two different deployments is how a false claim survives."""
    payload = generate_report.docker_socket_control_plane()
    chart = payload["production_chart_worker_mounts_runtime_socket"]
    compose = payload["compose_worker_mounts_runtime_socket"]
    assert chart is False, "the production chart must mount no control socket"
    assert compose is True, (
        "compose still mounts the runtime socket; update the docs, this test and "
        "the report line together -- the verdict below depends on it"
    )
    # The enum is the point: the production path being clean does not buy a PASS
    # while a shipped path can reach the host container runtime.
    assert payload["worker_control_plane_isolation"] == "PARTIAL"
    assert payload["unrestricted_docker_socket_mounted"] is True
    assert "compose_worker_mounts_runtime_socket" in payload
    assert "Compose is the evaluation/single-node path" in str(
        payload["compose_control_socket_scope"]
    ) or "no longer mounts" in str(payload["compose_control_socket_scope"])
    # The human report must not be able to print the headline without the path.
    source = (PROJECT_ROOT / "scripts" / "certification" / "generate_report.py").read_text("utf-8")
    assert "production chart worker mounts a runtime control socket" in source, (
        "the report line lost the path it describes"
    )
    assert "compose worker (evaluation path)" in source, "the report dropped the compose path"


def test_detector_is_not_a_substring_grep() -> None:
    """Negative controls for the detector the certification artifact depends on.

    A comment mentioning the socket must not change a security verdict -- that was
    the previous behaviour, and `.env.example` had already learned to hide from it
    by containing only prose about removal.
    """
    assert generate_report._mounts_socket(["- /var/run/docker.sock:/var/run/docker.sock"])
    assert not generate_report._mounts_socket([])
    assert not generate_report._mounts_socket(["- /var/lib/cap:/data"])
    chart_lines = _mount_lines(
        "# docker.sock mounting was removed in Phase 28.6\n"
        "kind: Pod\nspec:\n  containers:\n    - volumeMounts:\n        - name: tmp\n"
    )
    assert chart_lines == ["kind: Pod"] or all(
        "docker.sock" not in line for line in chart_lines
    ), chart_lines
    assert not any("docker.sock" in line for line in chart_lines)


def test_env_example_does_not_claim_the_impossible() -> None:
    text = (PROJECT_ROOT / ".env.example").read_text("utf-8")
    assert "The worker does NOT mount" not in text, (
        "the unqualified claim is back: it is true for kubernetes-sandbox and false "
        "for the oci-sandbox provider this same file configures"
    )
    assert "kubernetes-sandbox" in text and "oci-sandbox" in text

def test_the_detector_needs_no_third_party_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """The certification job calls generate_report.py with a bare interpreter.

    A first version of the compose detector imported PyYAML, which exists in the
    backend venv -- so the tests passed while the full-certification layer died with
    ``ModuleNotFoundError: No module named 'yaml'``. A gate whose answer depends on
    which python happens to have a package installed is not a gate.
    """
    import importlib.util
    import sys

    for name in list(sys.modules):
        if name == "yaml" or name.startswith("yaml."):
            monkeypatch.setitem(sys.modules, name, None)  # type: ignore[arg-type]
    monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` now raises

    spec = importlib.util.spec_from_file_location(
        "cap_generate_report_without_yaml", GENERATE_REPORT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.compose_worker_mounts_control_socket() is True
    assert module.chart_worker_mounts_control_socket() is False
    assert module.docker_socket_control_plane()["worker_control_plane_isolation"] == "PARTIAL"
