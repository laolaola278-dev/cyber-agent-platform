"""The GA report generator's decision modes, executed as the workflows run them.

``cap-ga-reliability.yml`` generates the GA artifact inside the soak job so its
own DR evidence is visible there. Every other gate's test runs in a different
workflow, so in that job 33 implemented gates are ``NOT_RUN`` by construction --
and the generator printed that as ``GA certification FAILED gates: ['GA-GATE 1',
…33 more]`` and exited 1, while the workflow swallowed the code with ``|| true``
(run ``35439341789``'s log is the evidence: a green soak carrying a 33-gate
"FAILED" line that nobody could act on and nobody could disbelieve). That is
F-21's class of defect pointing the other way: a word in the output that does not
own the verdict, in either direction.

So the modes are tested by running the script the way CI does -- a real process,
real exit code, real stdout -- not by asserting about its source:

* preview: absence is reported as absence, exit 0;
* preview: a ``FAIL`` and a silently ``SKIPPED`` certification test are still fatal;
* development: unchanged, ``NOT_RUN`` still fails;
* strict: ``CAP_GA_PREVIEW`` cannot soften the meta-gate.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = PROJECT_ROOT / "scripts" / "certification" / "generate_report_28_7.py"
RELIABILITY_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "cap-ga-reliability.yml"

#: A test name that maps to an implemented gate (GA-GATE 1).
MAPPED_TEST = "test_ga_gate1_baseline_recorded"


def _junit(out_dir: Path, outcome: str) -> None:
    """Write a one-case junit the way pytest would, in the mode under test."""
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root,
        "testsuite",
        {"name": "ga", "tests": "1", "failures": "0", "errors": "0", "skipped": "0"},
    )
    case = ET.SubElement(
        suite, "testcase", {"name": MAPPED_TEST, "classname": "tests.ga", "time": "1"}
    )
    if outcome == "failed":
        ET.SubElement(case, "failure", {"message": "assert False"})
    elif outcome == "skipped":
        ET.SubElement(case, "skipped", {"message": "availability"})
    ET.ElementTree(root).write(out_dir / "junit-ga.xml", encoding="utf-8")


def _run(
    tmp_path: Path,
    extra_env: dict[str, str],
    outcome: str | None = None,
) -> subprocess.CompletedProcess[str]:
    out_dir = tmp_path / "cap-cert-ga"
    out_dir.mkdir(parents=True, exist_ok=True)
    dr_dir = tmp_path / "ga-dr"
    dr_dir.mkdir(parents=True, exist_ok=True)
    # GA-GATE 15/16 are recorded-PASS, so the generator demands the measured
    # RPO/RTO values beside them. Giving it the values keeps every assertion in
    # this file about the decision under test rather than about a fixture gap.
    (dr_dir / "ga-dr-context.json").write_text(
        json.dumps({"rpo": {"observed_rpo_seconds": 4.5}, "rto": {"rto_seconds": 12.0}}),
        encoding="utf-8",
    )
    if outcome is not None:
        _junit(out_dir, outcome)
    env = {
        **os.environ,
        "CAP_GA_OUT": str(out_dir),
        "GA_REPORT_DIR": str(dr_dir),
    }
    env.pop("CAP_GA_STRICT", None)
    env.pop("CAP_GA_PREVIEW", None)
    env.update(extra_env)
    return subprocess.run(  # noqa: S603 -- running the product's own script
        (sys.executable, str(GENERATOR)),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(PROJECT_ROOT),
        timeout=180,
    )


def test_preview_mode_reports_absent_gates_without_claiming_failure(tmp_path: Path) -> None:
    """The soak job's own run: 38 implemented gates, no evidence for most of them."""
    proc = _run(tmp_path, {"CAP_GA_PREVIEW": "1"})
    assert proc.returncode == 0, proc.stderr
    assert "GA preview:" in proc.stdout
    assert "GA certification FAILED gates" not in proc.stderr
    assert "FAILED" not in proc.stdout, "a preview must not print a verdict it does not own"


def test_development_mode_still_fails_on_gates_that_never_ran(tmp_path: Path) -> None:
    """Without the flag, NOT_RUN is a failure -- that is the guard F-17 relies on."""
    proc = _run(tmp_path, {})
    assert proc.returncode == 1
    assert "GA certification FAILED gates" in proc.stderr


def test_preview_mode_is_still_fatal_for_a_failing_certification_test(
    tmp_path: Path,
) -> None:
    proc = _run(tmp_path, {"CAP_GA_PREVIEW": "1"}, outcome="failed")
    assert proc.returncode == 1
    assert "GA certification FAILED gates" in proc.stderr
    assert "GA-GATE 1" in proc.stderr


