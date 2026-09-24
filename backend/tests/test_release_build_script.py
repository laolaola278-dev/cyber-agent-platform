"""The release build script is executed here, against a stubbed docker.

`build_release_image.sh` is the one piece of the release graph that is not
declarative YAML: it chooses a build driver, decides whether attestations are
attached, and writes the evidence file that `release-image-completeness` then
refuses or accepts. CI proves it works on a clean runner; that answer arrives
twenty minutes after a push, and it arrived as a crash this time:

    scripts/release/build_release_image.sh: line 127: PLATFORM_DIGEST: unbound variable

Two of the five release images build through that path in `release.yml`, so the
same crash would have stopped a real release, and the string-level checks that
passed alongside it were not worth anything. Running the script with a fake
`docker` costs seconds, works where Docker does not (this repository is
developed on Windows), and makes the claim testable: for each driver, the
evidence file has to carry the fields the publication gate requires, and it has
to say what actually happened rather than what the flags were configured to do.

The last test is the control that keeps the rest honest: an earlier copy of the
script with one default deleted must fail here the way it failed in CI.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "release" / "build_release_image.sh"

REQUIRED_EVIDENCE_FIELDS = (
    "image",
    "ref",
    "registry",
    "repository",
    "tag",
    "pushed",
    "index_digest",
    "config_digest",
    "platform_digest_linux_amd64",
    "platform",
    "build_driver",
    "dockerfile",
    "dockerfile_sha256",
    "context_sha256",
    "base_refs",
    "source_revision",
    "attestations",
)

#: What the stub registry serves for the image this harness builds.
FAKE_INDEX_DIGEST = "sha256:" + "ab" * 32
FAKE_CONFIG_DIGEST = "sha256:" + "cd" * 32
FAKE_AMD64_CHILD = "sha256:" + "ef" * 32

_DOCKER_SHIM = r"""#!/usr/bin/env bash
# Fake enough docker for build_release_image.sh: records every invocation and
# answers the three queries its evidence block depends on.
printf 'docker %s\n' "$*" >> "${FAKE_DOCKER_CALLS:-/dev/null}"
sub="$1 $2 $3"
case "$sub" in
  "buildx build "*|"buildx build")
    prev=""
    for arg in "$@"; do
      if [[ "$prev" == "--metadata-file" ]]; then
        printf '{"containerimage.digest":"%s","containerimage.config.digest":"%s"}\n' \
          "__INDEX__" "__CONFIG__" > "$arg"
      fi
      prev="$arg"
    done
    ;;
  "docker buildx imagetools"|"buildx imagetools inspect")
    printf '%s\n' '__MANIFEST__'
    ;;
  "docker image inspect"|"image inspect "*)
    format=""
    prev=""
    for arg in "$@"; do
      if [[ "$prev" == "--format" ]]; then format="$arg"; fi
      prev="$arg"
    done
    case "$format" in
      *".Id"*) echo "__CONFIG__" ;;
      *".Os"*|*Architecture*) echo "linux/amd64" ;;
      *) echo "sha256:0000" ;;
    esac
    ;;
