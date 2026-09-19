"""Contract tests for the certification workflow definitions.

These are the defects that only show up as a red job ten minutes into a run on a
runner nobody is looking at:

  * an identifier built from a git ref and then handed to Docker (``release/x``
    contains a slash, which is not a legal container name or hostname -- this
    failed the release layer of the Linux certification at the exact commit that
    was supposed to certify it);
  * a ``workflow_dispatch`` input that no job reads, so a dispatched run "passes"
    having executed nothing;
  * a bootstrap that pipes a download into a binary without ``curl -f``, which
    turns an HTTP error page into an executable;
  * a gate that hard-codes a verdict WORD owned by
    scripts/certification/generate_report.py, which goes stale silently the day
    the generator's states change (this is what failed the release layer at run
    35431391962);
  * inline workflow Python that nothing has ever executed -- a moved key or a
    NameError there costs a whole certification run to discover.

The workflows are YAML the product ships, so they are asserted like code.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = PROJECT_ROOT / ".github" / "workflows"

#: Docker accepts ``[a-zA-Z0-9][a-zA-Z0-9_.-]*`` for container names and network
#: aliases, and the same strings are used as hostnames in URLs.
DOCKER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")

#: Interpolations that can expand to a value containing a slash.
REF_INTERPOLATION = re.compile(
    r"\$\{\{[^}]*\b(github\.(ref_name|ref|head_ref|base_ref))\b[^}]*\}\}"
)


def _workflows() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        doc = yaml.safe_load(path.read_text("utf-8"))
        assert doc and "jobs" in doc, f"{path.name} has no jobs"
        out[path.name] = doc
    assert out, f"no workflows found in {WORKFLOW_DIR}"
    return out


WORKFLOWS = _workflows()


def _interpolated_literal(value: str) -> str:
    """Best-effort stand-in for ``${{ ... }}`` so the name shape can be checked."""
    return re.sub(r"\$\{\{[^}]*\}\}", "x", value)


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_docker_identifiers_cannot_carry_a_git_ref(name: str) -> None:
    """No env value that becomes a container/network/hostname may embed a ref.

    ``github.ref_name`` for a branch such as ``release/1.0.6-rc1`` expands to a
    string with a slash in it; the certification job then dies in
    ``docker: Error response from daemon: Invalid container name``.
    """
    offenders: list[str] = []
    for job_name, job in WORKFLOWS[name].get("jobs", {}).items():
        env = job.get("env") or {}
        for key, value in env.items():
            text = str(value)
            markers = ("PREFIX", "URL", "NAME", "CLUSTER", "IMAGE")
            if not any(marker in key.upper() for marker in markers):
                continue
            if REF_INTERPOLATION.search(text):
                offenders.append(f"{job_name}.env.{key} = {text}")
        for step in job.get("steps", []) or []:
            step_env = step.get("env") or {}
            for key, value in step_env.items():
                if REF_INTERPOLATION.search(str(value)) and "PREFIX" in key.upper():
                    offenders.append(f"{job_name}.{step.get('name')}.env.{key} = {value}")
    assert not offenders, (
        "a git-ref interpolation reached an identifier that Docker or a URL must "
        "accept (use github.run_id, which is numeric): " + "; ".join(offenders)
    )


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_dispatchable_workflows_declare_a_job_that_can_actually_run(name: str) -> None:
    """A dispatch input nobody reads makes a green run mean nothing.

    ``cap-linux-certification.yml`` had a ``layer`` choice and an ``ha_n`` input
    that no condition referenced, so any dispatch on any ref completed having run
    one job.
    """
    doc = WORKFLOWS[name]
    triggers = doc.get(True) or doc.get("on")  # YAML 1.1 turns `on` into True
    if not isinstance(triggers, dict) or "workflow_dispatch" not in triggers:
        pytest.skip(f"{name} is not dispatchable")

    inputs = (triggers["workflow_dispatch"] or {}).get("inputs") or {}
    # Workflow-level `env:` may consume an input (CAP_GA_STRICT is derived from
    # ga_strict there), so the whole document counts as the consumer.
    document_text = yaml.dump(doc)
    runnable = [
        job_name
        for job_name, job in doc["jobs"].items()
        # No `if` at all means the job runs for whatever event started the
        # workflow, dispatch included.
        if "if" not in job or "workflow_dispatch" in str(job.get("if", ""))
    ]
    assert runnable, (
        f"{name} is dispatchable but every job is gated on an event other than "
        "workflow_dispatch, so a dispatched run executes nothing and still "
        "reports success"
    )
    for input_name in inputs:
        consumed = (
            f"inputs.{input_name}" in document_text
            or f"event.inputs.{input_name}" in document_text
        )
        assert consumed, (
            f"{name}: input {input_name!r} is never read by any job, so it is "
            "decorative -- a run that honours it is indistinguishable from one "
            "that ignores it"
        )


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_every_download_that_becomes_an_executable_uses_curl_fail(name: str) -> None:
    """``curl -o`` without ``-f`` writes the error page and the job installs it.

    This is how ``/usr/local/bin/mc`` became HTML when dl.min.io started
    answering 410 Gone: the nightly soak failed with an unhelpful exec error
    rather than a download error.
    """
    offenders: list[str] = []
    for job_name, job in WORKFLOWS[name].get("jobs", {}).items():
        for step in job.get("steps", []) or []:
            run = str(step.get("run", ""))
            for line in run.splitlines():
                stripped = line.strip()
                if not stripped.startswith("curl") or " -o " not in stripped:
                    continue
                tokens = stripped.split()
                flags = [
                    token
                    for token in tokens
                    if token.startswith("-") and not token.startswith("--")
                ]
                long_flags = [token for token in tokens if token.startswith("--")]
                # `-sfL` bundles -s, -f and -L: a substring search for " -f" misses
                # it, and the bundling form is the one the supply-chain job uses.
                has_fail = any("f" in flag.lstrip("-") for flag in flags) or any(
                    flag.startswith("--fail") for flag in long_flags
                )
                if not has_fail:
                    offenders.append(f"{job_name}: {stripped[:120]}")
    assert not offenders, (
        "curl must fail on an HTTP error, or the error page becomes the binary: "
        + "; ".join(offenders)
    )


def test_prefix_is_sanitised_where_the_names_are_built() -> None:
    """The shell side of the same contract: setup.sh cannot be handed a slash.

    The workflow stopped interpolating a ref, but any future caller (or a manual
    run that exports CAP_CERT_PREFIX from `git branch --show-current`) gets the
    same sanitisation, so the invariant lives with the code that builds names.
    """
    script = (PROJECT_ROOT / "scripts" / "certification" / "setup.sh").read_text("utf-8")
    assert re.search(r"PREFIX=.*tr -c 'A-Za-z0-9_\.-'", script), (
        "scripts/certification/setup.sh must sanitise CAP_CERT_PREFIX before "
        "building container and network names from it"
    )

#: The workflows whose purpose is producing a signed-for artifact, as opposed to
#: fast feedback on a diff.
CERTIFICATION_WORKFLOWS = (
    "cap-linux-certification.yml",
    "cap-k8s-certification.yml",
    "cap-ga-certification.yml",
    "cap-ga-reliability.yml",
)


@pytest.mark.parametrize("name", CERTIFICATION_WORKFLOWS)
def test_a_push_cannot_cancel_an_in_flight_certification(name: str) -> None:
    """Certification produces an artifact bound to the SHA that ran.

    Cancellation on new activity is right for CI -- a superseded unit run has no
    value -- but a run killed 17 minutes into the release layer (which is what
    happened at ad91e0e, when the candidate branch took another commit) leaves no
    evidence for any SHA, and on the soak it destroys two hours of real work.
    """
    concurrency = WORKFLOWS[name].get("concurrency") or {}
    assert concurrency.get("cancel-in-progress") is False, (
        f"{name} still cancels an in-flight certification run on a new push"
    )
    assert isinstance(concurrency.get("group"), str) and concurrency["group"], (
        f"{name} lost its concurrency group; two runs on one ref would fight over "
        "the same kind cluster and docker network names"
    )


def test_ci_still_cancels_superseded_runs() -> None:
    """The other half of the exemption: it must not spread to the feedback loop."""
    doc = yaml.safe_load((WORKFLOW_DIR / "ci.yml").read_text("utf-8"))
    assert (doc.get("concurrency") or {}).get("cancel-in-progress") is True


# -- control-plane isolation verdict must be derived, never pinned -------------

#: The three states scripts/certification/generate_report.py can report, and the
#: rule it owns: PASS is forbidden while a shipped deployment path mounts a
#: container-runtime socket; WHICH non-PASS state is correct is the generator's
#: business, so a workflow that pins one of them goes stale whenever the
#: generator's definition of the states moves.
ISOLATION_STATES = ("PASS", "PARTIAL", "NOT_CERTIFIED")


def _pinned_isolation_verdicts(run: str) -> list[str]:
    """Lines that pin WHICH non-PASS verdict must be reported."""
    pinned: list[str] = []
    for line in run.splitlines():
        code = line.split("#", 1)[0]
        if "worker_control_plane_isolation" not in code:
            continue
        if "!=" in code:  # the permitted form: forbid PASS, derive the rest
            continue
        if ("==" in code or "is True" in code or "is False" in code) and any(
            state in code for state in ISOLATION_STATES
        ):
            pinned.append(code.strip())
    return pinned


def _isolation_gate_derives_facts(run: str) -> bool:
    """True when the step re-derives the per-path facts from the generator."""
    return "docker_socket_control_plane" in run


def test_isolation_gate_re_derives_its_verdict_from_the_generator() -> None:
    found = 0
    for name, doc in WORKFLOWS.items():
        for job_name, job in doc["jobs"].items():
            for step in job.get("steps") or []:
                run = step.get("run")
                if not isinstance(run, str) or "worker_control_plane_isolation" not in run:
                    continue
                found += 1
                assert not _pinned_isolation_verdicts(run), (
                    f"{name} job {job_name} pins an isolation verdict; derive it from "
                    "scripts/certification/generate_report.py instead"
                )
                assert _isolation_gate_derives_facts(run), (
                    f"{name} job {job_name} does not re-derive the per-path control-plane "
                    "facts, so it can only ever assert a stale expectation"
                )
    assert found, "no workflow gate inspects worker_control_plane_isolation -- detector is dead"


def test_the_isolation_gate_detector_catches_the_shape_that_broke_release() -> None:
    """Negative control for both halves above.

    Run 35431391962 failed on ``assert data["worker_control_plane_isolation"] ==
    "NOT_CERTIFIED"``: the step asserted the value a chart-only detector used to
    produce, and went stale the day the detector started reading
    docker-compose.yml too. That exact line must be reported.
    """
    derives = "live = docker_socket_control_plane()"
    stale = (
        'assert data["worker_control_plane_isolation"] == "NOT_CERTIFIED", \\\n'
        '    "must be NOT_CERTIFIED (worker mounts docker.sock)"\n' + derives
    )
    pinned_pass = 'assert live["worker_control_plane_isolation"] == "PASS"\n' + derives
    acceptable = 'assert live["worker_control_plane_isolation"] != "PASS"\n' + derives
    assert _pinned_isolation_verdicts(stale), "the pinned-verdict shape must be reported"
    assert _pinned_isolation_verdicts(pinned_pass), "pinning PASS must be reported"
    assert not _pinned_isolation_verdicts(acceptable), "forbidding PASS is the rule, not a pin"
    assert _isolation_gate_derives_facts(acceptable)
    assert not _isolation_gate_derives_facts(stale.replace("docker_socket_control_plane", "x"))


# -- the gate step's inline Python must actually run --------------------------

GATE_STEP = "- name: Assert machine-readable artifact exists with all gates"
GATE_HEREDOC_OPEN = "          python - <<'EOF'\n"
GATE_HEREDOC_CLOSE = "\n          EOF\n"
GATE_ARTIFACT_PATH = 'p = "outputs/cap-cert/cap-28.5-linux-certification.json"'

_GENERATE_REPORT = PROJECT_ROOT / "scripts" / "certification" / "generate_report.py"
_SPEC = importlib.util.spec_from_file_location("generate_report", str(_GENERATE_REPORT))
assert _SPEC and _SPEC.loader
generate_report = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("generate_report", generate_report)
_SPEC.loader.exec_module(generate_report)


def _gate_step_source() -> str:
    """The release-layer gate's own code, lifted out of the workflow verbatim."""
    text = (WORKFLOW_DIR / "cap-linux-certification.yml").read_text("utf-8")
    start = text.index(GATE_STEP)
    body_open = text.index(GATE_HEREDOC_OPEN, start) + len(GATE_HEREDOC_OPEN)
    return textwrap.dedent(text[body_open : text.index(GATE_HEREDOC_CLOSE, body_open)])


