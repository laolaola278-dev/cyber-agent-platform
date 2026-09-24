"""BATCH 3 A2.2 Stage 1: the one controlled-buildx install mechanism, and the order it runs in.

`scripts/release/install_controlled_buildx.py` is the single implementation behind every job that
builds a release image: A2.1 proved the mechanism in a non-publishing job, and A2.2 puts the same
mechanism under the publishing one. "One mechanism" is not a style preference -- two copies of a
download-and-verify recipe is how a CI rehearsal and the thing it rehearses stop agreeing.

What is tested here is the *order*, because the order is what makes the evidence mean anything.
The assertions that matter are the ones a weaker implementation could not pass: bytes that hash
differently are never executed, a target inside a docker CLI plugin directory is refused before
anything is downloaded, and a builder is never created by a binary that has not yet been asked who
it is. Those are behavioural checks against an injected fetcher, hasher and runner -- the A2.1 job
used to carry this recipe inline, and its text assertions moved here with it rather than being
dropped.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "release"))

import install_controlled_buildx as installer  # noqa: E402
import record_build_producer as recorder  # noqa: E402

from tests.test_producer_observation_contract import (  # noqa: E402
    BUILDKIT,
    BUILDX,
    PIN,
    PIN_PATH,
)

CI_PATH = PROJECT_ROOT / ".github/workflows/ci.yml"
CI = yaml.safe_load(CI_PATH.read_text("utf-8"))
LOCK_PATH = PROJECT_ROOT / "deployment/third-party-images.json"
MECHANISM = "scripts/release/install_controlled_buildx.py"
ASSET = b"#!/bin/sh\n# a fake release asset; the real one is 65 MB of buildx\n"
ASSET_SHA = "sha256:" + hashlib.sha256(ASSET).hexdigest()
FAKE_COMMIT = "0b265a9f62db554fa9aba6dd19e1bd5704bc7d8a"
BUILDER = "cap-a22-test-builder"
INSPECT_TEXT = f"Name:          {BUILDER}\nDriver:        docker-container\n\nNodes:\n"


def make_pin(tmp_path: Path, *, sha: str = ASSET_SHA, version: str = BUILDX["version"],
             commit: str = FAKE_COMMIT, size: int | None = None,
             url: str = "https://example.invalid/buildx-v0.37.1.linux-amd64",
             path: str | None = None) -> Path:
    """A pin file in the shipped shape, pointed at a fake asset this module can produce.

    A throwaway pin rather than the repository's own, so the controls below can make it untrue on
    purpose. One test reads the shipped pin, and it reads it through the same loader.
    """
    pin = {
        "version": version,
        "expected_git_commit": commit,
        "source": {"download_url": url, "platform": "linux/amd64",
                   "asset_size_bytes": len(ASSET) if size is None else size},
        "integrity": {"algorithm": "sha256", "expected": sha},
        "install": {"path": str(path or (tmp_path / "controlled-buildx" / "buildx"))},
    }
    out = tmp_path / "pin.json"
    out.write_text(json.dumps(pin, indent=2), encoding="utf-8")
    return out


def target_of(pin_path: Path) -> str:
    return json.loads(pin_path.read_text("utf-8"))["install"]["path"]


def _stat_of(path: str) -> tuple[int, int, int] | None:
    """What the machine holds at this path -- enough to prove a file was left alone.

    Used where the path is a real system location that may already exist, so "it is not there"
    is not the property under test and "it is exactly what it was" is.
    """
    candidate = Path(path)
    if not candidate.is_file():
        return None
    stat = candidate.stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_mode)


def version_answer(version: str = BUILDX["version"], commit: str = FAKE_COMMIT) -> str:
    """A standalone buildx's version line: it prints no install path, as CI measured at A2.1."""
    return f"github.com/docker/buildx {version} {commit}"


def command_name(argv: list[str]) -> str:
    if argv[0] == "docker":
        return "docker-" + "-".join(argv[1:])
    return argv[1] if len(argv) > 1 else argv[0]


