"""The declared quality gates and the executed ones may not drift apart.

``make lint``/``make check`` are what a developer runs before delivery and
``ci.yml`` is what actually blocks a merge. They were found out of sync once
already (the Makefile was running gates that no longer exist while skipping the
ones CI added), so the equivalence is now asserted rather than maintained by
hand.

The second half matters more than the first: CI's unit job ``--ignore``s five
Linux/container certification suites and ``--deselect``s one long benchmark.
An exclusion that just moves a test is fine; an exclusion that quietly ENDS a
test's only execution is a hole in the release evidence (certification section
12 asks exactly "where does the deselected test run -- if never: BLOCKED"). So
every exclusion in CI is required to name a home that really collects it.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = PROJECT_ROOT / ".github" / "workflows"
MAKEFILE = (PROJECT_ROOT / "Makefile").read_text("utf-8")
CI = yaml.safe_load((WORKFLOW_DIR / "ci.yml").read_text("utf-8"))


def _target_line(target: str) -> tuple[str, list[str]]:
    """The ``target: prerequisites`` header and the recipe body that follows it."""
    lines = MAKEFILE.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith(f"{target}:"))
    except StopIteration:
        raise AssertionError(f"Makefile has no {target} target") from None
    header = lines[start].split(":", 1)[1].strip()
    body = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(("\t", "  ")):
            break
        body.append(line.strip().lstrip("@").strip())
    return header, [line for line in body if line]


def _recipe(target: str) -> str:
    """The shell lines of one Makefile target, joined."""
    _, body = _target_line(target)
    assert body, f"Makefile target {target} has no recipe"
    return " \n".join(body)


def _run_commands(doc: dict) -> list[str]:
    """Every multi-or-single-line ``run:`` body in a workflow."""
    out: list[str] = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run = step.get("run")
            if isinstance(run, str):
                out.append(run)
    return out


def _pytest_tokens(text: str) -> list[str]:
    """Whitespace tokens in ``text`` that select test files (globs allowed).

    Flag values are handled: ``--ignore=backend/tests/x.py`` yields the path it
    names, because for the question "is this file collected anywhere?" an
    explicit ignore is not a collection -- callers filter those out themselves.
    """
    tokens: list[str] = []
    for raw in re.split(r"[\s\\\n]+", text):
        value = raw.split("=", 1)[-1] if raw.startswith("--") else raw
        if value.endswith(".py") or "*.py" in value:
            tokens.append(value.removeprefix("backend/"))
    return tokens


#: Files the CI unit job stops collecting, straight out of ci.yml.
CI_IGNORED_TEST_FILES = sorted(
    {
        token
        for run in _run_commands(CI)
        for line in run.splitlines()
        for token in re.findall(r"--ignore=(\S+)", line)
    }
)

#: Test node ids the CI unit job deselects, straight out of ci.yml.
CI_DESELECTED_NODES = sorted(
    {
        token
        for run in _run_commands(CI)
        for line in run.splitlines()
        for token in re.findall(r"--deselect=(\S+)", line)
    }
)


def test_makefile_lint_is_the_ci_lint_gate() -> None:
    """``make lint`` must run exactly the linters CI runs, with the same targets."""
    ci_ruff = next(
        line.strip()
        for run in _run_commands(CI)
        for line in run.splitlines()
        if line.strip().startswith("uv run --project backend ruff check")
    )
    make_ruff = next(
        line.strip()
        for line in _recipe("lint").splitlines()
        if "ruff check" in line
    )
    targets = lambda cmd: tuple(cmd.split("ruff check", 1)[1].split())  # noqa: E731
    assert targets(ci_ruff) == targets(make_ruff), (
        f"make lint checks {targets(make_ruff)} but CI checks {targets(ci_ruff)}; "
        "one of them is not a gate"
    )
    assert "npm run lint" in _recipe("lint"), "make lint dropped the console linter"
    assert "--max-warnings=0" in _recipe("lint"), "make lint tolerates console warnings"


def test_makefile_check_runs_the_gates_ci_runs() -> None:
    """The pre-delivery aggregate must not be missing a CI job's core command."""
    prereqs = _target_line("check")[0].split()
    for target in ("lint", "test", "frontend-test", "frontend-build"):
        assert target in prereqs, f"make check no longer runs the {target} gate"
        assert _recipe(target), f"make {target} has no recipe"


