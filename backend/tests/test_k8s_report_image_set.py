"""The K8s certification artifact reports the image set the cluster ran.

`generate_report_28_6.py` used to write its `images` block from a literal table:

    "backend": "cap-backend:ci", … "sandbox_http": "cap-sandbox-http:latest", …

F-7 changed what the jobs build, the table did not, and nothing compared the two
-- so the artifact of a *passing* Kubernetes round would have described a
deployment that never happened, which is the F-21 class of defect wearing a
different hat: a document that states something no code owns. The table is gone;
K8S-GATE 34 now writes what it observed and the generator copies that, and when
the record is missing the artifact says so instead of remembering.

The script is run as a real process with a real exit code, the way the workflow
runs it, because every defect in this area (a generator that dies on a missing
`kind`, a verdict that survives a missing evidence file) only shows up when it
executes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = PROJECT_ROOT / "scripts" / "certification" / "generate_report_28_6.py"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "cap-k8s-certification.yml"

_spec = importlib.util.spec_from_file_location("cap_report_28_6", GENERATOR)
assert _spec and _spec.loader
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def _junit(out_dir: Path, *, fail_gate: str | None = None) -> None:
    """A junit where every mapped certification test passed."""
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root,
        "testsuite",
        {"name": "k8s", "tests": str(len(report.TEST_GATES)), "failures": "0"},
    )
    for name in report.TEST_GATES:
        case = ET.SubElement(
            suite, "testcase", {"name": name, "classname": "tests.k8s", "time": "1"}
        )
        if fail_gate and report.TEST_GATES[name][0] == fail_gate:
            ET.SubElement(case, "failure", {"message": "assert False"})
    ET.ElementTree(root).write(out_dir / "junit-k8s.xml", encoding="utf-8")


OBSERVED = {
    "observed_by": "K8S-GATE 34",
    "tag": "ci",
    "images": [
        "cap-backend:ci",
        "cap-egress-proxy:ci",
        "cap-sandbox-browser:ci",
        "cap-sandbox-http:ci",
        "cap-frontend:ci",
    ],
    "pod_images": ["cap-backend:ci", "cap-egress-proxy:ci", "cap-frontend:ci"],
    "worker_sandbox_coordinates": ["cap-sandbox-browser:ci", "cap-sandbox-http:ci"],
    "pull_errors": [],
}


def _run(tmp_path: Path, *, strict: str = "1") -> subprocess.CompletedProcess[str]:
    out_dir = tmp_path / "cap-cert"
    out_dir.mkdir(parents=True, exist_ok=True)
    _junit(out_dir)
    env = {**os.environ, "CAP_CERT_OUT": str(out_dir), "CAP_K8S_STRICT": strict}
    return subprocess.run(  # noqa: S603 -- running the product's own script
        [sys.executable, str(GENERATOR)],
        capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT), timeout=300,
    )


def _artifact(tmp_path: Path) -> dict:
    return json.loads(
        (tmp_path / "cap-cert" / "cap-28.6-k8s-certification.json").read_text("utf-8")
    )


def _record(tmp_path: Path, commit: str | None = None) -> Path:
    out = tmp_path / "cap-cert"
    out.mkdir(parents=True, exist_ok=True)
    payload = dict(OBSERVED)
    payload["commit"] = report._commit() if commit is None else commit
    path = out / "k8s-image-set.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_the_artifact_reports_the_observed_image_set(tmp_path: Path) -> None:
    _record(tmp_path)
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    images = _artifact(tmp_path)["images"]
    assert images["source"] == "K8S-GATE 34"
    assert images["images"] == OBSERVED["images"], (
        "the artifact must carry the coordinates the gate read from the cluster"
    )
    assert images["tag"] == "ci"
    assert not [ref for ref in images["images"] if ref.endswith(":latest")]


def test_the_artifact_says_when_nothing_observed_the_images(tmp_path: Path) -> None:
    """No gate-34 record: report absence, never a remembered table."""
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    images = _artifact(tmp_path)["images"]
    assert images["source"] == "not_observed", images
    assert "images" not in images or images.get("images") in (None, []), images
    text = json.dumps(images)
    assert "cap-sandbox-http:latest" not in text and "cap-backend:ci" not in text, (
        f"the artifact reconstructed coordinates from memory: {text}"
    )


def test_a_record_from_another_commit_describes_nothing_here(tmp_path: Path) -> None:
    """Stale evidence in the output directory is the bug this whole file exists for."""
    _record(tmp_path, commit="0" * 40)
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    images = _artifact(tmp_path)["images"]
    assert images["source"] == "commit_mismatch", images
    assert images["recorded_for"] == "0" * 40
    assert "images" not in images, images
    assert "not_observed" not in json.dumps(images)


def test_the_workflow_runs_the_generator_where_the_gate_writes(tmp_path: Path) -> None:
    """`CAP_CERT_OUT` is relative, so both sides' working directory matters.

    The first green round after the record existed still said `not_observed`: the
    certification tests run from `backend/` while the generator runs from the
    repository root, and the gate wrote where *it* thought the output directory
    was. The gate now resolves against the repository root, which only lines up if
    the generator keeps running from there -- so that is asserted, not assumed.
    """
    text = WORKFLOW.read_text("utf-8")
    job = yaml.safe_load(text)["jobs"]["k8s-certification"]
    step = next(
        step for step in job["steps"]
        if "generate_report_28_6.py" in str(step.get("run", ""))
    )
    assert not step.get("working-directory"), (
        "the generator is no longer run from the repository root, so the relative "
        "CAP_CERT_OUT it reads is a different directory from the one gate 34 writes"
    )
    gate = (PROJECT_ROOT / "backend" / "tests" / "test_phase_28_6_k8s_certification.py")
    assert "configured.is_absolute() else REPO_ROOT / configured" in gate.read_text("utf-8"), (
        "gate 34 stopped resolving CAP_CERT_OUT against the repository root"
    )


def test_the_generator_survives_a_machine_without_kind_or_kubectl(tmp_path: Path) -> None:
    """Provenance is recorded as unavailable, not raised through.

    The workflow runs this inside a job that has both tools; the *test* does not,
    and neither does a developer's box. A generator that dies on a missing binary
    writes no artifact at all, so the absence has to become a field value.
    """
    proc = _run(tmp_path)
    assert proc.returncode == 0, (
        f"the generator could not write an artifact without a cluster:\n"
        f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
    )
    kubernetes = _artifact(tmp_path)["kubernetes"]
    assert all(v for v in kubernetes.values()), kubernetes
    assert any("unavailable" in str(v) for v in kubernetes.values()), (
        f"no cluster here, yet the artifact claims: {kubernetes}"
    )


def test_a_failing_gate_still_fails_the_report(tmp_path: Path) -> None:
    """Control: `not_observed` must not have become the forgiving mode."""
    out_dir = tmp_path / "cap-cert"
    out_dir.mkdir(parents=True, exist_ok=True)
    _junit(out_dir, fail_gate="K8S-GATE 34")
    env = {**os.environ, "CAP_CERT_OUT": str(out_dir), "CAP_K8S_STRICT": "1"}
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(GENERATOR)],
        capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT), timeout=300,
    )
    assert proc.returncode == 1, proc.stdout[-1500:]
    assert "K8S-GATE 34" in proc.stdout + proc.stderr


def test_the_certification_job_writes_the_record_it_needs(tmp_path: Path) -> None:
    """The generator reads `k8s-image-set.json`; the round has to keep it in view.

    CAP_CERT_OUT is exported job-wide in this workflow, so gate 34 writes beside
    the junit the generator reads. Asserting that here is what stops the pair
    from drifting apart the way the literal table did.
    """
    text = WORKFLOW.read_text("utf-8")
    assert "CAP_CERT_OUT" in text, "the certification job no longer exports CAP_CERT_OUT"
    assert "generate_report_28_6.py" in text, "the workflow no longer builds the artifact"
    assert "k8s-image-set.json" in Path(
        PROJECT_ROOT / "backend" / "tests" / "test_phase_28_6_k8s_certification.py"
    ).read_text("utf-8"), "gate 34 no longer records the image set it asserted"
