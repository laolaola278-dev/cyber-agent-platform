"""The release-audit summariser must group reality, not invent it.

``scripts/quality/audit_junit.py`` produces the two tables the release report
publishes (skips by reason family, module coverage below a threshold). A tool
that quietly mis-groups evidence is worse than no tool: a security test skipped
because its container runtime is missing would read as a platform skip, and the
headline coverage figure would keep hiding the modules nobody tests.

So the tests here are built on purpose-made JUnit/coverage XML: what each family
must be classified as, what must NOT be swallowed, and what happens when an
outcome has no reason text at all.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "quality" / "audit_junit.py"

_SPEC = importlib.util.spec_from_file_location("cap_audit_junit", str(SCRIPT))
assert _SPEC and _SPEC.loader
audit_junit = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("cap_audit_junit", audit_junit)
_SPEC.loader.exec_module(audit_junit)

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="8">
    <testcase classname="tests.test_ok" name="test_passes" time="0.1"/>
    <testcase classname="tests.test_oci" name="test_needs_runtime" time="0.0">
      <skipped message="unconditional skip: requires a real OCI container runtime"/>
    </testcase>
    <testcase classname="tests.test_pg" name="test_needs_postgres" time="0.0">
      <skipped message="set CAP_PG_TEST=1 + DATABASE_URL for the authoritative run"/>
    </testcase>
    <testcase classname="tests.test_browser" name="test_needs_chromium" time="0.0">
      <skipped message="Playwright chromium not installed"/>
    </testcase>
    <testcase classname="tests.test_adversarial" name="test_ssrf_blocked" time="0.0">
      <skipped message="security suite requires the egress network"/>
    </testcase>
    <testcase classname="tests.test_mystery" name="test_no_reason" time="0.0">
      <skipped/>
    </testcase>
    <testcase classname="tests.test_fail" name="test_breaks" time="0.2">
      <failure message="AssertionError: boom">traceback text</failure>
    </testcase>
    <testcase classname="tests.test_err" name="test_explodes" time="0.2">
      <error message="RuntimeError: fixture died">traceback text</error>
    </testcase>
  </testsuite>
</testsuites>
"""

COVERAGE = """<?xml version="1.0" encoding="utf-8"?>
<coverage line-rate="0.9">
  <packages>
    <package name="app">
      <classes>
        <class name="app.worker.runtime" filename="app/worker/runtime.py">
          <lines>
            <line number="1" hits="1"/><line number="2" hits="1"/>
            <line number="3" hits="0"/><line number="4" hits="1"/>
          </lines>
        </class>
        <class name="app.sandbox.oci" filename="app/sandbox/oci.py">
          <lines>
            <line number="1" hits="0"/><line number="2" hits="0"/>
            <line number="3" hits="0"/><line number="4" hits="0"/>
            <line number="5" hits="0"/><line number="6" hits="0"/>
            <line number="7" hits="0"/><line number="8" hits="0"/>
          </lines>
        </class>
        <class name="app.acquisition.service" filename="app/acquisition/service.py">
          <lines>
            <line number="1" hits="1"/><line number="2" hits="1"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


@pytest.fixture
def junit_path(tmp_path: Path) -> Path:
    path = tmp_path / "junit-backend.xml"
    path.write_text(JUNIT, encoding="utf-8")
    return path


def test_skips_are_grouped_by_what_the_reason_actually_says(junit_path: Path) -> None:
    report = audit_junit.audit_skips(junit_path)
    assert report["skipped_total"] == 5
    families = report["skipped_by_family"]
    assert families["oci_runtime"] == 1
    assert families["postgres"] == 1
    assert families["browser"] == 1
    assert families["other"] == 1, "a skip with no reason text is 'other', never dropped"
    assert report["security_skips"] == ["tests.test_adversarial::test_ssrf_blocked"], (
        "a skipped security test must be named by what it tests, not hidden in the"
        " infrastructure family its excuse mentions"
    )
    assert report["outcomes"]["passed"] == 1


def test_failures_and_errors_are_listed_verbatim(junit_path: Path) -> None:
    report = audit_junit.audit_skips(junit_path)
    by_test = {entry["test"]: entry for entry in report["non_passing"]}
    assert set(by_test) == {"tests.test_fail::test_breaks", "tests.test_err::test_explodes"}
    assert by_test["tests.test_fail::test_breaks"]["reason"].startswith("AssertionError")
    # a failure is never counted as a skip
    assert report["outcomes"]["failure"] == 1
    assert report["outcomes"]["error"] == 1


def test_skips_are_also_counted_per_file(junit_path: Path) -> None:
    report = audit_junit.audit_skips(junit_path)
    assert report["skipped_by_file"]["tests.test_oci"] == 1
    assert sum(report["skipped_by_file"].values()) == report["skipped_total"]


def test_coverage_lists_the_modules_the_average_hides(tmp_path: Path) -> None:
    coverage_path = tmp_path / "coverage.xml"
    coverage_path.write_text(COVERAGE, encoding="utf-8")
    report = audit_junit.low_coverage_modules(coverage_path, threshold=80.0)
    below = {row["module"]: row["percent"] for row in report["modules_below_threshold"]}
    assert below == {"app/sandbox": 0.0, "app/worker": 75.0}, below
    assert report["module_count"] == 3
    # 3/4 + 0/8 + 2/2 = 5 covered of 14 statements
    assert report["overall_percent"] == round(100.0 * 5 / 14, 1)
    assert [row["module"] for row in report["modules_below_threshold"]] == [
        "app/sandbox",
        "app/worker",
    ], "lowest first, so an operator reads the worst module before the headline"


def test_the_tool_writes_both_files_and_exits_zero(junit_path: Path, tmp_path: Path) -> None:
    coverage_path = tmp_path / "coverage.xml"
    coverage_path.write_text(COVERAGE, encoding="utf-8")
    out = tmp_path / "evidence"
    code = audit_junit.main(
        [str(junit_path), "--coverage", str(coverage_path), "--out", str(out)]
    )
    assert code == 0
    skip_report = json.loads((out / "skip-audit.json").read_text(encoding="utf-8"))
    assert skip_report["skipped_total"] == 5
    coverage_report = json.loads((out / "coverage-low-modules.json").read_text(encoding="utf-8"))
    assert coverage_report["modules_below_threshold"][0]["module"] == "app/sandbox"


def test_a_real_certification_junit_is_readable(tmp_path: Path) -> None:
    """The parser must not depend on the fixture's formatting.

    pytest writes one ``<testsuite>`` per invocation and nests the skipped reason
    in a ``message`` attribute; a report generated on another runner also carries
    ``type`` and multi-line bodies. Both shapes are asserted here.
    """
    path = tmp_path / "junit-other.xml"
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3">
  <testcase classname="tests.test_a" name="t1"><skipped type="pytest.skip"
     message="docker-compose is not available
on this runner">long body</skipped></testcase>
  <testcase classname="tests.test_b" name="t2"/>
  <testcase classname="tests.test_c" name="t3"><skipped type="pytest.skip"
     message=""/></testcase>
</testsuite></testsuites>
""",
        encoding="utf-8",
    )
    report = audit_junit.audit_skips(path)
    assert report["skipped_total"] == 2
    assert report["skipped_by_family"]["oci_runtime"] == 1
    assert report["skipped_by_family"]["other"] == 1
    assert report["outcomes"]["passed"] == 1