esac
exit 0
"""


def bash_exe() -> str:
    """A real bash, not the Windows `system32\bash.exe` WSL launcher."""
    candidates: list[Path] = []
    program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
    candidates.append(Path(program_files) / "Git" / "bin" / "bash.exe")
    found = shutil.which("bash") or ""
    if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate).replace("\\", "/")
    raise AssertionError(f"no usable bash among {candidates}")


def _posix(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def _fake_bin(dest: Path) -> dict[str, str]:
    """A PATH entry whose `docker` is a stub and whose `python3` is this interpreter."""
    dest.mkdir(parents=True, exist_ok=True)
    manifest = json.dumps(
        {
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {"digest": "sha256:" + "12" * 32,
                 "platform": {"os": "linux", "architecture": "arm64"}},
                {"digest": FAKE_AMD64_CHILD, "platform": {"os": "linux", "architecture": "amd64"}},
            ],
        }
    )
    docker = dest / "docker"
    docker.write_text(
        _DOCKER_SHIM.replace("__INDEX__", FAKE_INDEX_DIGEST)
        .replace("__CONFIG__", FAKE_CONFIG_DIGEST)
        .replace("__MANIFEST__", manifest),
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(docker, 0o755)
    python = dest / "python3"
    python.write_text(
        f'#!/usr/bin/env bash\n"{_posix(sys.executable)}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(python, 0o755)
    return {
        "PATH": _posix(dest) + os.pathsep + os.environ.get("PATH", ""),
        "FAKE_DOCKER_CALLS": _posix(dest / "calls.txt"),
    }


PROBE_DOCKERFILE = (
    "ARG BASEIMAGE=example/base:1.2.3@sha256:" + "11" * 32 + "\n"
    "FROM ${BASEIMAGE}\nRUN echo hello\n"
)

#: A buildx stub: it writes the metadata file a real buildx writes, answers the one registry read
#: the push path makes, and records every invocation. `--buildx` is the *path under test* here --
#: what matters is which file the script runs and with what argv, not that the bytes build.
_BUILDX_SHIM = r"""#!/usr/bin/env bash
printf 'buildx %s\n' "$*" >> "${FAKE_BUILDX_CALLS:-/dev/null}"
if [[ "$1" == "build" ]]; then
  # Snapshot the build-command file *as this build starts*: if the script wrote the argv after
  # running instead of before, the snapshot would be missing or already carry an exit code.
  if [[ -n "${CAP_EXPECTED_BUILD_COMMAND:-}" ]]; then
    if [[ -f "$CAP_EXPECTED_BUILD_COMMAND" ]]; then
      cp "$CAP_EXPECTED_BUILD_COMMAND" "$CAP_EXPECTED_BUILD_COMMAND.at-build"
    else
      printf '{"missing": true}\n' > "$CAP_EXPECTED_BUILD_COMMAND.at-build"
    fi
  fi
  prev=""
  for arg in "$@"; do
    if [[ "$prev" == "--metadata-file" ]]; then
      printf '{"containerimage.digest":"%s","containerimage.config.digest":"%s"}\n' \
        "__INDEX__" "__CONFIG__" > "$arg"
    fi
    prev="$arg"
  done
  if [[ -n "${FAKE_BUILDX_EXIT:-}" ]]; then exit "$FAKE_BUILDX_EXIT"; fi
  exit 0
fi
if [[ "$*" == *"--format"* ]]; then printf '%s\n' '__MANIFEST__'; exit 0; fi
exit 0
"""

#: What stands in for the recorder inside the fake repository. The real recorder has its own
#: machine fixture (`test_producer_observation_contract.py`); what is under test here is the
#: script's wiring -- which arguments it hands over, and what it does with the exit code it gets
#: back. A test cross-checks those arguments against the real recorder's own option list.
_RECORDER_STUB = r"""#!/usr/bin/env python3
import json
import os
import sys

argv = sys.argv[1:]
log = os.environ.get("RECORDER_ARGV_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(argv) + "\n")
out = argv[argv.index("--out") + 1] if "--out" in argv else None
code = int(os.environ.get("RECORDER_EXIT") or 0)
if out:
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"stub": True, "recorded": code == 0, "argv": argv}, handle)
print("stub recorder saw " + str(len(argv)) + " arguments")
sys.exit(code)
"""

#: The release job's own producer declarations, in the shape `configured.workflow` reads: literals
#  in the job's `env:` block, with the setup action's pins beside them.
def fake_workflow(buildx_path: str, builder: str) -> str:
    return f"""name: fake release
on: push
jobs:
  release-images:
    env:
      CAP_RELEASE_BUILDER: {builder}
      CAP_RELEASE_DRIVER: docker-container
      CAP_RELEASE_BUILDKIT: moby/buildkit:v0.33.0@sha256:{"6" * 64}
      CAP_RELEASE_BUILDX_PIN: scripts/release/controlled_buildx.json
      CAP_RELEASE_BUILDX_PATH: {buildx_path}
    steps:
      - uses: docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f
        with:
          buildx-version: v0.37.1
          driver-opts: image=moby/buildkit:v0.33.0@sha256:{"6" * 64}
      - run: echo build