def _run_gate(
    source: str,
    payload: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected_sha: str | None = None,
) -> None:
    artifact = tmp_path / "cap-28.5-linux-certification.json"
    artifact.write_text(json.dumps(payload), "utf-8")
    snippet = source.replace(GATE_ARTIFACT_PATH, f"p = {str(artifact)!r}")
    assert snippet != source, f"the gate's artifact path moved -- update {__name__}"
    if expected_sha is None:
        monkeypatch.delenv("CAP_EXPECTED_SHA", raising=False)
    else:
        monkeypatch.setenv("CAP_EXPECTED_SHA", expected_sha)
    exec(  # noqa: S102 -- running the product's own gate to check that it runs
        compile(snippet, "<release artifact gate>", "exec"),
        {"__name__": "cap_release_artifact_gate"},
    )


def test_the_release_artifact_gate_runs_and_rejects_a_stale_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inline workflow Python is code, and it has never been executed by a test.

    A NameError or a moved key in this step costs a full certification run
    (20 minutes of real infrastructure) to discover, so the step is executed
    here against a truthful artifact and then against each kind of artifact it
    is supposed to refuse.
    """
    source = _gate_step_source()
    live = generate_report.docker_socket_control_plane()
    sha = "0bc8efa1234567890abcdef1234567890abcdef1"
    truthful = {
        "phase": "28.5-L",
        "commit": sha,
        "gates": dict.fromkeys(generate_report.REQUIRED_GATES, "PASS"),
        "sandbox_workload_isolation": "PASS",
        **live,
    }

    _run_gate(source, truthful, tmp_path, monkeypatch, expected_sha=sha)  # must not raise

    stale = {**truthful, "worker_control_plane_isolation": "NOT_CERTIFIED"}
    with pytest.raises(AssertionError, match="disagree with the repo"):
        _run_gate(source, stale, tmp_path, monkeypatch, expected_sha=sha)

    missing = {k: v for k, v in truthful.items() if k != "compose_worker_mounts_runtime_socket"}
    with pytest.raises(AssertionError, match="disagree with the repo"):
        _run_gate(source, missing, tmp_path, monkeypatch, expected_sha=sha)

    # evidence from another commit cannot certify this one
    with pytest.raises(AssertionError, match="evidence from another commit"):
        _run_gate(source, truthful, tmp_path, monkeypatch, expected_sha="f" * 40)
    unbound = {k: v for k, v in truthful.items() if k != "commit"}
    with pytest.raises(AssertionError, match="must record the SHA"):
        _run_gate(source, unbound, tmp_path, monkeypatch, expected_sha=sha)
    with pytest.raises(AssertionError, match="must record the SHA"):
        _run_gate(source, {**truthful, "commit": "unknown"}, tmp_path, monkeypatch, sha)

    # A chart that mounts a runtime socket is a release blocker even if the
    # artifact agrees with it: the gate must read the repo, not just the file.
    monkeypatch.setattr(
        generate_report, "chart_worker_mounts_control_socket", lambda: True, raising=True
    )
    mounted = {
        k: (generate_report.docker_socket_control_plane()[k] if k in live else v)
        for k, v in truthful.items()
    }
    with pytest.raises(AssertionError, match="RELEASE BLOCKER"):
        _run_gate(source, mounted, tmp_path, monkeypatch, expected_sha=sha)
