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
  * presence is not execution (F-42): each required job must be listed *and* every
    conclusion recorded under its name must be ``success``, so a run whose release job
    was skipped, cancelled, or left unstarted cannot stand as evidence -- and such a
    run is passed over to the next eligible ancestor, which is the pre-existing
    selection policy rather than a new one;
  * a green release job set is necessary and not sufficient (F-33): the GA and
    K8s rounds upload an artifact holding their own verdict, and this gate reads
    it -- bound to the commit of the run it came from. A development-mode GA
    round, an incomplete one, an absent, duplicated, undownloadable or
    unparseable artifact must all refuse the release, and a strict round that
    says it certified must pass;
  * an API error must raise, never be misread as "uncertified, carry on";
  * the verdict is computed in one place (`decide`), and a recorded refusal cannot
    coexist with a PASS: if an authority record or a classification verdict says
    otherwise while no failure line was recorded, the gate raises rather than passing;
  * every refused authority state is asserted to keep the release from passing, and
    `ERROR` blocks publication for the same structural reason `FAIL` does -- which is
    pinned by asserting that nothing in the gate job tolerates its own failure;
  * the prose in ``cap-linux-certification.yml`` may not claim a mechanism
    ``release.yml`` does not implement, in either direction.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import types
import zipfile
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = PROJECT_ROOT / ".github" / "workflows"
RELEASE_YML = WORKFLOW_DIR / "release.yml"
#: Only for the one live shape check, which skips rather than fails without a credential.
REPO_SLUG = "laolaola278-dev/cyber-agent-platform"

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
#: Evidence at the tagged commit still needs a chain with history: a chain of one
#: is the shallow-checkout signature the gate refuses to read as "uncertified".
CHAIN_TAG = [SHA_TAG, "d" * 40]

INHERITED = json.dumps(
    {"inheritance": "INHERITED", "runtime_affecting": False, "files": []}
)

#: The two workflows whose uploaded artifact carries a verdict of its own.
GA_WORKFLOW = "cap-ga-certification.yml"
K8S_WORKFLOW = "cap-k8s-certification.yml"

#: This module's copy of the gate's authority table, asserted against the gate's
#: own in every evidence file so the two cannot drift apart.
AUTHORITY = {
    GA_WORKFLOW: {
        "artifact": "ga-cert-artifacts",
        "member": "cap-28.7-ga-certification.json",
        "commit": "commit",
        "fields": {"mode": "final-strict", "full_ga_certified": True},
        "summary": {"failed": 0, "not_run": 0, "skipped": 0, "planned": 0},
        "passed_all_gates": True,
    },
    K8S_WORKFLOW: {
        "artifact": "k8s-cert-artifacts",
        "member": "cap-28.6-k8s-certification.json",
        "commit": "commit",
        "fields": {},
        "summary": {"failed": 0, "not_run": 0},
        "passed_all_gates": True,
    },
}

#: Where each verdict file actually sits inside its artifact, as downloaded from
#: the sealed v1.0.6-rc1 rounds (``ga-cert-artifacts`` carries two uploaded
#: paths, so the GA file is nested; the K8s artifact holds one directory).
MEMBER_PATH = {
    GA_WORKFLOW: "cap-cert-ga/cap-28.7-ga-certification.json",
    K8S_WORKFLOW: "cap-28.6-k8s-certification.json",
}

#: The gate counts the sealed rounds recorded, so a fixture that fits an
#: assertion cannot stand in for the shape the generators actually emit.
SEALED_SUMMARIES = {
    GA_WORKFLOW: {
        "total": 40,
        "implemented": 38,
        "passed": 40,
        "failed": 0,
        "not_run": 0,
        "skipped": 0,
        "planned": 0,
    },
    K8S_WORKFLOW: {"total": 34, "passed": 34, "failed": 0, "not_run": 0},
}


def _verdict(workflow: str, sha: str, *, summary: dict | None = None, **fields) -> dict:
    """A round's own verdict file, as ``generate_report_28_7``/``_28_6`` writes it."""
    payload = {
        "phase": "28.7" if workflow == GA_WORKFLOW else "28.6",
        "commit": sha,
        "gate_summary": dict(SEALED_SUMMARIES[workflow]),
    }
    if workflow == GA_WORKFLOW:
        payload["mode"] = "final-strict"
        payload["version"] = "1.0.6-rc1"
        payload["full_ga_certified"] = True
    payload.update(fields)
    if summary:
        payload["gate_summary"] = {**payload["gate_summary"], **summary}
    return payload