"""


def _repo(
    tmp_path: Path, script_text: str | None = None, dockerfile: str = PROBE_DOCKERFILE
) -> Path:
    """A repository-shaped directory: the layout the script resolves around.

    `REPO_ROOT` comes out of the script's own location, so the copy has to sit
    the same depth below the root that the real one does.
    """
    root = tmp_path / "repo"
    release_dir = root / "scripts" / "release"
    release_dir.mkdir(parents=True)
    text = BUILD_SCRIPT.read_text("utf-8") if script_text is None else script_text
    (release_dir / "build_release_image.sh").write_text(text, encoding="utf-8", newline="\n")
    # The files a controlled build insists on reading, in the shape the real ones have, and a
    # stand-in recorder whose argv and exit code the tests can observe.
    shutil.copyfile(PROJECT_ROOT / "scripts/release/controlled_buildx.json",
                    release_dir / "controlled_buildx.json")
    (root / "deployment").mkdir(exist_ok=True)
    shutil.copyfile(PROJECT_ROOT / "deployment/third-party-images.json",
                    root / "deployment" / "third-party-images.json")
    (release_dir / "record_build_producer.py").write_text(_RECORDER_STUB, encoding="utf-8",
                                                          newline="\n")
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    image_dir = root / "probe"
    image_dir.mkdir()
    (image_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8", newline="\n")
    (image_dir / "app.py").write_text("print('x')\n", encoding="utf-8", newline="\n")
    return root


def fake_buildx(root: Path) -> Path:
    """The executable a release build is pointed at, inside the fake repository."""
    path = root / "controlled-buildx" / "buildx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _BUILDX_SHIM.replace("__INDEX__", FAKE_INDEX_DIGEST)
        .replace("__CONFIG__", FAKE_CONFIG_DIGEST)
        .replace("__MANIFEST__", json.dumps(
            {"manifests": [
                {"digest": FAKE_AMD64_CHILD,
                 "platform": {"os": "linux", "architecture": "amd64"}}]})),
        encoding="utf-8", newline="\n")
    os.chmod(path, 0o755)
    return path


RELEASE_JOB = "release-images"
#: The commit every harness build claims to be building, and the value a producer record has to
#: carry back for the release gate to believe the record belongs to this run.
REVISION = "0123456789abcdef0123456789abcdef01234567"
#: The name the fake release job declares in its own env: block and builds with.
BUILDER = "cap-test-release-producer"


BuildResult = subprocess.CompletedProcess


def run_build(
    tmp_path: Path,
    *extra: str,
    script_text: str | None = None,
    dockerfile: str = PROBE_DOCKERFILE,
    where: str = "repo",
    mutate: Callable[[Path], None] | None = None,
    producer: bool = True,
    buildx_path: str | None = None,
    builder: str = BUILDER,
    buildx_exit: str | None = None,
    recorder_exit: str | None = None,
) -> BuildResult:
    root = _repo(tmp_path / where, script_text, dockerfile)
    if mutate is not None:
        mutate(root)
    env = dict(os.environ, **_fake_bin(tmp_path / f"fakebin-{where}"))
    installed = fake_buildx(root)
    buildx = _posix(installed) if buildx_path is None else buildx_path
    if producer:
        (root / ".github" / "workflows" / "release.yml").write_text(
            fake_workflow(buildx, builder), encoding="utf-8", newline="\n")
    env["FAKE_BUILDX_CALLS"] = _posix(tmp_path / f"buildx-calls-{where}.txt")
    env["RECORDER_ARGV_LOG"] = _posix(tmp_path / f"recorder-argv-{where}.jsonl")
    if buildx_exit:
        env["FAKE_BUILDX_EXIT"] = buildx_exit
    if recorder_exit:
        env["RECORDER_EXIT"] = recorder_exit
    out = tmp_path / f"evidence-{where}.json"
    build_command = out.parent / "producer" / (out.name + ".build-command.json")
    env["CAP_EXPECTED_BUILD_COMMAND"] = _posix(build_command)
    cmd = [
        bash_exe(),
        _posix(root / "scripts" / "release" / "build_release_image.sh"),
        "--name", "cap-probe",
        "--dockerfile", "probe/Dockerfile",
        "--context", "probe",
        "--version", "1.0.6-rc1",
        "--revision", REVISION,
        "--out", _posix(out),
    ]
    if producer:
        cmd += ["--buildx", buildx, "--builder", builder,
                "--workflow", ".github/workflows/release.yml", "--job", RELEASE_JOB]
    cmd += list(extra)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=_posix(root))
    proc.evidence_path = out  # type: ignore[attr-defined]
    proc.calls_path = Path(env["FAKE_DOCKER_CALLS"])  # type: ignore[attr-defined]
    proc.buildx_path = buildx  # type: ignore[attr-defined]
    proc.buildx_calls_path = Path(env["FAKE_BUILDX_CALLS"])  # type: ignore[attr-defined]
    proc.recorder_log_path = Path(env["RECORDER_ARGV_LOG"])  # type: ignore[attr-defined]
    proc.build_command_path = build_command  # type: ignore[attr-defined]
    proc.repo_root = root  # type: ignore[attr-defined]
    return proc


def buildx_calls(proc: subprocess.CompletedProcess) -> str:
    path: Path = proc.buildx_calls_path
    return path.read_text("utf-8") if path.exists() else ""


def recorder_argv(proc: subprocess.CompletedProcess) -> list[str]:
    """The arguments the script handed the recorder, from the last invocation it logged."""
    path: Path = proc.recorder_log_path
    if not path.exists():
        return []
    lines = [line for line in path.read_text("utf-8").splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else []


def evidence(proc: subprocess.CompletedProcess) -> dict:
    assert proc.returncode == 0, (
        f"build_release_image.sh exited {proc.returncode}\n"
        f"STDOUT:\n{proc.stdout[-4000:]}\nSTDERR:\n{proc.stderr[-4000:]}"
    )
    return json.loads(proc.evidence_path.read_text("utf-8"))


def docker_calls(proc: subprocess.CompletedProcess) -> str:
    return proc.calls_path.read_text("utf-8") if proc.calls_path.exists() else ""


def test_the_buildx_dry_build_writes_complete_evidence(tmp_path: Path) -> None:
    """The path `release.yml` uses, minus the push.

    This is the invocation that crashed in CI after a successful build, because
    the evidence block read a variable only the other driver assigned.
    """
    proc = run_build(tmp_path)
    doc = evidence(proc)
    for field in REQUIRED_EVIDENCE_FIELDS:
        assert field in doc, f"evidence is missing {field!r}: {sorted(doc)}"
    assert doc["build_driver"] == "buildx", doc
    assert doc["index_digest"] == FAKE_INDEX_DIGEST, (
        "a buildx build must record the digest the registry would serve"
    )
    assert doc["pushed"] is False
    assert doc["attestations"] == {"sbom": False, "provenance": False}, (
        "a dry build has no attestation subject; claiming one would let CI "
        "evidence satisfy the publication gate"
    )
    assert doc["platform_digest_linux_amd64"] is None, "nothing was pushed to inspect"
    assert doc["platform"] == "linux/amd64", (
        "a dry buildx build loads into the store, so its platform is knowable; the "
        "docker-driver record states it and a reader compares the two side by side"
    )
    assert doc["tag"] == "1.0.6-rc1" and doc["image"] == "cap-probe"
    assert doc["base_refs"] == ["example/base:1.2.3@sha256:" + "11" * 32], (
        "the resolved base, including an ARG default, is what makes the build replayable"
    )
    calls = buildx_calls(proc)
    assert "--load" in calls and "--provenance=false" in calls, calls
    assert "--push" not in calls, "a dry build must not push"
    assert "buildx build" not in docker_calls(proc), (
        "the build ran through the controlled executable; `docker buildx` is F-44's dispatch")
    docker = docker_calls(proc)
    assert "--load" not in docker, "the load is a buildx flag; docker must not be building"


def test_the_push_build_attests_and_records_the_platform_child(tmp_path: Path) -> None:
    """ARTIFACT-GATE 6/7/8 executed, not read off a workflow file."""
    proc = run_build(tmp_path, "--push", "--registry", "ghcr.io/cap-owner")
    doc = evidence(proc)
    assert doc["pushed"] is True
    assert doc["ref"] == "ghcr.io/cap-owner/cap-probe:1.0.6-rc1"
    assert doc["registry"] == "ghcr.io/cap-owner"
    assert doc["index_digest"] == FAKE_INDEX_DIGEST
    assert doc["platform_digest_linux_amd64"] == FAKE_AMD64_CHILD, (
        "the amd64 child has to be read from the pushed manifest, not guessed later"
    )
    assert doc["platform"] == "linux/amd64", (
        "the record names the platform whose child digest it claims; the served "
        "manifest also holds an arm64 entry, so a wrong pick is invisible otherwise"
    )
    assert doc["attestations"] == {"sbom": True, "provenance": True}
    calls = buildx_calls(proc)
    assert "--provenance=true --sbom=true --push" in calls, calls
    assert "--load" not in calls, "buildx refuses to load an attested build"
    assert "imagetools inspect" in calls, "the child digest comes from the registry"
    assert "buildx" not in docker_calls(proc), (
        "the docker CLI is not asked to build or to read the registry on this path:\n"
        + docker_calls(proc))


def test_the_local_driver_build_is_labelled_as_such(tmp_path: Path) -> None:
    """CI's browser build: the docker driver is the only one that sees the store."""
    proc = run_build(tmp_path, "--local-docker")
    doc = evidence(proc)
    assert doc["build_driver"] == "docker", doc
    assert doc["platform"] == "linux/amd64", doc
    assert doc["config_digest"] == FAKE_CONFIG_DIGEST, "the local image id is the identity here"
    assert doc["index_digest"] is None, (
        "no registry served these bytes, so no index digest exists"
    )
    assert doc["attestations"] == {"sbom": False, "provenance": False}
    calls = docker_calls(proc)
    assert re.search(r"^docker build ", calls, re.MULTILINE), calls
    assert "buildx build" not in calls, calls
    assert "--push" not in calls, calls


