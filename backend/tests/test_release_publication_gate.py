"""Contract tests for the release publication gate (``release.yml``).

F-21: ``cap-linux-certification.yml`` used to carry the comment

    Production releases MUST depend on cap-production-certification
    (the release.yml workflow includes this file via workflow_call and lists it
    as a required job before publishing artifacts).

Neither half was true -- ``release.yml`` calls only ``ci.yml``, and no job in any
workflow required a certification run. The release pipeline would have built
images and created a GitHub Release for a commit that had never been certified,
while the documentation described a gate that did not exist. The gate now exists
as ``verify-certification``, and this file is what keeps it honest:

  * every job that publishes something must wait for the gate, checked through
    the real ``needs`` graph rather than by reading the file;
  * the gate's inline Python is *executed* here against canned Actions-API
    answers -- inline workflow code otherwise costs a whole release to discover a
    NameError, and its interesting behaviour is entirely which evidence it
    accepts;
  * evidence that is green-but-not-release-scoped, from an uncleasable distance,
    from a failed run, or absent must all refuse the release;
  * an API error must raise, never be misread as "uncertified, carry on";
  * the prose in ``cap-linux-certification.yml`` may not claim a mechanism
    ``release.yml`` does not implement, in either direction.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = PROJECT_ROOT / ".github" / "workflows"
RELEASE_YML = WORKFLOW_DIR / "release.yml"

GATE_JOB = "verify-certification"
GATE_STEP = "Require green certification for this commit or an inheritable ancestor"
GATE_HEREDOC_OPEN = "python3 - <<'RELEASE_GATE_PY'\n"
GATE_HEREDOC_CLOSE = "\nRELEASE_GATE_PY"
EVIDENCE_FILE = Path("outputs/cap-cert-release/release-certification-gate.json")

#: The job set the gate must accept as release evidence, per workflow. Compared
#: against the gate's own ``REQUIRED`` in the evidence file so they cannot drift.
RELEASE_JOBS = {
    "cap-linux-certification.yml": ["cap-production-certification", "postgres-version-matrix"],
    "cap-k8s-certification.yml": ["k8s-certification"],
    "cap-ga-certification.yml": ["ga-certification", "supply-chain"],
    "cap-ga-reliability.yml": ["reliability"],
}

PG_LEGS = ["15-alpine", "16-alpine", "17-alpine"]
SHA_TAG = "a" * 40
SHA_CERTIFIED = "b" * 40
CHAIN = [SHA_TAG, SHA_CERTIFIED, "c" * 40]

INHERITED = json.dumps(
    {"inheritance": "INHERITED", "runtime_affecting": False, "files": []}
)


def _release_doc() -> dict:
    doc = yaml.safe_load(RELEASE_YML.read_text("utf-8"))
    assert doc and "jobs" in doc, "release.yml has no jobs"
    return doc


def _gate_source() -> str:
    steps = _release_doc()["jobs"][GATE_JOB]["steps"]
    run = next(step["run"] for step in steps if step.get("name") == GATE_STEP)
    start = run.index(GATE_HEREDOC_OPEN) + len(GATE_HEREDOC_OPEN)
    source = run[start : run.index(GATE_HEREDOC_CLOSE)]
    assert source.startswith("import "), f"lifted the wrong block: {source[:40]!r}"
    return source


def _runs_payload(entries: list[tuple[str, int, str]]) -> str:
    """``entries`` is (head_sha, run_id, conclusion), as the runs endpoint answers."""
    return json.dumps(
        {
            "workflow_runs": [
                {
                    "id": run_id,
                    "head_sha": sha,
                    "conclusion": conclusion,
                    "status": "completed",
                    "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
                }
                for sha, run_id, conclusion in entries
            ]
        }
    )


def _jobs_payload(names: list[str]) -> str:
    """One entry per job, with matrix legs named the way Actions names them."""
    jobs = []
    for name in names:
        if name == "postgres-version-matrix":
            jobs += [
                {"name": f"{name} ({leg})", "conclusion": "success"} for leg in PG_LEGS
            ]
        else:
            jobs.append({"name": name, "conclusion": "success"})
    return json.dumps({"jobs": jobs})


def _green_runs(sha: str) -> tuple[dict[str, str], dict[int, str]]:
    """One completed, successful, release-scoped run per workflow at ``sha``."""
    runs, jobs = {}, {}
    for index, (workflow, names) in enumerate(RELEASE_JOBS.items()):
        run_id = 100 + index
        runs[workflow] = _runs_payload([(sha, run_id, "success")])
        jobs[run_id] = _jobs_payload(names)
    return runs, jobs


class FakeActionsApi:
    """Answers the gate's ``gh api`` / ``git`` / classifier calls from fixtures."""

    def __init__(
        self,
        *,
        runs: dict[str, str] | None = None,
        jobs: dict[int, str] | None = None,
        classify: tuple[int, str] = (0, INHERITED),
        chain: list[str] | None = None,
        gh_fails: bool = False,
    ) -> None:
        self.runs = runs or {}
        self.jobs = jobs or {}
        self.classify = classify
        self.chain = chain if chain is not None else CHAIN
        self.gh_fails = gh_fails
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, **_kwargs):  # noqa: ANN001, ANN003 -- duck-typed CompletedProcess
        argv = tuple(str(part) for part in argv)
        self.calls.append(argv)
        result = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ("gh", "api"):
            return self._gh(argv[2], result)
        if argv[0] == "git":
            result.stdout = "\n".join(self.chain) + "\n"
            return result
        if "classify_diff.py" in " ".join(argv):
            result.returncode, result.stdout = self.classify
            return result
        result.returncode = 1
        result.stderr = f"unexpected command: {argv}"
        return result

    def _gh(self, path: str, result):  # noqa: ANN001
        if self.gh_fails:
            result.returncode = 1
            result.stderr = "HTTP 401: Requires authentication"
            return result
        for workflow, payload in self.runs.items():
            if f"actions/workflows/{workflow}/runs" in path:
                result.stdout = payload
                return result
        for run_id, payload in self.jobs.items():
            if f"actions/runs/{run_id}/jobs" in path:
                result.stdout = payload
                return result
        result.returncode = 1
        result.stderr = f"no fixture answers {path}"
        return result

    def installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The gate does `import subprocess`, which reads sys.modules first, so
        # this is what actually stands between the gate and the network.
        monkeypatch.setitem(sys.modules, "subprocess", types.SimpleNamespace(run=self.run))