def _zip(members: dict[str, object]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in members.items():
            text = body if isinstance(body, bytes) else str(body).encode("utf-8")
            archive.writestr(name, text)
    return buffer.getvalue()


def _artifact(workflow: str, payload) -> bytes:
    """One authoritative verdict file, packed the way Actions uploads it."""
    body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    return _zip({MEMBER_PATH[workflow]: body})


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


def _jobs_payload(
    names: list[str], *, conclusions: dict[str, str | list[str]] | None = None
) -> str:
    """One entry per job, with matrix legs named the way Actions names them.

    ``conclusions`` overrides what a job recorded: a bare value applies to that base
    name, and a list supplies the legs in order. Actions reports a job skipped by its
    ``if`` as *present* with ``conclusion: "skipped"``, so a test that means "the
    release gate never ran" has to write it that way rather than leave it out -- and
    F-42 is exactly the difference between those two shapes.
    """
    overrides = conclusions or {}
    jobs = []
    for name in names:
        if name == "postgres-version-matrix":
            legs = overrides.get(name)
            if isinstance(legs, list):
                jobs += [
                    {"name": f"{name} ({leg})", "conclusion": value}
                    for leg, value in zip(PG_LEGS, legs, strict=True)
                ]
            else:
                jobs += [
                    {"name": f"{name} ({leg})", "conclusion": legs or "success"}
                    for leg in PG_LEGS
                ]
        else:
            jobs.append({"name": name, "conclusion": overrides.get(name, "success")})
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
        exact: dict[str, str] | None = None,
        classify: tuple[int, str] = (0, INHERITED),
        chain: list[str] | None = None,
        resolved: str | None = None,
        gh_fails: bool = False,
        authority: dict[str, dict] | None = None,
    ) -> None:
        self.runs = runs or {}
        self.exact = exact or {}
        self.jobs = jobs or {}
        self.classify = classify
        self.chain = chain if chain is not None else CHAIN
        self.resolved = resolved if resolved is not None else self.chain[0]
        self.gh_fails = gh_fails
        self.calls: list[tuple[str, ...]] = []
        # run id -> workflow, and run id -> the commit that run was made from,
        # read back out of the fixtures: the authority leg addresses a run by id,
        # and a test must not have to remember which id `_green_runs` handed out.
        self.workflow_of: dict[int, str] = {}
        self.sha_of: dict[int, str] = {}
        for workflow, payload in self.runs.items():
            for run in json.loads(payload).get("workflow_runs") or []:
                self.workflow_of.setdefault(run["id"], workflow)
                self.sha_of.setdefault(run["id"], run["head_sha"])
        self.artifact_lists: dict[int, list[dict]] = {}
        self.zip_blobs: dict[int, bytes] = {}
        self.failed_zips: set[int] = set()
        for workflow, spec in AUTHORITY.items():
            override = (authority or {}).get(workflow) or {}
            for run_id, source in self.workflow_of.items():
                if source != workflow:
                    continue
                entries = override.get("artifacts")
                if entries is None:
                    entries = [
                        {
                            "id": 5000 + run_id,
                            "name": spec["artifact"],
                            "expired": False,
                            "digest": f"sha256:{run_id:064d}",
                        }
                    ]
                self.artifact_lists[run_id] = entries
                verdict = override.get("verdict")
                if "zip" in override:
                    blob = override["zip"]
                else:
                    # The round's own artifact says which commit it certified, so
                    # the standing-in fixture has to as well or the gate would be
                    # reading a different checkout's verdict.
                    payload = _verdict(workflow, self.sha_of[run_id])
                    if verdict is not None:
                        payload = verdict(self.sha_of[run_id]) if callable(verdict) else verdict
                    blob = payload if isinstance(payload, bytes) else json.dumps(payload)
                    blob = _artifact(workflow, blob)
                for entry in entries:
                    self.zip_blobs[entry["id"]] = blob
                    if override.get("fail"):
                        self.failed_zips.add(entry["id"])

    def run(self, argv, **_kwargs):  # noqa: ANN001, ANN003 -- duck-typed CompletedProcess
        argv = tuple(str(part) for part in argv)
        self.calls.append(argv)
        result = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ("gh", "api"):
            return self._gh(argv[2], result)
        if argv[0] == "git" and "rev-parse" in argv:
            result.stdout = self.resolved + "\n"
            return result
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
            if f"actions/workflows/{workflow}/runs" not in path:
                continue
            if "head_sha=" in path:
                result.stdout = self.exact.get(workflow, payload)
            else:
                result.stdout = payload
            return result
        for run_id, payload in self.jobs.items():
            if f"actions/runs/{run_id}/jobs" in path:
                result.stdout = payload
                return result
        for run_id, entries in self.artifact_lists.items():
            if f"actions/runs/{run_id}/artifacts" not in path:
                continue
            result.stdout = json.dumps({"total_count": len(entries), "artifacts": entries})
            return result
        if path.endswith("/zip") and "/actions/artifacts/" in path:
            # The gate reads this channel as bytes, so the answer is bytes too --
            # including the stderr it decodes itself when a download fails.
            artifact_id = int(path.rsplit("/", 2)[-2])
            if artifact_id in self.failed_zips:
                result.returncode = 1
                result.stderr = b"HTTP 403: artifact storage refused the download"
                return result
            if artifact_id in self.zip_blobs:
                result.stdout = self.zip_blobs[artifact_id]
                return result
        result.returncode = 1
        result.stderr = f"no fixture answers {path}"
        return result

    def installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The gate does `import subprocess`, which reads sys.modules first, so
        # this is what actually stands between the gate and the network.
        monkeypatch.setitem(sys.modules, "subprocess", types.SimpleNamespace(run=self.run))


def _exec_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api: FakeActionsApi,
    tag_sha: str = SHA_TAG,
    ns_out: dict | None = None,
) -> tuple[int, dict | None]:
    """Run the gate's own code in ``tmp_path``; return (exit code, evidence).

    ``ns_out`` receives the executed script's globals. That is how a test reaches
    ``decide`` -- the verdict rule -- without restating it: a check that reimplements
    the gate's logic proves the reimplementation, not the release path.
    """
    monkeypatch.setenv("CERT_TAG_SHA", tag_sha)
    monkeypatch.setenv("CERT_REPOSITORY", "octo/repo")
    monkeypatch.setenv("CERT_VERSION", "1.0.6-rc1")
    monkeypatch.setenv("GITHUB_REF_NAME", "v1.0.6-rc1")
    monkeypatch.chdir(tmp_path)
    api.installed(monkeypatch)
    namespace = ns_out if ns_out is not None else {}
    namespace["__name__"] = "cap_release_certification_gate"
    code = 0
    try:
        exec(  # noqa: S102 -- executing the product's own release gate
            compile(_gate_source(), "<release certification gate>", "exec"),
            namespace,
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


def test_nothing_in_the_gate_job_tolerates_its_own_failure() -> None:
    """This is what makes `ERROR` block publication, so it is pinned, not assumed.

    A refusal returns 1 and an inability to decide re-raises; either way the step
    fails, the job fails, and every publishing job behind `needs` never runs. One
    `continue-on-error` or one trailing `|| true` would turn the honest "could not
    decide" into a green light, which is a worse failure than the one F-21 was.
    """
    doc = _release_doc()
    job = doc["jobs"][GATE_JOB]
    assert not job.get("continue-on-error"), "the gate job may not tolerate its own failure"
    assert "if" not in job, (
        "a conditional certification gate is an optional one -- `if:` on the job "
        "would let a run exist where publication was never checked"
    )
    assert job["needs"] == "validate-tag", (
        "the gate must run on the tag's own commit, not on a ref it chose itself"
    )
    for step in job["steps"]:
        assert not step.get("continue-on-error"), (
            f"{step.get('name') or step.get('uses')}: a gate step that may fail "
            "without failing the job makes the gate advisory"
        )
        if step.get("name") == GATE_STEP:
            assert "if" not in step, "the verdict step itself must be unconditional"
    gate = next(step for step in job["steps"] if step.get("name") == GATE_STEP)
    script = gate["run"]
    assert "|| true" not in script, "a swallowed exit code is a swallowed verdict"
    assert script.rstrip().endswith("RELEASE_GATE_PY"), (
        "the gate step must end with the script, not with a command that reports success"
    )
    assert "raise SystemExit(GATE_EXIT)" in script, (
        "the gate's exit code is the verdict's only route out of this step"
    )


def test_a_full_recent_runs_page_cannot_hide_evidence_for_the_tagged_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The listing is one page; the ``head_sha`` probe is not subject to it.

    ``cap-linux-certification.yml`` runs on every push to ``main``, so 100
    completed runs is days, not years. Evidence for the commit being tagged can
    therefore sit outside the page while existing, and a gate that read that as
    "uncertified" would refuse a release it should allow -- the opposite failure
    to F-21, and just as much a lie about what it checked.
    """
    runs, jobs = _green_runs(SHA_TAG)
    noise = [(f"{i:040d}", 900 + i, "success") for i in range(100)]
    runs["cap-linux-certification.yml"] = _runs_payload(noise)
    jobs[777] = _jobs_payload(RELEASE_JOBS["cap-linux-certification.yml"])
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs=runs,
            jobs=jobs,
            exact={"cap-linux-certification.yml": _runs_payload([(SHA_TAG, 777, "success")])},
            chain=CHAIN_TAG,
        ),
    )
    assert code == 0, "the exact probe found the tagged commit's run and the gate ignored it"
    record = evidence["evidence"]["cap-linux-certification.yml"]
    assert record["run_id"] == 777
    assert record["runs_examined"] == 101, "page + probe, de-duplicated"


def test_a_full_page_with_no_evidence_says_so_instead_of_claiming_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal has to distinguish "not found" from "not fetched"."""
    noise = [(f"{i:040d}", 900 + i, "success") for i in range(100)]
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs={w: _runs_payload(noise) for w in RELEASE_JOBS},
            jobs={},
            chain=CHAIN_TAG,
        ),
    )
    assert code == 1
    assert len(evidence["failures"]) == len(RELEASE_JOBS)
    assert all("came back full" in reason for reason in evidence["failures"]), (
        "a truncated search must tell the operator to certify, not claim that "
        "certification never happened"
    )
    assert all(
        "dispatch certification on this commit" in reason for reason in evidence["failures"]
    )


