"""B2: the builder is pinned, and what actually ran is recorded separately from what was asked.

Every test here is either a read of the real workflow/lock/Dockerfile text (a pin that exists
but disagrees with the lock is the bug B2 is fixing), or the recorder driven with a stub
command runner -- so the *recorded* values are compared against something other than themselves.
No build runs here, and no part of the recorder's logic is stubbed: only `docker` is.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "release"))

import record_build_producer as recorder  # noqa: E402

LOCK = json.loads((PROJECT_ROOT / "deployment/third-party-images.json").read_text("utf-8"))
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
BUILDX_ENTRY = next(b for b in LOCK["binaries"] if b["name"] == "buildx")
BUILDKIT = next(e for e in LOCK["images"] if e["name"] == "buildkit-buildkit")
FRONTEND = next(e for e in LOCK["images"] if e["name"] == "dockerfile-1-frontend")
EVIDENCE = json.loads((PROJECT_ROOT / "docs/quality/artifacts/registry-resolution"
                       / "third-party-registries.json").read_text("utf-8"))


def claims_by_repository() -> dict[str, dict]:
    return {claim["repository"]: claim for claim in EVIDENCE["images"]}


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text("utf-8"))


def steps(named: str) -> list[dict]:
    return [step for job in workflow(named)["jobs"].values() for step in job.get("steps", [])]


# -- the pins, read where they are written ------------------------------------

SHA_PIN = re.compile(r"^(docker/(?:setup-buildx|login)-action)@([0-9a-f]{40})(?:\s+#\s*(\S+))?$")


def test_the_builder_actions_are_referenced_by_full_sha_and_nothing_loose() -> None:
    """A tag ref moves when somebody else pushes it; a commit ref cannot.

    The version comment lives in the YAML, where parsing erases it, so the SHA comes
    from the parsed step and the comment is read from the file's own lines: `@<40
    hex>` is the machine's pin, `# v3.x.y` is the reader's.
    """
    rows = []
    for name in ("ci.yml", "release.yml"):
        for step in steps(name):
            uses = str(step.get("uses", ""))
            if uses.startswith("docker/setup-buildx-action") or uses.startswith(
                    "docker/login-action"):
                rows.append((name, uses))
    assert rows, "no buildx/login steps found -- this check would pass vacuously"
    unpinned = [(name, uses) for name, uses in rows if not SHA_PIN.match(uses)]
    assert not unpinned, f"these refs move when somebody else tags: {unpinned}"
    pinned = {uses.split("@")[0]: SHA_PIN.match(uses).group(2) for _, uses in rows}
    assert pinned["docker/setup-buildx-action"] == (
        "8d2750c68a42422c14e847fe6c8ac0403b4cbd6f"), pinned
    assert pinned["docker/login-action"] == (
        "c94ce9fb468520275223c153574b00df6fe4bcc9"), pinned

    versioned = re.compile(
        r"uses: docker/(?:setup-buildx|login)-action@[0-9a-f]{40}\s+#\s\S+")
    for name in ("ci.yml", "release.yml"):
        text = (WORKFLOWS / name).read_text("utf-8")
        pinned_in_text = text.count("docker/setup-buildx-action@") + text.count(
            "docker/login-action@")
        assert pinned_in_text == len(versioned.findall(text)), (
            f"{name}: a SHA-pinned builder step lost the version it is a SHA for")


def test_every_setup_step_declares_the_pinned_buildx_and_buildkit() -> None:
    """The action's own default is not a pin: a bump to the action moves the CLI with it."""
    setups = [(step.get("with") or {}) for _, uses, step in
              ((n, u, s) for n in ("ci.yml", "release.yml") for u, s in
               ((str(x.get("uses", "")), x) for x in steps(n)) if "buildx-action" in u)]
    assert setups, "no setup-buildx steps"
    for config in setups:
        assert config.get("buildx-version") == BUILDX_ENTRY["version"], config
        assert config.get("driver-opts") == f"image={BUILDKIT['image_ref']}", (
            f"the driver runs {config.get('driver-opts')!r} while the lock records "
            f"{BUILDKIT['image_ref']!r}")


def test_buildkit_pin_matches_the_version_the_pinned_buildx_vendored() -> None:
    """A BuildKit chosen independently of the CLI would be a guess beside a fact."""
    assert BUILDKIT["tag"] in BUILDKIT["why_this_version"]
    assert BUILDX_ENTRY["version"] in BUILDKIT["why_this_version"]
    claim = claims_by_repository()["moby/buildkit"]
    assert claim["index_digest"] == BUILDKIT["digest"], "the lock and the evidence disagree"
    assert BUILDKIT["tag"] == "v0.33.0", (
        "the tag moved without the pairing note being re-read from buildx's go.mod")


def test_only_the_dockerfiles_that_already_named_a_frontend_still_do() -> None:
    """§G.2.4: pinning an existing directive is B2; inventing one elsewhere is another change.

    A `# syntax=` line decides which code parses the Dockerfile, so adding one to the three
    images that never had it would silently change how their layers are built.
    """
    with_directive = sorted(
        str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        for path in PROJECT_ROOT.rglob("Dockerfile")
        if "_tmp" not in path.parts and "# syntax=" in path.read_text("utf-8")
    )
    assert with_directive == sorted(FRONTEND["referenced_by"]), with_directive
    claim = claims_by_repository()["docker/dockerfile"]
    assert claim["index_digest"] == FRONTEND["digest"]
    for rel in with_directive:
        text = (PROJECT_ROOT / rel).read_text("utf-8")
        line = next(line for line in text.splitlines() if line.startswith("# syntax="))
        assert line == f"# syntax={FRONTEND['image_ref']}", (
            f"{rel}: {line!r} is not the locked frontend coordinate")