def _exec_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api: FakeActionsApi
) -> tuple[int, dict | None]:
    """Run the gate's own code in ``tmp_path``; return (exit code, evidence)."""
    monkeypatch.setenv("CERT_TAG_SHA", SHA_TAG)
    monkeypatch.setenv("CERT_REPOSITORY", "octo/repo")
    monkeypatch.setenv("CERT_VERSION", "1.0.6-rc1")
    monkeypatch.setenv("GITHUB_REF_NAME", "v1.0.6-rc1")
    monkeypatch.chdir(tmp_path)
    api.installed(monkeypatch)
    code = 0
    try:
        exec(  # noqa: S102 -- executing the product's own release gate
            compile(_gate_source(), "<release certification gate>", "exec"),
            {"__name__": "cap_release_certification_gate"},
        )
    except SystemExit as exit_info:  # noqa: PERF203 -- single handler, not a loop
        code = exit_info.code if isinstance(exit_info.code, int) else 1
    written = tmp_path / EVIDENCE_FILE
    evidence = json.loads(written.read_text("utf-8")) if written.exists() else None
    return code, evidence


def test_gate_job_exists_and_is_wired_into_the_publication_path() -> None:
    doc = _release_doc()
    assert GATE_JOB in doc["jobs"], "release.yml has no certification gate at all"

    def ancestors(job: str, seen: set[str] | None = None) -> set[str]:
        seen = seen if seen is not None else set()
        needs = doc["jobs"][job].get("needs") or []
        for parent in ([needs] if isinstance(needs, str) else needs):
            if parent not in seen:
                seen.add(parent)
                ancestors(parent, seen)
        return seen

    publishers = {
        "release-images": "builds and PUSHES the container images",
        "release-chart": "packages the chart that becomes a release asset",
        "publish-release": "creates the GitHub Release",
    }
    for job, what in publishers.items():
        assert job in doc["jobs"], f"{job} disappeared from release.yml"
        assert GATE_JOB in ancestors(job), (
            f"{job} ({what}) can run without {GATE_JOB}: the certification gate "
            "no longer gates publication"
        )


def test_gate_reads_release_history_and_holds_the_read_scope_it_needs() -> None:
    """``fetch-depth: 0`` and ``actions: read`` are both load-bearing."""
    doc = _release_doc()
    job = doc["jobs"][GATE_JOB]
    assert job["steps"][0]["with"]["fetch-depth"] == 0, (
        "the ancestor walk needs history: with a depth-1 checkout every "
        "certification run belongs to a commit that is not in the repository, and "
        "the gate fails a release that was legitimately certified"
    )
    scopes = {**(doc.get("permissions") or {}), **(job.get("permissions") or {})}
    assert scopes.get("actions") == "read", (
        "listing other workflows' runs is an Actions API read, and once "
        "`permissions` is declared the undeclared scopes become `none`"
    )