def test_gate_accepts_evidence_for_the_tagged_commit_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs, jobs = _green_runs(SHA_TAG)
    code, evidence = _exec_gate(
        tmp_path, monkeypatch, FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG)
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
        assert record["runs_examined"] == 1, f"{workflow} did not record its search"
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
        FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG),
    )
    assert code == 1
    assert evidence["verdict"] == "FAIL"
    assert "cap-linux-certification.yml" not in evidence["evidence"]
    assert any("cap-linux-certification.yml" in reason for reason in evidence["failures"])


#: What a required job can record that is not "it ran and passed". ``None`` is a job
#: that never started; ``""`` and ``"queued"`` stand for anything Actions records that
#: this file has not enumerated -- the gate's rule is equality against ``"success"``,
#: so an unlisted value must not quietly become a pass either.
NOT_A_SUCCESS = [
    "skipped",
    "failure",
    "cancelled",
    "timed_out",
    "action_required",
    "neutral",
    "stale",
    None,
    "",
    "queued",
]


@pytest.mark.parametrize("conclusion", NOT_A_SUCCESS)
def test_a_required_job_that_did_not_succeed_is_never_evidence(
    conclusion, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-42: presence is not execution, and green-on-the-outside is not green inside.

    Run 35594554182 -- a push to `main` -- listed `cap-production-certification`
    with ``conclusion: "skipped"`` and finished successfully. The gate took it as
    the Linux certification evidence for that commit, because the check it had was
    "is this job name in the list". Every value here must fail that check, and the
    failure line has to say which job and what it recorded, so an operator does not
    re-run a round that was never the problem.
    """
    runs, jobs = _green_runs(SHA_TAG)
    runs["cap-linux-certification.yml"] = _runs_payload([(SHA_TAG, 7, "success")])
    jobs[7] = _jobs_payload(
        list(RELEASE_JOBS["cap-linux-certification.yml"]),
        conclusions={"cap-production-certification": conclusion},
    )
    code, evidence = _exec_gate(
        tmp_path, monkeypatch, FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG)
    )
    assert code == 1, f"{conclusion!r} was read as a job that succeeded"
    assert evidence["verdict"] == "FAIL"
    assert "cap-linux-certification.yml" not in evidence["evidence"], (
        f"{conclusion!r} still produced an evidence record for the release"
    )
    reason = next(
        line for line in evidence["failures"]
        if line.startswith("cap-linux-certification.yml")
    )
    assert "passed over" in reason, reason
    assert "cap-production-certification" in reason, reason


def test_an_ineligible_newest_run_yields_to_the_older_eligible_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusing a skipped job changes which run qualifies, not how runs are sought.

    The walk-back policy is the pre-existing one: the newest *eligible* evidence wins,
    at its real distance, with the classifier asked about the gap. Here the tagged
    commit's Linux run skipped its release job and the ancestor's did not, so the
    ancestor is used and the release still certifies -- which is the opposite of a
    gate that has learned to reject everything.
    """
    runs, jobs = _green_runs(SHA_TAG)
    runs["cap-linux-certification.yml"] = _runs_payload(
        [(SHA_TAG, 7, "success"), ("d" * 40, 8, "success")]
    )
    jobs[7] = _jobs_payload(
        list(RELEASE_JOBS["cap-linux-certification.yml"]),
        conclusions={"cap-production-certification": "skipped"},
    )
    jobs[8] = _jobs_payload(list(RELEASE_JOBS["cap-linux-certification.yml"]))
    code, evidence = _exec_gate(
        tmp_path, monkeypatch, FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG)
    )
    assert code == 0, evidence["failures"]
    linux = evidence["evidence"]["cap-linux-certification.yml"]
    assert linux["run_id"] == 8, "the skipped run was still preferred"
    assert linux["sha"] == "d" * 40 and linux["distance"] == 1, linux
    assert linux["jobs"]["cap-production-certification"] == ["success"], linux
    assert linux["diff_verdict"] == "INHERITED", linux


def test_one_cancelled_matrix_leg_is_not_rescued_by_the_two_that_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legs share a base name; choosing the green one is the gate editing the record.

    The same fixture with all three legs green must stay eligible, in the same test:
    the refusal has to be about the conclusion, not about matrix jobs being
    unrepresentable in a rule that reads them as one name.
    """
    names = list(RELEASE_JOBS["cap-linux-certification.yml"])
    for legs, expect_code in (
        (["success", "cancelled", "success"], 1),
        (["success", "skipped", "success"], 1),
        (["success", "success", "success"], 0),
    ):
        target = tmp_path / f"legs-{'-'.join(str(x) for x in legs)}"
        target.mkdir()
        runs, jobs = _green_runs(SHA_TAG)
        runs["cap-linux-certification.yml"] = _runs_payload([(SHA_TAG, 7, "success")])
        jobs[7] = _jobs_payload(names, conclusions={"postgres-version-matrix": legs})
        code, evidence = _exec_gate(
            target, monkeypatch, FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG)
        )
        assert code == expect_code, f"{legs} -> exit {code}: {evidence['failures']}"
        present = "cap-linux-certification.yml" in evidence["evidence"]
        assert present is (expect_code == 0), legs


def test_two_jobs_of_one_name_are_not_resolved_by_picking_the_green_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeated name is read as a set of conclusions, all of which must be green.

    This is the ambiguity contract the authority leg already applies to artifacts,
    carried over to jobs rather than replaced by a preference for good news.
    """
    runs, jobs = _green_runs(SHA_TAG)
    runs[K8S_WORKFLOW] = _runs_payload([(SHA_TAG, 9, "success")])
    jobs[9] = json.dumps(
        {
            "jobs": [
                {"name": "k8s-certification", "conclusion": "success"},
                {"name": "k8s-certification", "conclusion": "failure"},
            ]
        }
    )
    code, evidence = _exec_gate(
        tmp_path, monkeypatch, FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG)
    )
    assert code == 1, "one green instance was allowed to answer for a failed one"
    assert K8S_WORKFLOW not in evidence["evidence"]
    reason = next(line for line in evidence["failures"] if line.startswith(K8S_WORKFLOW))
    assert "k8s-certification" in reason and "failure" in reason, reason


