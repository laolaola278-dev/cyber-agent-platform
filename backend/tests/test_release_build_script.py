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
                {"digest": FAKE_AMD64_CHILD, "platform": {"os": "linux", "architecture": "amd64"}},
                {"digest": "sha256:" + "12" * 32,
                 "platform": {"os": "linux", "architecture": "arm64"}},
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


def _repo(
    tmp_path: Path, script_text: str | None = None, dockerfile: str = PROBE_DOCKERFILE
) -> Path:
    """A repository-shaped directory: the layout the script resolves around.

    `REPO_ROOT` comes out of the script's own location, so the copy has to sit
    the same depth below the root that the real one does.
    """
    root = tmp_path / "repo"
    (root / "scripts" / "release").mkdir(parents=True)
    script = root / "scripts" / "release" / "build_release_image.sh"
    text = BUILD_SCRIPT.read_text("utf-8") if script_text is None else script_text
    script.write_text(text, encoding="utf-8", newline="\n")
    image_dir = root / "probe"
    image_dir.mkdir()
    (image_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8", newline="\n")
    (image_dir / "app.py").write_text("print('x')\n", encoding="utf-8", newline="\n")
    return root


BuildResult = subprocess.CompletedProcess


def run_build(
    tmp_path: Path,
    *extra: str,
    script_text: str | None = None,
    dockerfile: str = PROBE_DOCKERFILE,
) -> BuildResult:
    root = _repo(tmp_path, script_text, dockerfile)
    env = dict(os.environ, **_fake_bin(tmp_path / "fakebin"))
    out = tmp_path / "evidence.json"
    cmd = [
        bash_exe(),
        _posix(root / "scripts" / "release" / "build_release_image.sh"),
        "--name", "cap-probe",
        "--dockerfile", "probe/Dockerfile",
        "--context", "probe",
        "--version", "1.0.6-rc1",
        "--revision", "0123456789abcdef0123456789abcdef01234567",
        "--out", _posix(out),
        *extra,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=_posix(root))
    proc.evidence_path = out  # type: ignore[attr-defined]
    proc.calls_path = Path(env["FAKE_DOCKER_CALLS"])  # type: ignore[attr-defined]
    return proc


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
    assert doc["tag"] == "1.0.6-rc1" and doc["image"] == "cap-probe"
    assert doc["base_refs"] == ["example/base:1.2.3@sha256:" + "11" * 32], (
        "the resolved base, including an ARG default, is what makes the build replayable"
    )
    calls = docker_calls(proc)
    assert "--load" in calls and "--provenance=false" in calls, calls
    assert "--push" not in calls, "a dry build must not push"


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
    assert doc["attestations"] == {"sbom": True, "provenance": True}
    calls = docker_calls(proc)
    assert "--provenance=true --sbom=true --push" in calls, calls
    assert "--load" not in calls, "buildx refuses to load an attested build"
    assert "imagetools inspect" in calls, "the child digest comes from the registry"


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
    calls = docker_calls(proc)
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