class Events:
    """One shared log, so a test can name the order rather than only the outcome."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.commands: list[list[str]] = []

    def fetch(self, url: str, dest: Path, content: bytes = ASSET) -> int:
        self.order.append("fetch")
        Path(dest).write_bytes(content)
        return len(content)

    def hash(self, path: Path) -> str:
        self.order.append("hash")
        return installer.sha256_of(Path(path))

    def run(self, argv: list[str], answers: dict) -> tuple[int, str, str]:
        self.order.append("run:" + command_name(list(argv)))
        self.commands.append(list(argv))
        return answers.get(tuple(argv), (2, "", f"unexpected command: {tuple(argv)}"))


def answers_for(target: str) -> dict[tuple[str, ...], tuple[int, str, str]]:
    """A machine where the pinned binary answers for itself and the builder is created."""
    return {
        (target, "version"): (0, version_answer(), ""),
        (target, "create", "--name", BUILDER, "--driver", "docker-container",
         "--driver-opt", f"image={BUILDKIT['image_ref']}", "--use"): (0, BUILDER, ""),
        (target, "inspect", "--bootstrap", BUILDER): (0, INSPECT_TEXT, ""),
        ("docker", "buildx", "version"): (0, version_answer("v0.37.0"), ""),
    }


def run_install(tmp_path: Path, events: Events, *, pin: Path | None = None,
                target: str | None = None, builder: str | None = BUILDER,
                driver: str | None = "docker-container", buildkit: str | None = None,
                lock: str | None = str(LOCK_PATH), answers: dict | None = None,
                content: bytes = ASSET) -> tuple[dict, int]:
    """Install with fetcher, hasher and runner all injected; return the facts and exit code."""
    pin = pin or make_pin(tmp_path, path=target)
    target = target or target_of(pin)
    argv = ["--pin", str(pin), "--path", target, "--facts", str(tmp_path / "install-facts.json")]
    if lock:
        argv += ["--lock", lock]
    if builder:
        argv += ["--builder", builder, "--driver", driver or "docker-container",
                 "--buildkit", buildkit or BUILDKIT["image_ref"]]
    code = installer.main(
        argv,
        fetch=lambda url, dest: events.fetch(url, dest, content),
        run=lambda cmd: events.run(cmd, answers if answers is not None
                                   else answers_for(target)),
        hash_file=events.hash)
    facts_path = tmp_path / "install-facts.json"
    return (json.loads(facts_path.read_text("utf-8")) if facts_path.exists() else {}), code


# -- the order, and what each step protects -------------------------------------------

def test_the_install_runs_in_the_order_that_makes_the_evidence_mean_something(
        tmp_path: Path) -> None:
    events = Events()
    facts, code = run_install(tmp_path, events)
    assert code == 0, facts
    assert facts["status"] == "INSTALLED"
    assert facts["steps_completed"] == ["pin", "path", "download", "integrity", "install",
                                        "read-back", "declaration", "builder",
                                        "plugin-diagnostic"]
    # Hashed before executed, executed before a builder is created, the runner's plugin asked last.
    assert events.order[:3] == ["fetch", "hash", "run:version"], events.order
    assert events.order.index("run:version") < events.order.index("run:create")
    assert events.order.index("run:create") < events.order.index("run:inspect")
    assert events.order[-1] == "run:docker-buildx-version", events.order
    assert facts["integrity"]["relation"] == "equal"
    assert facts["read_back"]["version"] == BUILDX["version"]
    assert facts["builder"]["inspected_driver"] == "docker-container"
    assert Path(facts["install"]["path"]).exists()
    assert (tmp_path / "install-facts.json").exists()
    assert facts["policy"]["refused_markers"] == ["cli-plugins"]


def test_a_verified_download_leaves_no_half_written_producer_at_the_target(tmp_path: Path) -> None:
    """The file is hashed where it landed, then moved -- so the target is only ever complete."""
    events = Events()
    facts, code = run_install(tmp_path, events)
    assert code == 0, facts
    assert not Path(facts["download"]["partial"]).exists()
    assert facts["install"]["replaced"] == facts["download"]["partial"]
    assert facts["install"]["path"] == facts["target"]


def test_a_checksum_mismatch_stops_before_the_downloaded_binary_is_executed(
        tmp_path: Path) -> None:
    """The approval's hard failure, and the reason the hash sits where it sits.

    Bytes that hash differently must never get the chance to answer `version` with the right
    string -- that is F-44's shape arriving by a different route.
    """
    events = Events()
    pin = make_pin(tmp_path)
    target = target_of(pin)
    facts, code = run_install(tmp_path, events, pin=pin, content=b"X" * len(ASSET))
    assert code == 1
    assert facts["status"] == "ERROR"
    assert facts["failed_step"] == "integrity"
    assert facts["steps_completed"] == ["pin", "path", "download"]
    assert events.commands == [], "a checksum mismatch must not execute anything at all"
    assert not Path(target).exists(), "the target path is written only after verification"
    assert Path(facts["integrity"]["of"]).exists(), (
        "the unverified file stays where it can be inspected: the .part path")
    assert facts["integrity"]["relation"] == "different"
    assert "never executed" in facts["error"]


def test_a_truncated_asset_fails_on_size_before_it_is_hashed(tmp_path: Path) -> None:
    """Two integrity checks in the cheap order: the pinned length first, then the digest."""
    events = Events()
    pin = make_pin(tmp_path, size=len(ASSET) + 1)
    facts, code = run_install(tmp_path, events, pin=pin)
    assert code == 1 and facts["failed_step"] == "integrity"
    assert events.order == ["fetch"], events.order
    assert "truncated" in facts["error"]


@pytest.mark.parametrize("path", [
    "/usr/libexec/docker/cli-plugins/docker-buildx",
    "/usr/local/lib/docker/cli-plugins/docker-buildx",
    "/usr/lib/docker/cli-plugins/docker-buildx",
    "/home/runner/.docker/cli-plugins/docker-buildx",
    "/opt/docker/cli-plugins/buildx",
    "/srv/tools/docker-buildx",
])
def test_a_target_inside_a_cli_plugin_directory_is_refused_before_anything_is_downloaded(
        tmp_path: Path, path: str) -> None:
    """Overwriting the runner's plugin would make the job a mutable machine again.

    Refused *before* the download: writing into one of these directories changes what the next
    `docker buildx` on that runner dispatches to, whether or not the bytes turn out to be right.

    Several of these paths exist on a CI runner -- that is the whole point of refusing them -- so
    the check is that the installer left whatever was there exactly as it found it. Asserting
    non-existence would measure the machine, not the behaviour, and would pass on a box with no
    docker install while failing on the runner this mechanism is for.
    """
    events = Events()
    before = _stat_of(path)
    pin = make_pin(tmp_path, path=path)
    facts, code = run_install(tmp_path, events, pin=pin, target=path)
    assert code == 1 and facts["failed_step"] == "path"
    assert events.order == [], "no download, no execution, no write"
    assert "plugin" in str(facts.get("error", "")).lower(), (
        f"refused, but not for the reason this test is about: {facts.get('error')!r}")
    assert _stat_of(path) == before, (
        f"{path} existed before ({before}) and the installer changed it")


def test_a_relative_target_is_refused(tmp_path: Path) -> None:
    events = Events()
    facts, code = run_install(tmp_path, events, target="buildx")
    assert code == 1 and facts["failed_step"] == "path"
    assert "absolute" in facts["error"]


def test_an_install_path_the_repository_did_not_declare_is_refused(tmp_path: Path) -> None:
    """Two files name the producer's path, and the installer will not pick one silently.

    `configured.workflow`'s literal is the authority the recorder compares, so installing
    somewhere else would leave a record observing a file the job never ran.
    """
    events = Events()
    pin = make_pin(tmp_path)
    elsewhere = str(tmp_path / "somewhere-else" / "buildx")
    facts, code = run_install(tmp_path, events, pin=pin, target=elsewhere)
    assert code == 1 and facts["failed_step"] == "pin"
    assert events.order == []
    assert "declares" in facts["error"]


# -- the read-back, and the builder it gates -------------------------------------------

def test_a_pinned_binary_answering_another_version_never_creates_a_builder(tmp_path: Path) -> None:
    events = Events()
    pin = make_pin(tmp_path)
    target = target_of(pin)
    answers = answers_for(target)
    answers[(target, "version")] = (0, version_answer("v0.36.0"), "")
    facts, code = run_install(tmp_path, events, pin=pin, answers=answers)
    assert code == 1 and facts["failed_step"] == "read-back"
    assert "run:create" not in events.order, (
        "a builder created by a binary that is not the pinned one is the runner again")
    assert facts["steps_completed"] == ["pin", "path", "download", "integrity", "install"]
    assert facts["read_back"]["version"] == "v0.36.0"


def test_a_pinned_binary_answering_another_commit_is_a_hard_failure(tmp_path: Path) -> None:
    events = Events()
    pin = make_pin(tmp_path)
    target = target_of(pin)
    answers = answers_for(target)
    answers[(target, "version")] = (0, version_answer(commit="d" * 40), "")
    facts, code = run_install(tmp_path, events, pin=pin, answers=answers)
    assert code == 1 and facts["failed_step"] == "read-back"
    assert facts["integrity"]["relation"] == "equal", (
        "the bytes are what the pin says and the commit is not: the pin's own derivation is what "
        "is wrong, and this is the failure that says so out loud")


def test_a_version_read_that_does_not_answer_is_a_failure(tmp_path: Path) -> None:
    events = Events()
    pin = make_pin(tmp_path)
    target = target_of(pin)
    answers = answers_for(target)
    answers[(target, "version")] = (1, "", "cannot execute binary file")
    facts, code = run_install(tmp_path, events, pin=pin, answers=answers)
    assert code == 1 and facts["failed_step"] == "read-back"
    assert "cannot execute" in facts["error"]


def test_a_builder_asked_to_run_an_unlocked_buildkit_is_refused(tmp_path: Path) -> None:
    """Two in-tree declarations that disagree stop the job before it spends five image builds.

    A measurement the machine can disagree with is evidence and stays evidence. A build whose own
    files already contradict each other is a defect, and B2 pinned a BuildKit that F-44 then found
    nobody had checked -- this is that gap closed at the earliest point that costs nothing.
    """
    events = Events()
    facts, code = run_install(tmp_path, events,
                              buildkit="moby/buildkit:v0.9.9@sha256:" + "0" * 64)
    assert code == 1 and facts["failed_step"] == "declaration"
    assert "run:create" not in events.order
    assert "locks" in facts["error"]


def test_a_builder_asked_without_a_lock_is_refused(tmp_path: Path) -> None:
    events = Events()
    facts, code = run_install(tmp_path, events, lock="")
    assert code == 1 and facts["failed_step"] == "declaration"
    assert "third-party-images.json" in facts["error"]


def test_a_builder_that_reports_another_driver_is_refused(tmp_path: Path) -> None:
    events = Events()
    pin = make_pin(tmp_path)
    target = target_of(pin)
    answers = answers_for(target)
    answers[(target, "inspect", "--bootstrap", BUILDER)] = (
        0, f"Name:          {BUILDER}\nDriver:        docker\n", "")
    facts, code = run_install(tmp_path, events, pin=pin, answers=answers)
    assert code == 1 and facts["failed_step"] == "builder"
    assert facts["builder"]["inspected_driver"] == "docker"


def test_a_builder_that_cannot_be_inspected_after_creation_is_refused(tmp_path: Path) -> None:
    events = Events()
    pin = make_pin(tmp_path)
    target = target_of(pin)
    answers = answers_for(target)
    del answers[(target, "inspect", "--bootstrap", BUILDER)]
    facts, code = run_install(tmp_path, events, pin=pin, answers=answers)
    assert code == 1 and facts["failed_step"] == "builder"
    assert "unexpected command" in facts["error"]


def test_a_create_that_fails_is_reported_with_its_complaint(tmp_path: Path) -> None:
    events = Events()
    pin = make_pin(tmp_path)
    target = target_of(pin)
    answers = answers_for(target)
    create_key = next(key for key in answers if "create" in key)
    answers[create_key] = (1, "", "error: driver docker-container not found")
    facts, code = run_install(tmp_path, events, pin=pin, answers=answers)
    assert code == 1 and facts["failed_step"] == "builder"
    assert "not found" in facts["error"]


# -- the diagnostic that must never become the decision ---------------------------------

def test_the_runner_plugin_answer_never_fails_the_install(tmp_path: Path) -> None:
    """F-44 stays visible without becoming a second gate inside the installer.

    What `docker buildx` would have dispatched to is recorded. Blocking on it would let a
    machine's missing plugin look like the reason a release failed, which is a different question
    than the one this mechanism answers.
    """
    events = Events()
    pin = make_pin(tmp_path)
    target = target_of(pin)
    answers = answers_for(target)
    answers[("docker", "buildx", "version")] = (
        1, "", "docker: 'buildx' is not a docker command")
    facts, code = run_install(tmp_path, events, pin=pin, answers=answers)
    assert code == 0, facts
    assert facts["status"] == "INSTALLED"
    assert facts["runner_plugin"]["status"] == "ERROR"
    assert facts["runner_plugin"]["note"].startswith("diagnostic only")


def test_a_plugin_diagnostic_that_cannot_even_be_attempted_is_recorded(tmp_path: Path) -> None:
    """No docker binary on the PATH is a fact about the machine, not a crash in the tool."""
    events = Events()
    pin = make_pin(tmp_path)
    target = target_of(pin)
    answers = answers_for(target)

    def run(argv: list[str]) -> tuple[int, str, str]:
        if argv[0] == "docker":
            raise OSError("No such file or directory: 'docker'")
        return events.run(argv, answers)

    facts = installer.install(str(pin), target, builder=BUILDER, driver="docker-container",
                              buildkit=BUILDKIT["image_ref"], lock_path=str(LOCK_PATH),
                              fetch=lambda url, dest: events.fetch(url, dest),
                              hash_file=events.hash, run=run)
    assert facts["status"] == "INSTALLED"
    assert facts["runner_plugin"]["status"] == "ERROR"
    assert "No such file" in facts["runner_plugin"]["error"]


# -- the pin that ships ----------------------------------------------------------------

def test_the_shipped_pin_is_readable_and_points_at_a_legal_target() -> None:
    """The real `controlled_buildx.json`, through the loader the installer actually uses.

    Everything above uses a throwaway pin so it can be untrue on purpose. This test checks the
    file that ships: it loads, it agrees with the release lock on the version, and the path it
    declares is one the mechanism will accept.
    """
    facts: dict = {}
    pin = installer.load_pin(str(PIN_PATH), facts)
    assert pin["version"] == BUILDX["version"] == "v0.37.1"
    assert pin["expected_git_commit"] == PIN["expected_git_commit"] == FAKE_COMMIT
    assert pin["expected_sha256"] == PIN["integrity"]["expected"]
    assert pin["asset_size_bytes"] == PIN["source"]["asset_size_bytes"]
    assert pin["download_url"].startswith("https://")
    assert facts == {}
    assert installer.refusal_reason(pin["declared_path"]) is None, (
        "the shipped pin names a target the mechanism would refuse")
    assert Path(pin["declared_path"]).name == "buildx"


def test_a_pin_missing_what_the_mechanism_needs_is_an_error_not_a_default(tmp_path: Path) -> None:
    events = Events()
    pin = make_pin(tmp_path)
    doc = json.loads(pin.read_text("utf-8"))
    doc["integrity"]["expected"] = ""
    pin.write_text(json.dumps(doc), encoding="utf-8")
    facts, code = run_install(tmp_path, events, pin=pin)
    assert code == 1 and facts["failed_step"] == "pin"
    assert events.order == []
    assert "expected_sha256" in facts["error"]


def test_an_http_pin_url_is_refused(tmp_path: Path) -> None:
    events = Events()
    pin = make_pin(tmp_path, url="http://example.invalid/buildx")
    facts, code = run_install(tmp_path, events, pin=pin)
    assert code == 1 and facts["failed_step"] == "pin"
    assert "https" in facts["error"]


# -- one mechanism, not a copy in every job --------------------------------------------

def job_steps(doc: dict, job_name: str) -> list[str]:
    job = (doc.get("jobs") or {}).get(job_name) or {}
    return [str(step.get("run", "")) for step in job.get("steps", [])]


def test_the_observation_job_installs_through_the_shared_mechanism() -> None:
    """A2.1's job carried the recipe inline; the shared mechanism replaces that copy.

    The assertions that guarded the inline copy (hash before execute, never a plugin path) now live
    in the behaviour tests above, which is why this checks the call and the absence of a second
    implementation rather than repeating them.
    """
    steps = job_steps(CI, "producer-observation")
    script = "\n".join(steps)
    assert MECHANISM in script, "the observation job still installs its own copy"
    for forbidden in ("curl ", "wget ", "sha256sum --check", "| sh ", "| bash ",
                      "cli-plugins"):
        assert forbidden not in script, f"{forbidden!r} is a second install implementation"
    install_step = next(index for index, step in enumerate(steps) if MECHANISM in step)
    build_step = next(index for index, step in enumerate(steps) if "record_and_run" in step)
    assert install_step < build_step, "a build that runs before the install verifies nothing"


def test_the_installer_and_the_recorder_disagree_about_nothing_they_both_read() -> None:
    """One definition of the pin's shape, the version line and the plugin directories.

    The installer borrows `load_controlled_pin`, `parse_version_line` and `PLUGIN_DIRS` from the
    recorder rather than restating them, so the tool that installs the producer and the tool that
    later measures it cannot drift into describing two different files.
    """
    assert installer.recorder is recorder, "the installer restated the recorder's definitions"
    home = "/home/runner"
    plugin = str(Path(recorder.PLUGIN_DIRS[2].format(home=home)) / "docker-buildx")
    assert installer.refusal_reason(plugin) is not None
    assert installer.plugin_paths(), "the refusal has nothing to check against"