#: Every (workflow, required job) pair the gate declares. The rule must bind all of
#: them, not just the one that happened to be observed failing.
REQUIRED_PAIRS = [
    (workflow, job) for workflow, jobs in RELEASE_JOBS.items() for job in jobs
]


@pytest.mark.parametrize(
    "workflow,job", REQUIRED_PAIRS, ids=[f"{w}:{j}" for w, j in REQUIRED_PAIRS]
)
def test_the_success_rule_binds_every_required_workflow(
    workflow: str, job: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same mechanism for GA, K8s, the soak and Linux -- one skipped job each."""
    runs, jobs = _green_runs(SHA_TAG)
    runs[workflow] = _runs_payload([(SHA_TAG, 42, "success")])
    jobs[42] = _jobs_payload(list(RELEASE_JOBS[workflow]), conclusions={job: "skipped"})
    code, evidence = _exec_gate(
        tmp_path, monkeypatch, FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG)
    )
    assert code == 1, f"{workflow}:{job} skipped was still enough to publish"
    assert evidence["verdict"] == "FAIL"
    assert workflow not in evidence["evidence"], evidence["evidence"][workflow]


def test_the_rule_is_equality_against_success_not_a_blocklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``unqualified_jobs`` in the gate's own namespace, over conclusions at random.

    Asserted against the executed gate rather than a reimplementation in this file,
    so a future edit to the rule is what this test finds -- and so an unenumerated
    conclusion cannot pass by being absent from a list of the bad ones.
    """
    namespace: dict = {}
    runs, jobs = _green_runs(SHA_TAG)
    code, _ = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG),
        ns_out=namespace,
    )
    assert code == 0, "the baseline fixture stopped being eligible evidence"
    unqualified = namespace["unqualified_jobs"]
    wanted = ("ga-certification", "supply-chain")

    assert unqualified(wanted, {"ga-certification": ["success"], "supply-chain": ["s"]}) == {
        "supply-chain": ["s"]
    }
    for value in ("skipped", "queued", "stale", None, "", False, 1, "Success"):
        verdicts = {job: [value] for job in wanted}
        assert set(unqualified(wanted, verdicts)) == set(wanted), value
    assert set(unqualified(wanted, {})) == set(wanted), "a job nobody recorded read as fine"
    assert set(unqualified(wanted, {"ga-certification": [], "supply-chain": ["success"]})) == {
        "ga-certification"
    }, "an empty conclusion list was taken as a pass"
    assert unqualified(wanted, {"ga-certification": ["success", "success"],
                               "supply-chain": ["success"]}) == {}


def test_a_strict_round_that_certified_is_accepted_and_what_was_read_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive half of F-33, with the fields the sealed rounds really recorded."""
    runs, jobs = _green_runs(SHA_TAG)
    api = FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG)
    code, evidence = _exec_gate(tmp_path, monkeypatch, api)
    assert code == 0
    authority = {
        workflow: record["authority"]
        for workflow, record in evidence["evidence"].items()
        if "authority" in record
    }
    assert sorted(authority) == sorted(AUTHORITY), (
        "the gate stopped reading an authoritative verdict somewhere: GA and K8s "
        "are the two rounds that publish one"
    )
    assert evidence["authority"] == AUTHORITY, (
        "the gate's authority table and this module's copy drifted -- the refusal "
        "reasons in the evidence file would describe checks nobody runs"
    )
    for workflow, record in authority.items():
        assert record["verdict"] == "PASS", record
        assert record["artifact"] == AUTHORITY[workflow]["artifact"]
        assert record["member"] == MEMBER_PATH[workflow], (
            f"{workflow}: the verdict was read from a path the real artifact does not have"
        )
        assert record["read"]["commit"] == SHA_TAG
        assert record["read"]["gate_summary"] == SEALED_SUMMARIES[workflow]
    listing = [
        call[2]
        for call in api.calls
        if call[:2] == ("gh", "api")
        and "actions/runs/" in call[2]
        and "/artifacts" in call[2]
    ]
    selected = {record["run_id"] for record in evidence["evidence"].values()}
    assert listing and all(
        any(f"actions/runs/{run_id}/artifacts" in path for run_id in selected)
        for path in listing
    ), "the artifact of a run the gate did not select cannot certify anything"


def test_a_green_development_mode_ga_round_is_not_ga_certification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-33 itself: the job is green because development mode is allowed to be.

    Every push to ``main`` runs ``cap-ga-certification.yml`` with
    ``CAP_GA_STRICT=0``, and in that mode a gate with no evidence in this job is
    PLANNED instead of failing -- so no test fails, the job succeeds, and the
    artifact says ``development``. The run object reports no inputs, so the job
    set above cannot tell a preview from a proof; the round's own verdict can.
    """
    runs, jobs = _green_runs(SHA_TAG)
    preview = _verdict(
        GA_WORKFLOW,
        SHA_TAG,
        mode="development",
        full_ga_certified=False,
        summary={"passed": 35, "planned": 5},
    )
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs=runs, jobs=jobs, chain=CHAIN_TAG, authority={GA_WORKFLOW: {"verdict": preview}}
        ),
    )
    assert code == 1, "a development-mode round shipped a release that claims FULL GA"
    assert evidence["verdict"] == "FAIL"
    record = evidence["evidence"][GA_WORKFLOW]
    assert record["authority"]["verdict"] == "REJECTED"
    reasons = " ".join(evidence["failures"])
    assert "mode='development'" in reasons
    assert "full_ga_certified=False" in reasons
    assert "planned=5, requires 0" in reasons
    assert "35 of 40" in reasons
    assert len(record["authority"]["rejects"]) >= 4, (
        "one complaint at a time would have the operator rerun a round per field"
    )
    assert sorted(evidence["evidence"]) == sorted(RELEASE_JOBS), (
        "the other three rounds' evidence must still be reported alongside the "
        "refusal, or the next person cannot tell what else is missing"
    )