def test_preview_mode_still_fails_on_a_silently_skipped_certification_test(
    tmp_path: Path,
) -> None:
    """SKIP == FAIL in every mode; the old ``|| true`` hid exactly this."""
    proc = _run(tmp_path, {"CAP_GA_PREVIEW": "1"}, outcome="skipped")
    assert proc.returncode == 1
    assert "GA certification FAILED gates" in proc.stderr


def test_preview_cannot_soften_the_strict_meta_gate(tmp_path: Path) -> None:
    """CAP_GA_STRICT wins: a strict run never gets to call itself a preview."""
    proc = _run(tmp_path, {"CAP_GA_PREVIEW": "1", "CAP_GA_STRICT": "1"})
    assert proc.returncode == 1
    assert "GA preview:" not in proc.stdout, (
        "preview mode softened a strict run -- FULL GA could be reported "
        "certified on gates that were never executed"
    )
    assert "FAILED gates" in proc.stderr, "the strict run refused, but not visibly"


def test_the_reliability_workflow_previews_instead_of_swallowing() -> None:
    """The workflow must use the mode it is claiming, and keep the exit code."""
    doc = yaml.safe_load(RELIABILITY_WORKFLOW.read_text("utf-8"))
    steps = [
        step
        for job in doc["jobs"].values()
        for step in job.get("steps", []) or []
        if "generate_report_28_7" in str(step.get("run", ""))
    ]
    assert steps, "the reliability workflow no longer generates the GA artifact at all"
    for step in steps:
        assert (step.get("env") or {}).get("CAP_GA_PREVIEW") == "1", (
            "the reliability job runs the generator but does not declare preview "
            "mode, so its log reports absence as failure again"
        )
        command = next(
            line for line in step["run"].splitlines() if "generate_report_28_7" in line
        )
        assert "|| true" not in command, (
            "discarding the generator's exit code hides a FAIL and a skipped "
            "certification test behind a green job"
        )


def _generator_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("generate_report_28_7", str(GENERATOR))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_redirected_output_dir_never_reads_the_repository_leftovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legacy fallback is for the default location, not for a redirected one.

    With ``CAP_GA_OUT`` pointing elsewhere, falling back to the repository's own
    ``outputs/cap-cert-ga/junit-ga.xml`` reported 28 gates PASS in a run that had
    executed nothing -- a stale file certifying a fresh report.
    """
    module = _generator_module()
    empty = tmp_path / "out"
    empty.mkdir()
    stale_repo = tmp_path / "repo"
    (stale_repo / "outputs" / "cap-cert-ga").mkdir(parents=True)
    _junit(stale_repo / "outputs" / "cap-cert-ga", "passed")

    monkeypatch.setattr(module, "OUT_DIR", empty)
    monkeypatch.setattr(module, "REPO_ROOT", stale_repo)

    monkeypatch.setenv("CAP_GA_OUT", str(empty))
    assert module._parse_junit() == {}, (
        "a redirected run read the repository's leftover JUnit as its own evidence"
    )
    monkeypatch.delenv("CAP_GA_OUT")
    assert module._parse_junit() == {MAPPED_TEST: "passed"}, (
        "the default dev location lost the fallback it exists for"
    )


TIER2_FILES = {
    "soak-context.json": "GA-GATE 24",
    "capacity.json": "GA-GATE 27",
    "backpressure.json": "GA-GATE 28",
}


def test_evidence_outside_the_checkout_is_reported_not_crashed_on(tmp_path: Path) -> None:
    """``GA_REPORT_DIR`` may point anywhere; the report must still be written.

    ``relative_to(REPO_ROOT)`` raised ``ValueError`` the moment a measured DR file
    lived outside the checkout -- after the artifact JSON was already written, so
    the run died with a traceback over the cosmetic form of a source path.
    """
    dr_dir = tmp_path / "ga-dr"
    dr_dir.mkdir(parents=True, exist_ok=True)
    for name, gate in TIER2_FILES.items():
        (dr_dir / name).write_text(json.dumps({"gate": gate, "observed": 1}), encoding="utf-8")
    proc = _run(tmp_path, {"CAP_GA_PREVIEW": "1"})
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr and "ValueError" not in proc.stderr
    payload = json.loads(
        (tmp_path / "cap-cert-ga" / "cap-28.7-ga-certification.json").read_text("utf-8")
    )
    source = payload["soak"]["evidence_source"]
    assert Path(source).is_absolute() and Path(source).exists(), (
        f"evidence outside the repository lost its location: {source!r}"
    )


def test_preview_mode_is_documented_where_it_is_used() -> None:
    """Three modes, and the module docstring that a reader trusts lists all three."""
    docstring = GENERATOR.read_text("utf-8").split('"""')[1]
    assert "CAP_GA_PREVIEW" in docstring
    assert "preview" in docstring.lower()