def test_every_ci_ignored_suite_still_runs_in_ci() -> None:
    """An ignore that moves a suite to the certification job is fine; one that
    strands it is a hole in the release evidence."""
    assert CI_IGNORED_TEST_FILES, "ci.yml ignores no test files -- scanner is stale"
    other_commands = [
        run
        for doc in (
            yaml.safe_load(path.read_text("utf-8"))
            for path in sorted(WORKFLOW_DIR.glob("*.yml"))
        )
        for run in _run_commands(doc)
        if not any(ignore in run for ignore in CI_IGNORED_TEST_FILES)
    ]
    never = [
        ignored
        for ignored in CI_IGNORED_TEST_FILES
        if not any(
            fnmatch.fnmatch(ignored.replace("backend/", ""), token)
            for token in _pytest_tokens(" \n".join(other_commands))
        )
    ]
    assert not never, (
        f"CI ignores {never} and no other workflow command collects them; that "
        "suite would then never run in any gate"
    )


def test_the_deselected_benchmark_names_where_it_actually_runs() -> None:
    """Section 12 of the release audit, made permanent.

    The 28.2 SQLite durability benchmark is CI-deselected because the runner's
    single-writer lock serialises it past 20 minutes. Its declared home is the
    developer gate, so the rules are: the deselection must be documented right
    there in ci.yml, and ``make test`` must really collect it (no ignore, no
    deselect, and the file exists).
    """
    assert CI_DESELECTED_NODES, "ci.yml deselects nothing -- scanner is stale"
    ci_text = (WORKFLOW_DIR / "ci.yml").read_text("utf-8")
    make_test = _recipe("test")
    for node in CI_DESELECTED_NODES:
        path_part = node.split("::")[0]
        stem = Path(path_part).name
        assert stem in ci_text, (
            f"{node} is deselected in CI but ci.yml never names {stem}: record "
            "where it runs instead (that is what the release audit asks)"
        )
        collected_anywhere = any(
            fnmatch.fnmatch(path_part, token)
            for doc in (
                yaml.safe_load(p.read_text("utf-8"))
                for p in sorted(WORKFLOW_DIR.glob("*.yml"))
            )
            for run in _run_commands(doc)
            for token in _pytest_tokens(run)
            if "--deselect" not in run.split(path_part)[0][-40:]
        )
        local_home = "--deselect" not in make_test and "--ignore" not in make_test
        assert collected_anywhere or local_home, (
            f"{node} is deselected everywhere: it runs in no CI job and make test "
            "no longer collects it"
        )


def test_evidence_a_ci_job_generates_is_actually_uploaded() -> None:
    """A report a job writes and never uploads is evidence that does not exist.

    The release certification report cites CI's own pass/fail, skip and coverage
    numbers, and ``scripts/quality/audit_junit.py`` builds those tables from the
    unit job's JUnit and coverage XML. Both files are produced inside the job and
    vanish with the runner unless the artifact step names them, so the pair is
    bound here: every machine-readable report a ``run:`` step generates has to
    appear in an ``upload-artifact`` path of the same job.
    """
    offenders: list[str] = []
    for job_name, job in (CI.get("jobs") or {}).items():
        generated: set[str] = set()
        uploaded: set[str] = set()
        for step in job.get("steps") or []:
            run = step.get("run")
            if isinstance(run, str):
                patterns = (
                    r"--junitxml=(\S+)",
                    r"--cov-report=xml:(\S+)",
                    r"--tee=(\S+)",
                )
                for pattern in patterns:
                    generated |= set(re.findall(pattern, run))
            if "upload-artifact" in str(step.get("uses") or ""):
                paths = str((step.get("with") or {}).get("path") or "")
                uploaded |= {p.strip().rsplit("/", 1)[-1] for p in paths.splitlines() if p.strip()}
        for name in sorted(generated):
            if name.rsplit("/", 1)[-1] not in uploaded:
                offenders.append(f"{job_name}: generates {name}, uploads no such file")
    assert not offenders, "; ".join(offenders)