def test_a_strict_round_that_left_gates_planned_or_failing_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`mode` alone is not the verdict, and neither is the flag on its own.

    The first case is what a gate reading only the flag would wave through:
    `full_ga_certified` still says True while five gates are PLANNED. The other
    two are shapes rounds actually recorded (`fdee042a`, `c69d9606`): strict,
    incomplete, and green.
    """
    runs, jobs = _green_runs(SHA_TAG)
    cases = [
        {"summary": {"passed": 35, "planned": 5}},
        {"full_ga_certified": False, "summary": {"passed": 33, "failed": 2, "planned": 5}},
        {"summary": {"passed": 39, "failed": 1}},
    ]
    for index, fields in enumerate(cases):
        target = tmp_path / f"case-{index}"
        target.mkdir()
        code, evidence = _exec_gate(
            target,
            monkeypatch,
            FakeActionsApi(
                runs=runs,
                jobs=jobs,
                chain=CHAIN_TAG,
                authority={GA_WORKFLOW: {"verdict": _verdict(GA_WORKFLOW, SHA_TAG, **fields)}},
            ),
        )
        assert code == 1, f"accepted an incomplete strict round: {fields}"
        assert evidence["verdict"] == "FAIL"


def test_full_ga_certified_is_read_as_stored_rather_than_reinterpreted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`1` and `"true"` are not the `True` the round recorded.

    The flag is the generator's own decision; the counts are read beside it, not
    instead of it. Substituting a recomputed flag for the stored one would put a
    second, weaker implementation of the rule in the release path.
    """
    runs, jobs = _green_runs(SHA_TAG)
    for index, stand_in in enumerate((1, "true", "True")):
        target = tmp_path / f"stand-in-{index}"
        target.mkdir()
        code, evidence = _exec_gate(
            target,
            monkeypatch,
            FakeActionsApi(
                runs=runs,
                jobs=jobs,
                chain=CHAIN_TAG,
                authority={
                    GA_WORKFLOW: {
                        "verdict": _verdict(
                            GA_WORKFLOW, SHA_TAG, full_ga_certified=stand_in
                        )
                    }
                },
            ),
        )
        assert code == 1, f"{stand_in!r} satisfied a check written for True"
        assert any("full_ga_certified" in reason for reason in evidence["failures"])


def test_an_artifact_describing_another_commit_is_not_this_run_s_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generator writes `git rev-parse HEAD`, so it names what it certified."""
    runs, jobs = _green_runs(SHA_TAG)
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs=runs,
            jobs=jobs,
            chain=CHAIN_TAG,
            authority={
                GA_WORKFLOW: {"verdict": _verdict(GA_WORKFLOW, "e" * 40)},
            },
        ),
    )
    assert code == 1
    reasons = " ".join(evidence["failures"])
    assert f"records commit {'e' * 12!r}" in reasons
    assert "different checkout" in reasons, (
        "the refusal has to say why a commit mismatch matters, not just that one "
        "was found"
    )


def test_an_absent_authoritative_artifact_refuses_the_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A green job set with nothing uploaded behind it is not evidence."""
    runs, jobs = _green_runs(SHA_TAG)
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs=runs, jobs=jobs, chain=CHAIN_TAG, authority={GA_WORKFLOW: {"artifacts": []}}
        ),
    )
    assert code == 1
    reason = " ".join(evidence["failures"])
    assert "ga-cert-artifacts" in reason and "0 artifacts" in reason
    assert evidence["evidence"][GA_WORKFLOW]["authority"]["verdict"] == "REJECTED"


def test_two_artifacts_with_one_name_are_not_resolved_by_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Choosing the newest of two would be an invented tie-break."""
    runs, jobs = _green_runs(SHA_TAG)
    duplicated = [
        {"id": 9001, "name": AUTHORITY[GA_WORKFLOW]["artifact"], "expired": False},
        {"id": 9002, "name": AUTHORITY[GA_WORKFLOW]["artifact"], "expired": False},
    ]
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs=runs,
            jobs=jobs,
            chain=CHAIN_TAG,
            authority={GA_WORKFLOW: {"artifacts": duplicated}},
        ),
    )
    assert code == 1
    assert "will not choose between them" in " ".join(evidence["failures"])


def test_an_artifact_holding_two_verdict_files_is_ambiguous_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The zip, not just the listing, has to name one answer."""
    runs, jobs = _green_runs(SHA_TAG)
    two_files = _zip(
        {
            MEMBER_PATH[GA_WORKFLOW]: json.dumps(_verdict(GA_WORKFLOW, SHA_TAG)),
            "ga-dr/copy/cap-28.7-ga-certification.json": json.dumps(
                _verdict(GA_WORKFLOW, SHA_TAG, mode="development")
            ),
        }
    )
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs=runs, jobs=jobs, chain=CHAIN_TAG, authority={GA_WORKFLOW: {"zip": two_files}}
        ),
    )
    assert code == 1
    assert "matched 2 entries" in " ".join(evidence["failures"])


def test_an_unparseable_authoritative_artifact_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Truncated JSON in the verdict file must not read as agreement."""
    runs, jobs = _green_runs(SHA_TAG)
    for index, body in enumerate(("not json at all", '{"full_ga_certified": tru', "[1,2,3]", "")):
        target = tmp_path / f"body-{index}"
        target.mkdir()
        code, evidence = _exec_gate(
            target,
            monkeypatch,
            FakeActionsApi(
                runs=runs,
                jobs=jobs,
                chain=CHAIN_TAG,
                authority={GA_WORKFLOW: {"zip": _zip({MEMBER_PATH[GA_WORKFLOW]: body})}},
            ),
        )
        assert code == 1, f"accepted an unreadable verdict file: {body!r}"
        assert evidence["verdict"] == "FAIL"
        assert "readable" in " ".join(evidence["failures"]) or "object" in " ".join(
            evidence["failures"]
        )


def test_an_expired_artifact_cannot_be_read_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actions deletes old artifacts; an old inherited round may be unreadable."""
    runs, jobs = _green_runs(SHA_TAG)
    expired = [
        {
            "id": 9003,
            "name": AUTHORITY[K8S_WORKFLOW]["artifact"],
            "expired": True,
        }
    ]
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs=runs, jobs=jobs, chain=CHAIN_TAG, authority={K8S_WORKFLOW: {"artifacts": expired}}
        ),
    )
    assert code == 1
    assert "has expired" in " ".join(evidence["failures"])