#: The browser image's real shape: an ARG with no default, so the caller has to
#: name the base and the evidence has to carry the name it used.
NO_DEFAULT_BASE = "FROM ${SANDBOX_HTTP_BASE}\nRUN echo layered\n"


def test_a_named_base_is_recorded_verbatim(tmp_path: Path) -> None:
    """ARTIFACT-GATE 9's evidence side: the released browser image states its base.

    `release.yml` hands the browser build
    `ghcr.io/<owner>/cap-sandbox-http@sha256:<index digest>`. If the evidence
    kept the `${SANDBOX_HTTP_BASE}` placeholder instead, nothing downstream could
    tell which HTTP sandbox bytes the shipped image extends -- and that is the
    question F-7 made unanswerable for the three images it never published.
    """
    base = "ghcr.io/cap-owner/cap-sandbox-http@sha256:" + "ab" * 32
    proc = run_build(
        tmp_path, "--push", "--registry", "ghcr.io/cap-owner",
        "--build-arg", f"SANDBOX_HTTP_BASE={base}", dockerfile=NO_DEFAULT_BASE,
    )
    doc = evidence(proc)
    assert doc["base_refs"] == [base], doc["base_refs"]
    calls = buildx_calls(proc)
    assert f"--build-arg SANDBOX_HTTP_BASE={base}" in calls, calls