def test_gate_accepts_evidence_for_the_tagged_commit_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs, jobs = _green_runs(SHA_TAG)
    code, evidence = _exec_gate(
        tmp_path, monkeypatch, FakeActionsApi(runs=runs, jobs=jobs, chain=[SHA_TAG])
    )
    assert code == 0
    assert evidence["verdict"] == "PASS"
    assert evidence["tag_sha"] == SHA_TAG
    assert evidence["version"] == "1.0.6-rc1"
    assert evidence["required"] == RELEASE_JOBS, (
        "the gate's required job set and this test's copy drifted apart"
    )
    for workflow, record in evidence["evidence"].items():
        assert record["distance"] == 0, f"{workflow} resolved to the wrong commit"
        assert record["run_url"], "the evidence must name the run it came from"
    assert all("diff_verdict" not in record for record in evidence["evidence"].values()), (
        "nothing was inherited, so nothing should have been classified"
    )


def test_gate_inherits_certification_from_an_ancestor_only_when_the_diff_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs, jobs = _green_runs(SHA_CERTIFIED)
    api = FakeActionsApi(runs=runs, jobs=jobs)
    code, evidence = _exec_gate(tmp_path, monkeypatch, api)
    assert code == 0
    for record in evidence["evidence"].values():
        assert record["sha"] == SHA_CERTIFIED
        assert record["distance"] == 1
        assert record["diff_verdict"] == "INHERITED"
    classified = [call for call in api.calls if "classify_diff.py" in " ".join(call)]
    assert classified, "an inherited certification was accepted without proving it"
    argv = classified[0]
    assert argv[0] == sys.executable
    assert argv[1:] == ("scripts/release/classify_diff.py", SHA_CERTIFIED, SHA_TAG), (
        "the gate must classify certified -> tag, never the other way round"
    )


def test_gate_refuses_a_green_run_that_did_not_execute_the_release_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The PR layer of the Linux workflow is green too -- and is not evidence.

    ``cap-production-certification`` runs on a tag push or a ``layer: release``
    dispatch; on other triggers the workflow still finishes successfully with
    only ``fast-certification`` executed. Accepting the run's colour would
    certify a release from a run that never ran a release gate.
    """
    runs, jobs = _green_runs(SHA_TAG)
    runs["cap-linux-certification.yml"] = _runs_payload([(SHA_TAG, 7, "success")])
    jobs[7] = _jobs_payload(["fast-certification"])
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(runs=runs, jobs=jobs, chain=[SHA_TAG]),
    )
    assert code == 1
    assert evidence["verdict"] == "FAIL"
    assert "cap-linux-certification.yml" not in evidence["evidence"]
    assert any("cap-linux-certification.yml" in reason for reason in evidence["failures"])


def test_gate_refuses_a_runtime_affecting_distance_between_evidence_and_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs, jobs = _green_runs(SHA_CERTIFIED)
    blocking = json.dumps(
        {
            "inheritance": "RECERTIFICATION_REQUIRED",
            "runtime_affecting": True,
            "files": [
                {
                    "path": "backend/app/worker/runtime.py",
                    "category": "production_runtime",
                    "runtime_affecting": True,
                },
                {"path": "docs/known-issues.md", "category": "docs", "runtime_affecting": False},
            ],
        }
    )
    code, evidence = _exec_gate(
        tmp_path, monkeypatch, FakeActionsApi(runs=runs, jobs=jobs, classify=(2, blocking))
    )
    assert code == 1
    assert evidence["verdict"] == "FAIL"
    reasons = " ".join(evidence["failures"])
    assert "RECERTIFICATION_REQUIRED" in reasons
    assert "backend/app/worker/runtime.py" in reasons, (
        "the refusal has to name what broke the inheritance"
    )
    assert "docs/known-issues.md" not in reasons, "only runtime-affecting files block"


def test_gate_refuses_when_no_certification_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, evidence = _exec_gate(
        tmp_path, monkeypatch, FakeActionsApi(runs={w: _runs_payload([]) for w in RELEASE_JOBS})
    )
    assert code == 1
    assert evidence["evidence"] == {}
    assert len(evidence["failures"]) == len(RELEASE_JOBS)


def test_gate_ignores_runs_that_did_not_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A red release certification and a cancelled one are both not evidence."""
    runs, jobs = _green_runs(SHA_TAG)
    runs = {
        workflow: _runs_payload([(SHA_TAG, 11, "failure"), (SHA_TAG, 12, "cancelled")])
        for workflow in RELEASE_JOBS
    }
    code, evidence = _exec_gate(
        tmp_path, monkeypatch, FakeActionsApi(runs=runs, jobs=jobs, chain=[SHA_TAG])
    )
    assert code == 1
    assert evidence["evidence"] == {}