def test_a_k8s_round_with_unrun_gates_is_refused_by_the_same_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The authority check is a table, not a special case for GA.

    K8s publishes no `mode` field -- its artifact is the gate table itself -- so
    what is required of it is its own counts, read from its own artifact.
    """
    runs, jobs = _green_runs(SHA_TAG)
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs=runs,
            jobs=jobs,
            chain=CHAIN_TAG,
            authority={
                K8S_WORKFLOW: {
                    "verdict": _verdict(
                        K8S_WORKFLOW, SHA_TAG, summary={"passed": 29, "not_run": 5}
                    )
                }
            },
        ),
    )
    assert code == 1
    reasons = " ".join(evidence["failures"])
    assert "not_run=5, requires 0" in reasons and "29 of 34" in reasons
    assert "mode" not in reasons, "the K8s artifact has no mode field to demand"


def test_gates_that_are_counted_nowhere_are_still_not_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one thing `passed == total` catches on its own.

    Every enumerated count is zero and nothing is marked failed, yet four gates
    have no PASS: a status the summary does not tally is invisible to a gate that
    only looks for the failure words it knows.
    """
    runs, jobs = _green_runs(SHA_TAG)
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs=runs,
            jobs=jobs,
            chain=CHAIN_TAG,
            authority={
                K8S_WORKFLOW: {"verdict": _verdict(K8S_WORKFLOW, SHA_TAG, summary={"passed": 30})}
            },
        ),
    )
    assert code == 1
    assert "30 of 34" in " ".join(evidence["failures"])


def test_a_missing_gate_count_is_not_read_as_the_zero_it_should_have_been(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs, jobs = _green_runs(SHA_TAG)
    payload = _verdict(GA_WORKFLOW, SHA_TAG)
    del payload["gate_summary"]["planned"]
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs=runs, jobs=jobs, chain=CHAIN_TAG, authority={GA_WORKFLOW: {"verdict": payload}}
        ),
    )
    assert code == 1
    assert "no planned count" in " ".join(evidence["failures"])


def test_a_failed_artifact_download_says_could_not_decide_not_uncertified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Storage refusing a download is not a verdict; the release still stops."""
    runs, jobs = _green_runs(SHA_TAG)
    with pytest.raises(RuntimeError, match="gh api"):
        _exec_gate(
            tmp_path,
            monkeypatch,
            FakeActionsApi(
                runs=runs,
                jobs=jobs,
                chain=CHAIN_TAG,
                authority={GA_WORKFLOW: {"fail": True}},
            ),
        )
    evidence = json.loads((tmp_path / EVIDENCE_FILE).read_text("utf-8"))
    assert evidence["verdict"] == "ERROR"
    assert "artifact" in evidence["error"] and "/zip" in evidence["error"], (
        "the error has to name the artifact download, or the operator goes looking "
        "for a certification that was never the problem"
    )


#: Every state in which the gate reads an authoritative artifact and refuses it. The
#: assertion is the same for all of them and is the one that matters for a release:
#: the verdict may not be PASS.
REFUSED = {
    "development mode": {
        "verdict": _verdict(
            GA_WORKFLOW, SHA_TAG, mode="development", full_ga_certified=False,
            summary={"passed": 35, "planned": 5},
        )
    },
    "planned gates": {
        "verdict": _verdict(GA_WORKFLOW, SHA_TAG, summary={"passed": 35, "planned": 5})
    },
    "failing gates": {
        "verdict": _verdict(
            GA_WORKFLOW, SHA_TAG, full_ga_certified=False,
            summary={"passed": 33, "failed": 2, "planned": 5},
        )
    },
    "wrong commit": {"verdict": _verdict(GA_WORKFLOW, "e" * 40)},
    "absent artifact": {"artifacts": []},
    "duplicated artifact": {
        "artifacts": [
            {"id": 9001, "name": AUTHORITY[GA_WORKFLOW]["artifact"]},
            {"id": 9002, "name": AUTHORITY[GA_WORKFLOW]["artifact"]},
        ]
    },
    "expired artifact": {
        "artifacts": [{"id": 9003, "name": AUTHORITY[GA_WORKFLOW]["artifact"], "expired": True}]
    },
    "two verdict files in one artifact": {
        "zip": _zip(
            {
                MEMBER_PATH[GA_WORKFLOW]: json.dumps(_verdict(GA_WORKFLOW, SHA_TAG)),
                "ga-dr/replay/cap-28.7-ga-certification.json": "{}",
            }
        )
    },
    "malformed json": {"zip": _zip({MEMBER_PATH[GA_WORKFLOW]: '{"mode": "final-strict"'})},
    "a count the artifact does not report": {
        "verdict": {
            k: v
            for k, v in _verdict(GA_WORKFLOW, SHA_TAG).items()
            if k != "gate_summary"
        }
        | {"gate_summary": {"total": 40, "passed": 40, "failed": 0, "not_run": 0, "skipped": 0}}
    },
}


@pytest.mark.parametrize("reason", sorted(REFUSED))
def test_no_refused_authority_state_leaves_the_release_able_to_pass(
    reason: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One table, one assertion: whatever the gate refuses, PASS is not on the menu."""
    runs, jobs = _green_runs(SHA_TAG)
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(
            runs=runs,
            jobs=jobs,
            chain=CHAIN_TAG,
            authority={GA_WORKFLOW: REFUSED[reason]},
        ),
    )
    assert code != 0, f"{reason}: the gate refused the artifact and still exited successfully"
    assert evidence["verdict"] != "PASS", f"{reason}: refused, and published a PASS anyway"
    assert evidence["failures"], f"{reason}: a refusal with no recorded reason"
    authority = evidence["evidence"][GA_WORKFLOW]["authority"]
    assert authority["verdict"] == "REJECTED", f"{reason}: {authority}"


def test_error_and_fail_refuse_publication_by_different_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The diagnosis differs; the consequence for the tag does not.

    `FAIL` is the gate reading evidence that says uncertified. `ERROR` is the gate
    unable to read anything -- and an uninformed gate is not a licence, so this
    asserts the route that a `PASS` could come from is closed in both cases.
    """
    runs, jobs = _green_runs(SHA_TAG)
    accepted = tmp_path / "accepted"
    accepted.mkdir()
    failure_code, failure_evidence = _exec_gate(
        accepted,
        monkeypatch,
        FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG),
    )
    assert failure_code == 0 and failure_evidence["verdict"] == "PASS"

    refused = tmp_path / "refused"
    refused.mkdir()
    code, evidence = _exec_gate(
        refused,
        monkeypatch,
        FakeActionsApi(
            runs=runs,
            jobs=jobs,
            chain=CHAIN_TAG,
            authority={
                GA_WORKFLOW: {
                    "verdict": _verdict(GA_WORKFLOW, SHA_TAG, mode="development")
                }
            },
        ),
    )
    assert code == 1 and evidence["verdict"] == "FAIL"

    broken = tmp_path / "broken"
    broken.mkdir()
    with pytest.raises(RuntimeError):
        _exec_gate(
            broken,
            monkeypatch,
            FakeActionsApi(
                runs=runs, jobs=jobs, chain=CHAIN_TAG,
                authority={GA_WORKFLOW: {"fail": True}},
            ),
        )
    errored = json.loads((broken / EVIDENCE_FILE).read_text("utf-8"))
    assert errored["verdict"] == "ERROR"
    assert errored["tag_sha"] == SHA_TAG and errored["authority"] == AUTHORITY, (
        "the ERROR evidence has to carry the same context, or the refusal is unexplained"
    )
    assert {evidence["verdict"], errored["verdict"]} == {"FAIL", "ERROR"}, (
        "collapsing the two would lose which of them the operator can answer by dispatching"
    )


def test_the_verdict_is_computed_from_every_signal_the_gate_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`decide()` cannot let a recorded refusal coexist with a PASS.

    Reached through the executed gate rather than reimplemented here, because the
    thing under test is the release path's own verdict rule.
    """
    namespace: dict = {}
    runs, jobs = _green_runs(SHA_TAG)
    code, evidence = _exec_gate(
        tmp_path,
        monkeypatch,
        FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG),
        ns_out=namespace,
    )
    assert code == 0
    decide = namespace["decide"]
    assert decide(evidence["evidence"], []) == "PASS"
    assert decide({}, ["any refusal"]) == "FAIL"
    with pytest.raises(RuntimeError, match="no failure line"):
        decide({GA_WORKFLOW: {"authority": {"verdict": "REJECTED", "rejects": []}}}, [])
    with pytest.raises(RuntimeError, match="no failure line"):
        decide({K8S_WORKFLOW: {"authority": {"verdict": None}}}, [])
    with pytest.raises(RuntimeError, match="no failure line"):
        decide({"cap-linux-certification.yml": {"diff_verdict": "RECERTIFICATION_REQUIRED"}}, [])
    assert decide({"cap-linux-certification.yml": {"diff_verdict": "INHERITED"}}, []) == "PASS", (
        "an inherited distance the classifier accepted is not a contradiction"
    )


