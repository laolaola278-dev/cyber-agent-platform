"""BATCH 3 A2.2 Stage 2: the release build runs the producer this repository pins.

`tests/test_release_build_script.py` already runs `build_release_image.sh` against a stubbed
docker; this file adds what A2.2 changed about that run -- the executable that builds, the argv
recorded before it runs, the arguments handed to the producer recorder, what happens when any of
those is missing or unreadable, and where the sidecar files are allowed to live.

Two things are checked here that a workflow-text grep cannot: that the command line the release
publishes *images* on is the pinned executable with the named builder, and that the script treats
a broken producer record as a failed build. Both were verified on a real runner at A2.1 for the
observation path; A2.2 extends them to the publishing path, and a 20-minute CI round trip is a
poor place to discover that a flag was misspelled.

The recorder itself is not under test in these files: it runs here as a stand-in that logs its
argv and returns an exit code, while its real behaviour against a whole machine is exercised by
`test_producer_observation_contract.py`. What is checked is the seam between them -- including
that every option the script passes is an option the shipped recorder actually declares, because
a stub would otherwise accept `--controled-pin` without complaint.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.test_release_build_script import (
    BUILDER,
    FAKE_INDEX_DIGEST,
    RELEASE_JOB,
    REVISION,
    _posix,
    buildx_calls,
    evidence,
    recorder_argv,
    run_build,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECORDED_SOURCE = (PROJECT_ROOT / "scripts/release/record_build_producer.py").read_text("utf-8")
#: Every long option the shipped recorder declares. The script builds this argv by hand, and a
#: typo in it is invisible to a stub that accepts anything.
RECORDED_OPTIONS = set(re.findall(r'add_argument\(["\'](--[a-z][a-z-]*)', RECORDED_SOURCE))


def test_the_release_build_command_is_the_controlled_executable_with_its_builder(
        tmp_path: Path) -> None:
    """The build line, in the order A2.2 asked for it: `<abs path> build --builder <name>`.

    `--builder` is what makes the named, pinned builder the one that builds, and the absolute path
    is what keeps the CLI plugin out of the producer's seat. Both halves are read from the log of
    the executable that ran, not from the script's own text.
    """
    proc = run_build(tmp_path)
    doc = evidence(proc)
    calls = buildx_calls(proc)
    first = calls.splitlines()[0]
    assert first.startswith("buildx build --builder " + BUILDER), first
    assert BUILDER in first
    assert doc["builder_named"] == BUILDER
    assert Path(proc.buildx_path).name == "buildx", proc.buildx_path
    assert doc["workflow"] == ".github/workflows/release.yml"
    assert doc["workflow_job"] == RELEASE_JOB
    assert doc["producer_record_exit"] == 0
    assert doc["producer"]["recorded"] is True
    assert "buildx" not in _docker_calls(proc)


def test_the_argv_is_written_before_the_build_and_patched_with_its_exit_after(
        tmp_path: Path) -> None:
    """The ordering that makes the invocation claim falsifiable.

    A file written after the build could name any argv the script liked. The stub copies it at the
    moment the build starts, so what is proved here is that the argv -- including the executable
    path -- was already on disk, and that the exit code arrived only afterwards.
    """
    proc = run_build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    command_path: Path = proc.build_command_path
    at_build = json.loads(Path(str(command_path) + ".at-build").read_text("utf-8"))
    assert "missing" not in at_build, "the build ran before its command was recorded"
    assert at_build["exit"] is None, "the exit code was written before the build ran"
    assert at_build["image"] == "cap-probe"
    assert at_build["argv"][0] == proc.buildx_path, at_build["argv"]
    assert at_build["argv"][1] == "build"
    assert BUILDER in at_build["argv"]
    after = json.loads(command_path.read_text("utf-8"))
    assert after["argv"] == at_build["argv"], "the record of the command changed after the fact"
    assert after["exit"] == 0, after


def test_a_build_that_failed_is_recorded_as_a_failure_of_the_build(tmp_path: Path) -> None:
    """A non-zero buildx exit is evidence with an exit code, and the job goes red."""
    proc = run_build(tmp_path, buildx_exit="7")
    assert proc.returncode == 1, proc.stdout[-2000:]
    after = json.loads(proc.build_command_path.read_text("utf-8"))
    assert after["exit"] == 7, after
    assert not proc.evidence_path.exists(), "a build that did not succeed writes no evidence"


def test_the_recorder_is_asked_for_this_build_and_only_with_real_options(
        tmp_path: Path) -> None:
    """The argv the script hands the recorder: complete, and made of options that exist.

    The stand-in would accept a misspelled flag without complaint, which is how a producer record
    can silently lose half its inputs. Checking the flags against the shipped recorder's own parser
    is what closes that.
    """
    proc = run_build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    argv = recorder_argv(proc)
    assert argv, "the script never invoked the producer recorder"
    flags = {token for token in argv if token.startswith("--")}
    unknown = sorted(flags - RECORDED_OPTIONS)
    assert not unknown, f"the script passes options the recorder does not declare: {unknown}"
    for needed in ("--self-check", "--builder", "--controlled-pin", "--workflow", "--job",
                   "--image", "--revision", "--build-command", "--dockerfile", "--lock", "--out"):
        assert needed in flags, f"the producer record is asked without {needed}"
    assert argv[argv.index("--builder") + 1] == BUILDER
    assert argv[argv.index("--controlled-pin") + 1] == "scripts/release/controlled_buildx.json"
    assert argv[argv.index("--job") + 1] == RELEASE_JOB
    assert argv[argv.index("--revision") + 1] == REVISION
    assert "--docker-cli-build" not in flags, "this build did not run through the docker driver"


@pytest.mark.parametrize("missing", ["--buildx", "--builder", "--workflow", "--job"])
def test_an_unnamed_producer_refuses_the_release_build(tmp_path: Path, missing: str) -> None:
    """No fallback to the runner: forgetting to name the producer stops the build.

    Each of the four carries one of the record's authorities -- the executable, the builder, the
    file that declares them, and the job inside it. Drop any one and a third of the evidence would
    be the recorder agreeing with a default.
    """
    argv = ["--buildx", "C:/somewhere/buildx", "--builder", BUILDER,
            "--workflow", ".github/workflows/release.yml", "--job", RELEASE_JOB]
    index = argv.index(missing)
    del argv[index:index + 2]
    proc = run_build(tmp_path, *argv, producer=False)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2, combined[-1500:]
    assert "required unless --local-docker" in combined, combined[-800:]
    assert not proc.evidence_path.exists()


@pytest.mark.parametrize("path, fragment", [
    ("buildx", "absolute"),
    ("docker", "absolute"),
    ("relative/dir/buildx", "absolute"),
    ("/usr/libexec/docker/cli-plugins/docker-buildx", "plugin path"),
    ("/opt/docker/cli-plugins/buildx", "plugin path"),
])
def test_a_buildx_that_is_not_an_installed_executable_is_refused(
        tmp_path: Path, path: str, fragment: str) -> None:
    """A name on PATH and a plugin directory both let the runner decide again."""
    proc = run_build(tmp_path, "--buildx", path, "--builder", BUILDER,
                     "--workflow", ".github/workflows/release.yml", "--job", RELEASE_JOB,
                     producer=False)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2, combined[-1500:]
    assert fragment in combined, combined[-800:]


def test_a_buildx_path_that_is_not_executable_points_at_the_installer(tmp_path: Path) -> None:
    proc = run_build(tmp_path, "--buildx", "/tmp/not-installed-here/buildx",
                     "--builder", BUILDER, "--workflow", ".github/workflows/release.yml",
                     "--job", RELEASE_JOB, producer=False)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined[-1500:]
    assert "install_controlled_buildx.py" in combined, combined[-800:]


def test_a_broken_producer_record_fails_the_release_build_after_writing_evidence(
        tmp_path: Path) -> None:
    """Unreadable evidence is not a pass, and it is not a mystery either.

    The exit comes *after* the evidence and the recorder's log are on disk: the release is blocked
    and an operator can still read which read was lost.
    """
    proc = run_build(tmp_path, recorder_exit="1")
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined[-1500:]
    assert "producer could not be fully recorded" in combined, combined[-800:]
    doc = json.loads(proc.evidence_path.read_text("utf-8"))
    assert doc["producer"]["recorded"] is False
    assert doc["producer_record_exit"] == 1
    assert doc["index_digest"] == FAKE_INDEX_DIGEST, "the build itself did succeed"
    sidecar_dir = proc.evidence_path.parent / "producer"
    assert (sidecar_dir / "evidence-repo.json.producer.log").exists(), sorted(
        path.name for path in sidecar_dir.iterdir())


def test_the_developer_docker_path_warns_about_a_record_it_cannot_complete(
        tmp_path: Path) -> None:
    """`--local-docker` claims no pinned producer, so it is not held to that claim.

    A developer's own build stays a build; a release image's does not. This is the one place the
    two paths differ, and the assertion is that the difference is the *producer claim*, not one
    build being measured more loosely than the other.
    """
    proc = run_build(tmp_path, "--local-docker", recorder_exit="1")
    assert proc.returncode == 0, proc.stderr[-2000:]
    doc = evidence(proc)
    assert doc["build_driver"] == "docker"
    assert doc["producer"]["recorded"] is False
    argv = recorder_argv(proc)
    assert "--docker-cli-build" in argv
    assert "--builder" not in argv


def test_the_producer_sidecars_stay_out_of_the_directory_the_gate_globs(
        tmp_path: Path) -> None:
    """One image, one `*.json` where the completeness gate looks.

    `release-image-completeness` reads every `*.json` in the evidence directory as an image
    record. A producer sidecar with an `image` field in that same directory either replaces the
    real record or appears as an image the release never declared -- both fail the gate for a
    reason that has nothing to do with the images. This is F-48's fix, stated as a rule.
    """
    proc = run_build(tmp_path)
    assert proc.returncode == 0, proc.stderr
    evidence_dir = proc.evidence_path.parent
    assert [path.name for path in sorted(evidence_dir.glob("*.json"))] == ["evidence-repo.json"]
    sidecars = sorted(path.name for path in (evidence_dir / "producer").iterdir())
    assert sidecars == ["evidence-repo.json.build-command.json",
                        "evidence-repo.json.build-command.json.at-build",
                        "evidence-repo.json.producer.json",
                        "evidence-repo.json.producer.log"], sidecars


def test_an_oci_export_replaces_the_load_and_says_which_archive_it_wrote(
        tmp_path: Path) -> None:
    """The dry path's base hand-off: bytes to a file, not to a store nothing can see.

    A `docker-container` builder cannot read the host docker store, so an image another build
    layers on has to leave this one as an archive. The record names the archive, and does not
    claim a platform it could not read.
    """
    archive = tmp_path / "base-image.oci.tar"
    proc = run_build(tmp_path, "--oci-out", _posix(archive))
    doc = evidence(proc)
    calls = buildx_calls(proc)
    assert f"--output type=oci,dest={_posix(archive)}" in calls, calls
    assert "--load" not in calls, "an archive export and a store load are two different outputs"
    assert doc["oci_archive"] == _posix(archive)
    assert doc["platform"] is None, "nothing was loaded, so no store entry names a platform"
    assert "--oci-tar" in recorder_argv(proc)


def _docker_calls(proc) -> str:
    path: Path = proc.calls_path
    return path.read_text("utf-8") if path.exists() else ""