def test_the_build_script_records_a_producer_it_did_not_write() -> None:
    script = (PROJECT_ROOT / "scripts/release/build_release_image.sh").read_text("utf-8")
    assert "record_build_producer.py" in script, "the producer is never read"
    assert '"producer"' in script, "the recorder's output is not embedded in the evidence"
    assert '{"recorded": False}' in script, (
        "a failed recorder has to be visible in the evidence, not absent from it")
    # The recorder runs *before* the evidence is written, and the build has already
    # happened by then: a recorder crash must not be the reason a good build fails.
    assert script.index("record_build_producer.py") < script.index('evidence = {')


# -- the recorder, driven with a stubbed `docker` ------------------------------

@pytest.fixture
def stub_runner():
    """A `docker` that answers, with values chosen to differ from the lock's."""

    def build(version: str = "v0.99.9-observed", builder_image: str = "moby/buildkit:other",
              image_id: str = "sha256:" + "ee" * 32, inspect_fails: bool = False):
        answers = {
            ("docker", "buildx", "version", "--format", "{{.Version}}"): (0, version),
            ("docker", "buildx", "inspect", "--format",
             "{{.Driver}} {{.Name}}"): (0, "docker-container cap-builder"),
            ("docker", "inspect", "--format", "{{.Config.Image}}\t{{.Image}}",
             "buildx_buildkit_cap-builder"): (
                0 if not inspect_fails else 1,
                "" if inspect_fails else f"{builder_image}\t{image_id}"),
            ("docker", "version", "--format", "{{.Server.Version}}"): (0, "28.3.0"),
        }

        def run(argv: list[str]) -> tuple[int, str]:
            key = tuple(argv)
            if key in answers:
                return answers[key]
            return 1, f"unexpected command: {key}"

        return run

    return build


def recorded_payload(tmp_path: Path, run) -> dict:
    out = tmp_path / "producer.json"
    code = recorder.main(["--out", str(out), "--lock",
                          str(PROJECT_ROOT / "deployment/third-party-images.json")],
                         run=run, environ={"RUNNER_ENVIRONMENT": "GitHub-ACTIONS",
                                           "ImageOS": "ubuntu2404"})
    assert code == 0
    return json.loads(out.read_text("utf-8"))


def test_observed_values_come_from_the_tool_not_from_the_pin(tmp_path: Path,
                                                             stub_runner) -> None:
    """The whole point of B2's recording half: two different places, two different answers.

    If `observed` were a restatement of `configured`, this test could not tell a build that
    ran the pinned BuildKit from one that ran something else entirely.
    """
    payload = recorded_payload(tmp_path, stub_runner())
    assert payload["configured"]["buildkit_image"] == BUILDKIT["image_ref"]
    assert payload["observed"]["buildx_version"]["value"] == "v0.99.9-observed"
    assert payload["observed"]["builder"]["image_id"] == "sha256:" + "ee" * 32
    assert payload["observed"]["builder"]["reference"] == "moby/buildkit:other"
    assert payload["configured"]["buildkit_image"] != payload["observed"]["builder"]["reference"]
    assert payload["runner"]["runner_environment"] == "GitHub-ACTIONS"


def test_a_mismatch_between_pin_and_run_is_visible(tmp_path: Path, stub_runner) -> None:
    """Nothing here asserts they agree -- the release reviewer decides that, from the record."""
    payload = recorded_payload(tmp_path, stub_runner(builder_image="moby/buildkit:v0.20.0"))
    pinned = payload["configured"]["buildkit_image"]
    ran = payload["observed"]["builder"]["reference"]
    assert not pinned.endswith(ran), "the fixture stopped being a mismatch"


def test_a_command_that_does_not_answer_is_recorded_as_such(tmp_path: Path,
                                                            stub_runner) -> None:
    payload = recorded_payload(tmp_path, stub_runner(inspect_fails=True))
    builder = payload["observed"]["builder"]
    assert builder["ok"] is False and builder["error"], builder
    assert "incomplete" in payload, "an unread producer must be named, not left implicit"
    assert "builder" in payload["incomplete"] or "buildx_version" in payload["incomplete"]


def test_the_docker_driver_says_there_is_no_buildkit_container(tmp_path: Path) -> None:
    """`--local-docker` really has no BuildKit image to read; that is stated, not faked."""

    def run(argv: list[str]) -> tuple[int, str]:
        if argv[:3] == ["docker", "buildx", "version"]:
            return 0, "v0.37.1"
        if "--format" in argv and "{{.Driver}}" in " ".join(argv):
            return 0, "docker default"
        if "buildx_buildkit_default" in argv:
            return 1, "No such object"
        if argv[:2] == ["docker", "version"]:
            return 0, "28.3.0"
        return 1, f"unexpected: {argv}"

    payload = recorded_payload(tmp_path, run)
    builder = payload["observed"]["builder"]
    assert builder["ok"] is False
    assert "embedded BuildKit" in builder["reason"], builder
    assert "incomplete" not in payload, (
        f"a stated absence is not a read failure: {payload.get('incomplete')}")


def test_run_command_reports_instead_of_raising(tmp_path: Path) -> None:
    """A missing binary is a recorded fact; the build must not die behind the recorder."""
    code, out = recorder.run_command([str(tmp_path / "no-such-binary"), "--version"])
    assert code != 0
    assert "FileNotFoundError" in out, (
        f"a missing binary has to read as a failed command, not as silence: {out!r}")