def test_an_unresolvable_base_refuses_the_build(tmp_path: Path) -> None:
    """No caller, no default: the build stops instead of recording an empty base.

    The old behaviour was a warning line and `base_refs: []` -- an image that
    built fine and could not say what it was built on, which is the same blind
    spot as `FROM cap-sandbox-http:latest` wearing a different hat.
    """
    proc = run_build(tmp_path, dockerfile=NO_DEFAULT_BASE)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"a build with an unresolvable base reported success\n{combined[-1500:]}"
    )
    assert "SANDBOX_HTTP_BASE" in combined, combined[-800:]
    assert not proc.evidence_path.exists(), "no evidence for a build that never ran"


def test_a_missing_required_argument_is_refused(tmp_path: Path) -> None:
    """The harness reports failure as failure -- it is not always green."""
    proc = run_build(tmp_path, "--version", "")
    assert proc.returncode != 0
    assert "required" in (proc.stdout + proc.stderr).lower()
    assert not proc.evidence_path.exists(), "a refused build writes no evidence"


def test_the_harness_notices_an_uninitialised_evidence_variable(tmp_path: Path) -> None:
    """Regression control: the exact CI failure, reproduced on an older script.

    `PLATFORM_DIGEST=""` is what the fix added. Without it, `set -u` kills the
    buildx path *after* the image has built, which is why a passing build and a
    passing string-level test still produced a red pipeline.
    """
    shipped = BUILD_SCRIPT.read_text("utf-8")
    mutated = re.sub(r'^PLATFORM_DIGEST=""$', "", shipped, count=1, flags=re.MULTILINE)
    assert mutated != shipped, "the default this control removes is no longer where it was"
    proc = run_build(tmp_path, script_text=mutated)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        "the harness cannot detect an unbound variable -- these tests would "
        "have passed on the commit CI broke on"
    )
    assert "PLATFORM_DIGEST" in combined and "unbound" in combined, combined[-2000:]