def test_api_failure_raises_instead_of_looking_like_absent_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 401 must not be reported as "this commit is uncertified".

    Both refuse the release, but only the truthful one tells the operator what
    happened -- and a gate that could not see the evidence has not earned the
    right to be believed about its absence.
    """
    with pytest.raises(RuntimeError, match="gh api"):
        _exec_gate(tmp_path, monkeypatch, FakeActionsApi(gh_fails=True))
    assert not (tmp_path / EVIDENCE_FILE).exists()


def test_shallow_checkout_is_detected_rather_than_misread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git rev-list`` that does not start at the tag means the checkout is wrong."""
    with pytest.raises(RuntimeError, match="deep enough"):
        _exec_gate(tmp_path, monkeypatch, FakeActionsApi(chain=["f" * 40]))


def test_the_gate_writes_its_evidence_even_when_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A red gate must still say what it looked at, or the next person guesses."""
    runs, jobs = _green_runs(SHA_CERTIFIED)
    runs["cap-k8s-certification.yml"] = _runs_payload([])
    code, evidence = _exec_gate(tmp_path, monkeypatch, FakeActionsApi(runs=runs, jobs=jobs))
    assert code == 1
    assert evidence["ancestors_considered"] == len(CHAIN)
    assert sorted(evidence["evidence"]) == sorted(
        set(RELEASE_JOBS) - {"cap-k8s-certification.yml"}
    )


def test_the_release_workflow_does_not_claim_a_job_it_does_not_call() -> None:
    """The prose that started F-21 may not come back in either direction.

    Documentation describing a gate the workflow does not implement is how an
    uncertified release ships with everyone believing otherwise; documentation
    denying a gate that does exist is the same lie mirrored.
    """
    release_text = RELEASE_YML.read_text("utf-8")
    certification_text = (WORKFLOW_DIR / "cap-linux-certification.yml").read_text("utf-8")

    called = {
        used.removeprefix("./.github/workflows/")
        for used in [
            *[str(job.get("uses") or "") for job in _release_doc()["jobs"].values()],
            *[
                str(step.get("uses") or "")
                for job in _release_doc()["jobs"].values()
                for step in job.get("steps", []) or []
            ],
        ]
        if used.startswith("./.github/workflows/")
    }
    assert called == {"ci.yml"}, (
        "release.yml now calls a reusable workflow that the certification "
        "comments do not describe -- update them together"
    )
    assert "includes this file via workflow_call" not in certification_text
    assert GATE_JOB in certification_text, (
        "cap-linux-certification.yml must point at the job that gates "
        "publication rather than at a general promise"
    )
    assert f"{GATE_JOB}:" in release_text


def test_gate_source_compiles() -> None:
    """An inline heredoc is code, and it must at least compile before a runner sees it."""
    compile(_gate_source(), "<release certification gate>", "exec")


def test_the_actions_api_paths_the_gate_uses_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The canned fixtures prove the logic; this proves the real shapes.

    Skipped without an authenticated ``gh``: the job token a workflow gets is not
    this repository's credential, so the live check is a release-engineering step
    recorded in the certification report rather than a CI requirement.
    """
    auth = subprocess.run(  # noqa: S603 -- a known command name, no shell
        ("gh", "auth", "status"), capture_output=True, encoding="utf-8", errors="replace"
    )
    if auth.returncode != 0:
        pytest.skip("no authenticated gh to exercise the live Actions API paths")
    runs = json.loads(
        subprocess.run(
            (
                "gh",
                "api",
                "repos/laolaola278-dev/cyber-agent-platform/actions/workflows/"
                "cap-k8s-certification.yml/runs?per_page=5&status=completed",
            ),
            capture_output=True,
            encoding="utf-8",
            check=True,
        ).stdout
    )["workflow_runs"]
    assert runs, "the runs endpoint the gate resolves evidence from returned nothing"
    first = runs[0]
    assert {"id", "head_sha", "conclusion", "html_url"} <= set(first), (
        "the Actions API stopped returning the fields the gate resolves "
        "evidence by -- re-read its shape before shipping a release"
    )
    jobs = json.loads(
        subprocess.run(
            (
                "gh",
                "api",
                "repos/laolaola278-dev/cyber-agent-platform/actions/runs/"
                f"{first['id']}/jobs?per_page=100",
            ),
            capture_output=True,
            encoding="utf-8",
            check=True,
        ).stdout
    )["jobs"]
    assert jobs and {"name", "conclusion"} <= set(jobs[0]), (
        "the jobs endpoint shape the gate reads for the release job set changed"
    )
