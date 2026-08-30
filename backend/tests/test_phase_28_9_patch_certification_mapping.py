"""PATCH-GATE mapping + meta-gate tests for the v1.0.1-rc1 certification tool.

The generator derives every gate status from real evidence. These tests
assert the *tooling* is honest:

* every one of the 20 gates resolves to at least one piece of real evidence
  (a live test, a CI step, a workflow run, or an immutability check) -- a
  gate with no evidence must never be able to report PASS;
* gate numbering matches the restored PATCH specification exactly (no
  renumbering, no re-interpretation);
* GATE 13 is the response/service targeted suite, NOT a deployment-template
  assertion (those belong to GATE 6/7);
* reserved interfaces / AssessmentScheduler are supplemental disclosures and
  are explicitly excluded from the 20 gates;
* the strict meta-gate combines FAIL / NOT_RUN / SKIP / UNKNOWN into a
  non-PASS verdict, so a missing or skipped test can never pass silently.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "scripts" / "certification" / "generate_patch_certification_1_0_1.py"


def _load():
    spec = importlib.util.spec_from_file_location("patchgen", GEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load()


@pytest.fixture(scope="module")
def specs(gen):
    """Resolve the gate mapping ONCE.

    Every resolution spawns a ``pytest --collect-only`` subprocess; doing it
    per-test is both slow and fragile (nested pytest runs), so the mapping is
    collected a single time and shared across the module.
    """
    return gen.build_gate_specs()


@pytest.fixture(scope="module")
def disclosures(gen):
    return {key: _load_and_collect(gen, meta) for key, meta in gen.SUPPLEMENTAL.items()}


def _load_and_collect(gen, meta):
    nodeids = meta["tests"]()
    return nodeids


EXPECTED_DESCRIPTIONS = {
    "1": "Production weak sandbox fails closed",
    "2": "Production approved sandbox passes",
    "3": "Development weak sandbox allowed + warning",
    "4": "Production missing egress enforcement fails closed",
    "5": "Proxy unhealthy -> readiness fails / no direct fallback",
    "6": "Helm production defaults secure",
    "7": "Compose environment semantics explicit",
    "8": "No production silent fallback",
    "9": "Response mock-only disclosed",
    "10": "mock_only=False rejected",
    "11": "Zeek JSONL-only disclosed",
    "12": "Zeek TSV actionable error",
    "13": "response/service targeted tests",
    "14": "OCI targeted security tests",
    "15": "egress targeted security tests",
    "16": "K8s provider targeted tests",
    "17": "General CI PASS",
    "18": "Linux security certification PASS",
    "19": "K8s certification PASS",
    "20": "No v1.0.0 artifact/tag mutation",
}


def test_gate_numbering_matches_the_restored_specification(specs) -> None:
    """Do not renumber and do not re-interpret: 20 gates, exact descriptions."""
    assert sorted(specs, key=int) == [str(i) for i in range(1, 21)]
    for gid, description in EXPECTED_DESCRIPTIONS.items():
        assert specs[gid]["description"] == description, f"GATE {gid} description drifted"


def test_every_gate_has_real_evidence(specs) -> None:
    """A gate with no evidence can never report PASS -- it would be NOT_RUN."""
    for gid in EXPECTED_DESCRIPTIONS:
        spec = specs[gid]
        has_evidence = bool(
            spec.get("tests")
            or spec.get("workflow_steps")
            or spec.get("runs")
            or spec.get("immutability")
        )
        assert has_evidence, f"PATCH-GATE {gid} has no evidence source"


def test_gate13_is_response_service_not_deployment_templates(specs) -> None:
    """GATE 13 = response/service targeted tests; templates belong to 6/7."""
    g13 = specs["13"]
    assert g13["coverage"]["before"] == "47.4%"
    assert g13["coverage"]["after"] == "98%"
    assert all("test_phase_28_8_response_service.py" in n for n in g13["tests"])
    assert g13["tests"], "GATE 13 resolved no tests"

    # deployment-template assertions live in GATE 6 (Helm) and GATE 7 (Compose)
    helm_and_compose = set(specs["6"]["tests"]) | set(specs["7"]["tests"])
    for nodeid in helm_and_compose:
        assert "test_helm_" in nodeid or "test_compose_" in nodeid
    assert not (set(g13["tests"]) & helm_and_compose)


def test_gate6_and_gate7_carry_ci_step_evidence(gen, specs) -> None:
    for gid in ("6", "7"):
        steps = specs[gid]["workflow_steps"]
        assert steps, f"GATE {gid} lost its CI step evidence"
        run_id, job_id, step, _label = steps[0]
        assert run_id == gen.RUN_CI
        assert job_id == gen.JOB_PACKAGING


def test_gates_17_to_19_assert_sha_match_against_the_anchor(gen, specs) -> None:
    expected = {"17": gen.RUN_CI, "18": gen.RUN_LINUX, "19": gen.RUN_K8S}
    for gid, run_id in expected.items():
        runs = specs[gid]["runs"]
        assert runs and runs[0][0] == run_id, f"GATE {gid} run mapping drifted"
    assert gen.CERTIFIED_SHA == "c6793a8ef1fb892120deb15285a19d0373f378d0"


def test_disclosures_are_supplemental_not_patch_gates(gen, specs, disclosures) -> None:
    """Reserved interfaces / AssessmentScheduler: kept, but not counted."""
    assert set(gen.SUPPLEMENTAL) == {"DISCLOSURE-A", "DISCLOSURE-B"}
    gate_tests = {n for spec in specs.values() for n in spec.get("tests", [])}
    for key in gen.SUPPLEMENTAL:
        nodeids = disclosures[key]
        assert nodeids, f"{key} resolved no tests"
        # they are disclosed by tests outside the 20-gate mapping
        assert all(n.startswith("tests/test_phase_28_8_capability_disclosure.py") for n in nodeids)
        assert set(nodeids) - gate_tests, f"{key} tests are already covered by a gate"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["PASS", "PASS"], "PASS"),
        (["PASS", "FAIL"], "FAIL"),
        (["PASS", "NOT_RUN"], "NOT_RUN"),
        (["PASS", "SKIP"], "NOT_RUN"),
        (["PASS", "UNKNOWN"], "NOT_RUN"),
        ([], "NOT_RUN"),
    ],
)
def test_meta_gate_combination(gen, statuses, expected) -> None:
    """SKIP / NOT_RUN / UNKNOWN / FAIL can never yield a PASS verdict."""
    assert gen._combine(statuses) == expected


def test_generator_entrypoint_is_importable_and_has_main() -> None:
    module = _load()
    assert callable(module.main)
    assert module.VERSION == "1.0.1-rc1"
    assert module.OUT_DIR.name == "release"


def test_junit_parser_reports_missing_tests_as_not_run(gen, tmp_path) -> None:
    """A test that did not execute is NOT_RUN -- never assumed PASS."""
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<testsuites><testsuite><testcase classname="tests.x" name="test_a"
        file="tests/x.py"/></testsuite></testsuites>""",
        encoding="utf-8",
    )
    results = gen.parse_junit(junit, ["tests/x.py::test_a", "tests/x.py::test_missing"])
    assert results["tests/x.py::test_a"] == "PASS"
    assert results["tests/x.py::test_missing"] == "NOT_RUN"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