@pytest.mark.parametrize("total", [32, 33, 34])
def test_an_older_round_with_a_smaller_gate_table_still_certifies(
    total: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing in the authority rule may hardcode today's gate count.

    Measured, not assumed: earlier Kubernetes rounds recorded 32/32 and 33/33, so a
    fixed 34 -- or 40 on the GA side -- would refuse certification that the
    classifier says is inheritable, which is the opposite failure to F-33 and just
    as expensive for whoever holds the tag.
    """
    runs, jobs = _green_runs(SHA_TAG)
    payload = _verdict(K8S_WORKFLOW, SHA_TAG, summary={"total": total, "passed": total})
    target = tmp_path / f"table-{total}"
    target.mkdir()
    code, evidence = _exec_gate(
        target,
        monkeypatch,
        FakeActionsApi(
            runs=runs,
            jobs=jobs,
            chain=CHAIN_TAG,
            authority={K8S_WORKFLOW: {"verdict": payload}},
        ),
    )
    assert code == 0, f"a {total}-gate round was refused: {evidence['failures']}"
    assert evidence["evidence"][K8S_WORKFLOW]["authority"]["verdict"] == "PASS"


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
    assert all(
        "0 most recent completed runs examined" in reason for reason in evidence["failures"]
    ), (
        "the refusal has to say how far it looked: 'not on the page' and 'never "
        "ran' are different remedies for the operator holding the tag"
    )


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
        tmp_path, monkeypatch, FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG)
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
    evidence = json.loads((tmp_path / EVIDENCE_FILE).read_text("utf-8"))
    assert evidence["verdict"] == "ERROR", (
        "a gate that could not look must say so, not leave the run with two red "
        "steps and no artifact explaining which of them decided anything"
    )
    assert "gh api" in evidence["error"]
    assert evidence["tag_sha"] == SHA_TAG and evidence["required"] == RELEASE_JOBS


def test_a_shallow_checkout_is_reported_as_one_rather_than_as_absent_certification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Depth-1 history cannot show an ancestor, and must not claim one is missing."""
    with pytest.raises(RuntimeError, match="fetch-depth"):
        _exec_gate(
            tmp_path,
            monkeypatch,
            FakeActionsApi(runs={}, jobs={}, chain=[SHA_TAG]),
        )


def test_an_abbreviated_tag_sha_is_normalised_before_it_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The evidence, the walk and the ``head_sha`` probe all need the full commit."""
    runs, jobs = _green_runs(SHA_TAG)
    api = FakeActionsApi(runs=runs, jobs=jobs, chain=CHAIN_TAG)
    code, evidence = _exec_gate(tmp_path, monkeypatch, api, tag_sha=SHA_TAG[:7])
    assert code == 0, "an abbreviated input must still resolve to the tagged commit"
    assert evidence["tag_sha"] == SHA_TAG
    probes = [
        call[2]
        for call in api.calls
        if call[:2] == ("gh", "api") and "head_sha=" in call[2]
    ]
    assert probes, "the gate must probe by head_sha, which only matches full SHAs"
    assert all(probe.endswith(SHA_TAG) for probe in probes), probes


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


def _gh_detail(path: str, binary: bool = False) -> tuple[object, str]:
    """`gh api`, with the reason it did not answer.

    The diagnosis matters as much as the answer: "None" cannot tell an operator
    whether `gh` is absent, the token lacks the scope, GitHub is degraded, or the
    response was not JSON. Off CI all four are equally harmless -- the check skips.
    Inside CI the workflow has *declared* the capability, which is the difference.
    """
    try:
        proc = subprocess.run(  # noqa: S603 -- a known command name, no shell
            ("gh", "api", path),
            capture_output=True,
            encoding=None if binary else "utf-8",
            errors=None if binary else "replace",
            timeout=120,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as error:
        return None, f"the call itself failed: {type(error).__name__}: {error}"
    if proc.returncode != 0:
        detail = proc.stderr or b"" if binary else proc.stderr or ""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        first = (detail.strip().splitlines() or ["(no stderr)"])[0]
        return None, f"`gh api` exited {proc.returncode}: {first}"
    return proc.stdout, "answered"


def _gh(path: str, binary: bool = False):
    """A lookup that can be unavailable, never a hard failure -- see `_gh_detail`."""
    return _gh_detail(path, binary)[0]


#: Set by the runner itself; the only honest way to know which contract is in force.
_IN_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

#: A declared capability that does not answer is worth one retry before it is worth a
#: complaint: a degraded API and a broken contract must not be conflated.
_LIVE_RETRY_SECONDS = 20


def _live(answer: object, path: str, what: str) -> object:
    """Return the answer, or explain why this check could not run where it claims to.

    Off CI: skip, as before -- no `gh`, no credential, nothing to conclude.
    In CI: the job declares `permissions: actions: read`, so silence is a finding.
    Retry once, then fail -- naming both readings rather than picking one, because
    the difference between "GitHub was degraded" and "the scope changed" is decided
    by whether a re-run fixes it, not by this file.
    """
    if answer is not None:
        return answer
    _, diagnosis = _gh_detail(path)
    if not _IN_GITHUB_ACTIONS:
        pytest.skip(f"{what} needs the live Actions API, which cannot be read here: {diagnosis}")
    time.sleep(_LIVE_RETRY_SECONDS)
    retried, second_diagnosis = _gh_detail(path)
    if retried is not None:
        return retried
    pytest.fail(
        f"{what} could not read the live Actions API from inside CI, twice "
        f"({second_diagnosis}; first attempt: {diagnosis}). The `backend` job declares "
        "`permissions: actions: read`, so this is either the scope changing under us "
        "(a real contract break: fix the workflow, because the release gate resolves its "
        "evidence through these same endpoints) or GitHub's API being degraded (re-run "
        "this job before reading anything into it). Neither reading is a publication "
        "verdict, and neither may be reported as a skip that hides the question."
    )


def _live_json(path: str, what: str) -> dict:
    answer = _live(_gh(path), path, what)
    try:
        return json.loads(answer)
    except (TypeError, json.JSONDecodeError):  # an HTML error page where JSON should be
        return _live(None, path, what)  # not an answer: skip or fail by the same rule


def _live_bytes(path: str, what: str) -> bytes:
    answer = _live(_gh(path, binary=True), path, what)
    return answer if isinstance(answer, bytes) else _live(None, path, what)


def _live_condition(ok: bool, message: str, what: str) -> None:
    """A data condition a live check cannot proceed past: skip off CI, fail in CI.

    Same rule as a missing answer -- inside the `backend` job the capability is
    declared, so "no run uploaded the artifact the gate reads" is a finding about the
    release path, not a reason to report nothing.
    """
    if ok:
        return
    if not _IN_GITHUB_ACTIONS:
        pytest.skip(f"{message} -- and this check needs the live Actions API, unreadable here")
    pytest.fail(f"{message} -- inside CI, where `permissions: actions: read` is declared, "
                f"this is a finding about {what}, not a skip")


def test_the_authoritative_artifact_is_where_the_gate_reads_it() -> None:
    """The fixture proves the logic; only the real API proves the path to it.

    A run that no longer uploads `ga-cert-artifacts`, an artifact whose contents
    moved to another directory inside the zip, or a verdict file that renamed
    `full_ga_certified` would each make the gate refuse every release -- or, if
    someone "fixed" it by weakening the read, let one through unread.

    Two regimes, because the two environments differ in what they promise. Where no
    `gh` or credential exists -- any developer machine, this repository's own audit
    host -- the check skips: it has no basis for a verdict. Inside the `backend` CI
    job, which declares `permissions: actions: read`, silence is a finding and the
    check fails with the diagnosis, because that is precisely the case where the
    release gate's live read path is supposed to have been validated.
    """
    listing = _live_json(
        f"repos/{REPO_SLUG}/actions/workflows/cap-ga-certification.yml"
        "/runs?per_page=10&status=completed",
        "listing the GA certification runs",
    )
    runs = listing.get("workflow_runs") or []
    _live_condition(bool(runs), "the GA certification workflow has no completed runs listed",
                    "the gate's run resolution")
    checked = 0
    for run in runs:
        artifacts = _live_json(
            f"repos/{REPO_SLUG}/actions/runs/{run['id']}/artifacts?per_page=100",
            "listing a run's artifacts",
        )
        _live_condition(
            "artifacts" in artifacts,
            f"run {run['id']} answered the artifacts endpoint without an `artifacts` list",
            "the gate's artifact resolution",
        )
        named = [
            artifact
            for artifact in artifacts["artifacts"]
            if artifact.get("name") == AUTHORITY[GA_WORKFLOW]["artifact"]
        ]
        if not named:
            continue  # a run whose job never reached the upload step
        checked += 1
        assert len(named) == 1, (
            f"run {run['id']} has {len(named)} artifacts named "
            f"{AUTHORITY[GA_WORKFLOW]['artifact']}: the gate refuses ambiguity, so "
            "one has to be the authority"
        )
        artifact = named[0]
        if artifact.get("expired"):
            continue
        blob = _live_bytes(
            f"repos/{REPO_SLUG}/actions/artifacts/{artifact['id']}/zip",
            "downloading the authoritative artifact",
        )
        members = [
            name
            for name in zipfile.ZipFile(io.BytesIO(blob)).namelist()
            if name.rsplit("/", 1)[-1] == AUTHORITY[GA_WORKFLOW]["member"]
        ]
        assert len(members) == 1, (
            f"{AUTHORITY[GA_WORKFLOW]['member']} matched {len(members)} entries in "
            "the real artifact -- the gate's one-unambiguous-file rule would stop "
            "accepting every round"
        )
        payload = json.loads(zipfile.ZipFile(io.BytesIO(blob)).read(members[0]))
        assert {"commit", "mode", "full_ga_certified", "gate_summary"} <= set(payload), (
            "the verdict file stopped carrying the fields the release gate reads"
        )
        assert {"total", "passed", "failed", "not_run", "skipped", "planned"} <= set(
            payload["gate_summary"]
        )
        assert payload["commit"] == run["head_sha"], (
            "the artifact's recorded commit is not the run's head_sha, so the "
            "gate's binding check would refuse a certification it should accept"
        )
        break
    _live_condition(
        checked > 0,
        "no recent GA certification run in this repository's history uploaded the artifact "
        "the release gate reads",
        "every future release",
    )


def test_the_actions_api_paths_the_gate_uses_exist() -> None:
    """The canned fixtures prove the logic; this proves the real shapes.

    Off CI, no answer means no basis: no binary, no credential, a rate limit, a job
    token without Actions reads, or a network that will not answer, and the check
    skips. In CI the scope is declared, so the same silence fails with both readings
    named -- a changed scope is a contract break the release path shares, a degraded
    API is a re-run, and neither may be reported as a skip that hides the question.
    It ran for real on the audit host and produced the evidence cited in §23 F-21 of
    the certification report; batch 1's remote validation recorded it skipping in CI
    for want of `actions: read`, which is the gap this job's permissions now close.
    """
    runs = _live_json(
        f"repos/{REPO_SLUG}/actions/workflows/cap-k8s-certification.yml"
        "/runs?per_page=5&status=completed",
        "listing the K8s certification runs",
    )
    _live_condition(
        bool(runs.get("workflow_runs")),
        "the K8s certification workflow has no completed runs listed",
        "the gate's run resolution",
    )
    first = runs["workflow_runs"][0]
    assert {"id", "head_sha", "conclusion", "html_url"} <= set(first), (
        "the Actions API stopped returning the fields the gate resolves "
        "evidence by -- re-read its shape before shipping a release"
    )
    jobs = _live_json(
        f"repos/{REPO_SLUG}/actions/runs/{first['id']}/jobs?per_page=100",
        "listing a run's jobs",
    )
    _live_condition(
        bool(jobs.get("jobs")),
        f"run {first['id']} listed no jobs",
        "the gate's release-job-set check",
    )
    assert {"name", "conclusion"} <= set(jobs["jobs"][0]), (
        "the jobs endpoint shape the gate reads for the release job set changed"
    )