#: Each entry is (the line the shipped script has, the same line with one flag
#: taken out) for the attestation the release gate requires.
ATTESTATION_MUTATIONS = {
    "provenance": (
        "    BUILD_ARGS+=(--provenance=true --sbom=true --push)",
        "    BUILD_ARGS+=(--sbom=true --push)",
    ),
    "sbom": (
        "    BUILD_ARGS+=(--provenance=true --sbom=true --push)",
        "    BUILD_ARGS+=(--provenance=true --push)",
    ),
}


@pytest.mark.parametrize("attestation", sorted(ATTESTATION_MUTATIONS))
def test_attestations_are_reported_from_the_build_that_ran(
    attestation: str, tmp_path: Path
) -> None:
    """Flip one flag out of the push build and watch the record change.

    `attestations` has to be derived from the command line, not from the intent
    of the workflow -- otherwise removing `--sbom=true` from release.yml would
    still produce evidence that satisfies the publication gate.
    """
    shipped = BUILD_SCRIPT.read_text("utf-8")
    old, new = ATTESTATION_MUTATIONS[attestation]
    assert old in shipped, f"{old!r} is no longer in the build script"
    proc = run_build(tmp_path, "--push", "--registry", "ghcr.io/o",
                     script_text=shipped.replace(old, new))
    doc = evidence(proc)
    assert doc["attestations"][attestation] is False, (
        f"--{attestation}=true was removed from the build but the evidence still "
        f"claims it: {doc['attestations']}"
    )
    other = "sbom" if attestation == "provenance" else "provenance"
    assert doc["attestations"][other] is True, "the other attestation was still requested"
    assert f"--{attestation}" not in docker_calls(proc).replace("--push", ""), (
        "the flag should be gone from the command the stub recorded"
    )


def test_a_build_without_a_metadata_file_records_no_digest(tmp_path: Path) -> None:
    """The digest comes from the build; nothing invents it.

    Dropping `--metadata-file` makes the buildx branch unable to answer, and
    the correct outcome is a failed build, not an evidence file that guesses.
    """
    shipped = BUILD_SCRIPT.read_text("utf-8")
    mutated = shipped.replace('--metadata-file "$META" ', "")
    assert mutated != shipped, "the metadata flag moved"
    proc = run_build(tmp_path, script_text=mutated)
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        "a build that cannot resolve its digest has to say so: "
        f"evidence was written anyway\n{combined[-1500:]}"
    )


def test_the_context_hash_is_about_bytes_not_directories(tmp_path: Path) -> None:
    """Two builds of the same staged content must agree. CI could not make them.

    Run 35500219964 recorded `cap-sandbox-http`'s context as 8310b248… in one
    matrix cell and febcb187… in another, at the same commit, because the hash
    covered `sha256sum`'s output *including the absolute path* of a fresh
    `mktemp -d`. The field §4 advertises as "what makes a difference visible"
    was recording which temporary directory the run happened to get, so a real
    change to a staged file was indistinguishable from a rebuild.
    """
    first = evidence(run_build(tmp_path, where="one"))
    second = evidence(run_build(tmp_path, where="two"))
    assert first["context_sha256"] == second["context_sha256"], (
        f"the same staged content hashed to {first['context_sha256']} and "
        f"{second['context_sha256']}"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", first["context_sha256"]), first


def test_the_context_hash_moves_when_a_staged_file_changes(tmp_path: Path) -> None:
    """The other half of the claim: stable is not the same as meaningful."""
    before = evidence(run_build(tmp_path, where="before"))["context_sha256"]

    def change(root: Path) -> None:
        (root / "probe" / "app.py").write_text("print('changed')\n", encoding="utf-8")

    after = evidence(run_build(tmp_path, where="after", mutate=change))["context_sha256"]
    assert before != after, (
        "editing a file in the build context did not change context_sha256"
    )
