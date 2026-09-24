"""Batch 3A/3A2.1: the producer *observation* contract -- authorities kept apart, controls.

B2's recorder pinned a builder and read one version line back. Batch 3A separated the
producer into the things that can disagree, and kept them apart in the record:

    configured.lock        deployment/third-party-images.json
    configured.workflow    what the workflow file itself declares, read from the file
    configured.controlled  scripts/release/controlled_buildx.json (A2.1)
    observed.*             what the runner, the CLI, the builder and its container answer

A2.1 adds the half 3A could only measure: an executable the repository chooses, verifies by
checksum and *runs*, so the producer stops being whichever plugin the runner happens to have.
Two rules from 3A survive unchanged and one is sharpened:

* A **mismatch is evidence, never a failure.** `--self-check` exits non-zero only when a field
  the observation *promised* could not be read -- a broken instrument -- and a genuine version
  mismatch still exits 0.
* **An unreadable value is never a match.** `null` must not read as agreement, so every
  unknown side carries a status and a reason.
* **The binary that built is what gets scored.** The runner's `docker buildx` answer stays in
  the record -- unscored, visible -- because "the pinned executable ran the build" is only a
  finding if what the runner *would* have used is still on the page.

Only `docker`, `sha256sum` and the controlled binary are stubbed. No recorder logic is stubbed,
and no test here asserts that the pins agree -- they assert what the record *says* about
whether they agree.
"""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "release"))

import record_build_producer as recorder  # noqa: E402

LOCK_PATH = PROJECT_ROOT / "deployment/third-party-images.json"
LOCK = json.loads(LOCK_PATH.read_text("utf-8"))
BUILDKIT = next(e for e in LOCK["images"] if e["name"] == "buildkit-buildkit")
BUILDX = next(b for b in LOCK["binaries"] if b["name"] == "buildx")
CI_PATH = PROJECT_ROOT / ".github/workflows/ci.yml"
CI = yaml.safe_load(CI_PATH.read_text("utf-8"))
OBSERVATION_JOB = "producer-observation"
# The tests read the repository's own controlled-executable pin rather than inventing one, so a
# bumped buildx version or a replaced asset digest fails here as a disagreement with the file
# that ships, which is the only kind of failure that would also matter in CI.
PIN_PATH = PROJECT_ROOT / "scripts/release/controlled_buildx.json"
PIN = json.loads(PIN_PATH.read_text("utf-8"))
CONTROLLED = PIN["install"]["path"]
CONTROLLED_SHA_HEX = PIN["integrity"]["expected"].split(":", 1)[1]
CONTROLLED_COMMIT = PIN["expected_git_commit"]

BUILDER = "cap3a-producer-observation"
CONTAINER = f"buildx_buildkit_{BUILDER}"          # named after the *builder*
NODE = f"{BUILDER}0"                              # the first node is called <builder>0
HOME = "/home/runner"


def plugin_path(template_index: int) -> str:
    """One of the recorder's own plugin candidates, computed the way it computes them.

    Spelling these out as literals would key the stub against POSIX paths only, and on a
    Windows machine the recorder asks about `\\home\\runner\\…` -- which the stub would answer
    "unexpected command", turning an installed plugin into a missing one in the test record.
    """
    return str(Path(recorder.PLUGIN_DIRS[template_index].format(home=HOME)) / "docker-buildx")


ALL_PLUGINS = tuple(plugin_path(index) for index in range(len(recorder.PLUGIN_DIRS)))
ACTION_PLUGIN = ALL_PLUGINS[0]                    # where setup-buildx-action installs
SYSTEM_PLUGIN = ALL_PLUGINS[2]                    # where the runner image ships one
MISSING_PLUGIN = ALL_PLUGINS[3]
PATH_PLUGIN = "/usr/local/bin/docker-buildx"  # the docker CLI resolves plugins here too
CI_COMMIT = "ac30b249211430b85fb8f37b6e7154b5c47ba0b6"  # what the runner's line reported
CHILD = "sha256:" + "ab" * 32                      # a platform child of the pinned index
ATTESTATION = "sha256:" + "cd" * 32                # the index's attestation entry
CONFIG_ID = "sha256:" + "ee" * 32                  # what `.Image` reports
FOREIGN = "sha256:" + "ff" * 32                    # some other buildx build's bytes

OBSERVE_ARGS = ["--mode", "observe", "--builder", BUILDER, "--workflow", str(CI_PATH),
                "--job", OBSERVATION_JOB, "--controlled-pin", str(PIN_PATH)]


def version_line(path: str, version: str, commit: str = "62ab6b42a64bcf84f225559ff1015b98") -> str:
    """The shape `docker buildx version` prints on a GitHub runner."""
    return f"{path} github.com/docker/buildx {version} {commit}"


def inspect_text(driver: str = "docker-container", image: str | None = None,
                 worker: str | None = None, name: str | None = None) -> str:
    node_image = BUILDKIT["image_ref"] if image is None else image
    builder = BUILDER if name is None else name
    head = (f"Name:          {builder}\n"
            f"Driver:        {driver}\n"
            "Last Activity: 2026-09-23 05:00:00 +0000 UTC\n\nNodes:\n")
    node = (f"Name:      {builder}0\n"
            "Endpoint:  unix:///var/run/docker.sock\n"
            "Status:    running\n")
    if worker:
        node += f"org.mobyproject.buildkit.worker.hostname: {worker}\n"
    node += "Buildkit:  v0.33.0\n"
    if node_image:
        node += f"Image:     {node_image}\n"
    return head + node


def index_json(children: tuple[str, ...] = (CHILD,)) -> str:
    """A registry answer for the pinned BuildKit reference: an index, not one manifest."""
    manifests = [{"digest": digest, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                  "platform": {"os": "linux", "architecture": "amd64"}} for digest in children]
    manifests.append({"digest": ATTESTATION,
                      "mediaType": "application/vnd.oci.image.manifest.v1+json",
                      "platform": {"os": "unknown", "architecture": "unknown"},
                      "annotations": {"vnd.docker.reference.type": "attestation-manifest",
                                      "vnd.docker.reference.digest": children[0]}})
    return json.dumps({"schemaVersion": 2,
                       "mediaType": "application/vnd.oci.image.index.v1+json",
                       "manifests": manifests})


def child_manifest_json(config_digest: str) -> str:
    """What the registry serves for one platform child: a manifest naming its own config."""
    return json.dumps({"schemaVersion": 2,
                       "mediaType": "application/vnd.oci.image.manifest.v1+json",
                       "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                                  "digest": config_digest, "size": 7023},
                       "layers": [{"digest": "sha256:" + "11" * 32, "size": 100}]})


PS_FORMAT = "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.State}}"


def observe_run(version: str = BUILDX["version"], path: str = SYSTEM_PLUGIN,
                driver: str = "docker-container", inspect_image: str | None = None,
                repo_digests: list[str] | None = None, image_id: str = CONFIG_ID,
                builder_exists: bool = True, plugins: dict[str, str] | None = None,
                container_exists: bool = True, server: str = "28.0.4",
                index: str | None = None, dead_daemon: bool = False,
                inspect_output: str | None = None, path_plugin: str | None = None,
                container_rows: list[str] | None = None, worker: str | None = None,
                platform: str | None = "linux/amd64", child_config: str | None = None,
                child_read_fails: bool = False, controlled_exists: bool = True,
                controlled_version: str | None = None, controlled_commit: str | None = None,
                controlled_sha: str | None = None, controlled_path: str | None = None,
                controlled_reports_path: str | None = None):
    """A runner that answers every command the observation path issues.

    Answers are keyed by the exact argv, which makes the fixture a regression guard on the
    commands themselves: `docker buildx inspect --format`, a `docker inspect` that asks
    for a config image and a repo digest in one call, and a `docker inspect` of a
    *container* for `RepoDigests` -- an image-object field, which the daemon therefore
    refuses to template -- each broke an implementation, and each is visible here as an
    "unexpected command".

    The controlled executable of A2.1 is here too, and the two are deliberately *separate*
    knobs: `version`/`path` describe what `docker buildx` dispatches to on the runner, while
    `controlled_*` describe the binary the repository pinned. Once the pinned one answers, the
    recorder asks the builder and the registry through it, so those argv keys exist under the
    controlled prefix -- and the runner's own plugin can still disagree with everything, which
    is the F-44 state the record has to keep showing.
    """
    controlled_path = controlled_path or CONTROLLED
    repo_digests = [f"moby/buildkit@{CHILD}"] if repo_digests is None else repo_digests
    child_config = CONFIG_ID if child_config is None else child_config
    plugins = {ACTION_PLUGIN: BUILDX["version"], SYSTEM_PLUGIN: version} if plugins is None \
        else plugins
    rows = container_rows if container_rows is not None else [
        "\t".join(("abc123def456", CONTAINER, BUILDKIT["image_ref"], "running"))]
    listed = rows[0].split("\t")[1] if rows else CONTAINER
    # Which prefix the instrument will use: the controlled binary once it answers, `docker
    # buildx` otherwise. Mirroring the recorder's own rule here is what lets a test assert that
    # a *missing* pinned executable degrades the reads instead of faking them.
    bx = ((controlled_path,) if controlled_exists and controlled_version != "missing"
          else ("docker", "buildx"))
    answers: dict[tuple[str, ...], tuple[int, str, str]] = {
        ("docker", "buildx", "version"): (0, version_line(path, version), ""),
        (*bx, "ls"): (0, "\n".join((
            "NAME/NODE  DRIVER/ENDPOINT    STATUS   BUILDKIT  PLATFORMS",
            f"{BUILDER}*  {driver}  running  v0.33.0   linux/amd64",
            "default    docker                        ")), ""),
        (*bx, "inspect", f"--builder={BUILDER}"): (
            0 if builder_exists else 1,
            (inspect_output if inspect_output is not None
             else inspect_text(driver, inspect_image, worker)) if builder_exists else "",
            "" if builder_exists else f'ERROR: no builder "{BUILDER}" found'),
        ("docker", "ps", "-a", "--filter", "name=buildx_buildkit_", "--format", PS_FORMAT): (
            0, "\n".join(rows), ""),
        ("docker", "inspect", "--format", "{{.Config.Image}}\t{{.Image}}", listed): (
            0 if container_exists else 1,
            f"{BUILDKIT['image_ref']}\t{image_id}" if container_exists else "",
            "" if container_exists else f"Error: No such object: {listed}"),
        ("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id): (
            0, json.dumps(repo_digests), ""),
        ("docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image_id): (
            (0, platform, "") if platform else (1, "", "Error: No such image: " + image_id)),
        (*bx, "imagetools", "inspect", BUILDKIT["image_ref"], "--raw"): (
            0, index if index is not None else index_json(), ""),
        ("docker", "version", "--format", "{{.Server.Version}}"): (0, server, ""),
        ("which", "docker"): (0, "/usr/bin/docker", ""),
        ("which", "docker-buildx"): (
            (0, PATH_PLUGIN, "") if path_plugin else (1, "", "docker-buildx not found in path")),
    }
    if controlled_exists and controlled_version != "missing":
        # What CI measured at run 35866400091: a standalone buildx prints
        # `github.com/docker/buildx v0.37.1 <commit>` and no install path. The fixture defaults
        # to that shape on purpose -- an earlier version of it printed the path the recorder had
        # just invoked, which is how the design came to require an answer no runner gives.
        reported = (version_line(controlled_reports_path, controlled_version or BUILDX["version"],
                                 controlled_commit or CONTROLLED_COMMIT)
                    if controlled_reports_path else
                    version_line("", controlled_version or BUILDX["version"],
                                 controlled_commit or CONTROLLED_COMMIT).lstrip())
        answers[(controlled_path, "version")] = (0, reported, "")
        answers[("sha256sum", controlled_path)] = (
            0, f"{controlled_sha or CONTROLLED_SHA_HEX}  {controlled_path}", "")
    else:
        answers[(controlled_path, "version")] = (1, "", "no such file or directory")
        answers[("sha256sum", controlled_path)] = (1, "", "No such file or directory")
    served = index if index is not None else index_json()
    try:
        named = [item["digest"] for item in json.loads(served).get("manifests", [])
                 if (item.get("platform") or {}).get("architecture") == "amd64"]
    except (json.JSONDecodeError, AttributeError, KeyError):
        named = []
    if len(named) == 1:
        coordinate = f"moby/buildkit@{named[0]}"
        answers[(*bx, "imagetools", "inspect", coordinate, "--raw")] = (
            (1, "", "ERROR: failed to resolve manifest for " + coordinate) if child_read_fails
            else (0, child_manifest_json(child_config), ""))
    if path_plugin:
        answers[(PATH_PLUGIN, "version")] = (0, version_line(PATH_PLUGIN, path_plugin), "")
    if dead_daemon:
        # A binary can still answer `version` with no daemon; every `docker …` read cannot,
        # and that is the case where a comparison has nothing left to compare.
        complaint = "ERROR: failed to connect to the docker API at unix:///var/run/docker.sock"
        for key in list(answers):
            if key[0] == "docker":
                answers[key] = (1, "", complaint)
            elif key[0] == controlled_path and key[1:] == ("inspect", f"--builder={BUILDER}"):
                answers[key] = (1, "", complaint)
    for plugin_path_value in ALL_PLUGINS:
        if plugin_path_value in plugins:
            answers[(plugin_path_value, "version")] = (
                0, version_line(plugin_path_value, plugins[plugin_path_value]), "")
        else:
            answers.setdefault((plugin_path_value, "version"),
                               (1, "", "no such file or directory"))
    seen: list[tuple[str, ...]] = []

    def run(argv: list[str]) -> tuple[int, str, str]:
        key = tuple(argv)
        seen.append(key)
        if key in answers:
            return answers[key]
        return 2, "", f"unexpected command: {key}"

    run.seen = seen  # type: ignore[attr-defined]
    return run


def recorded(tmp_path: Path, run, *extra: str, environ=None,
             build_argv: list[str] | None = None, build_exit: int = 0,
             image: str = "cap-backend", dockerfile: str | None = None) -> tuple[int, dict]:
    """Run the recorder's observation path over a stubbed machine and read the record back.

    The build-command file is written here rather than assumed: A2.1's claim that the pinned
    executable built the image is only as good as the record of what was invoked, so every test
    passes through the same artifact the CI job will write.
    """
    out = tmp_path / "producer-evidence.json"
    command = tmp_path / "build-command.json"
    build_list = build_argv if build_argv is not None else [
        CONTROLLED, "build", "--builder", BUILDER, "--file", "backend/Dockerfile", "backend"]
    command.write_text(json.dumps({"image": image, "argv": build_list, "exit": build_exit}),
                       "utf-8")
    argv = ["--out", str(out), "--lock", str(LOCK_PATH), *OBSERVE_ARGS,
            "--image", image, "--build-command", str(command)]
    if dockerfile:
        argv += ["--dockerfile", dockerfile]
    argv += list(extra)
    code = recorder.main(argv, run=run,
                         environ=environ if environ is not None else {"HOME": HOME,
                                                                      "RUNNER_ENVIRONMENT":
                                                                      "GitHub-ACTIONS",
                                                                      "ImageOS": "ubuntu2404",
                                                                      "GITHUB_SHA": "abc123"})
    return code, json.loads(out.read_text("utf-8"))


def statuses(payload: dict) -> dict:
    return {name: payload["comparison"][name]["status"]
            for name in ("lock_vs_workflow", "controlled_pin_vs_lock", "workflow_vs_observed",
                         "lock_vs_observed")}


# -- configured.workflow: read from the file, or it is not a second authority -------------

def test_the_observation_job_declares_the_locked_pins_in_its_own_file() -> None:
    """The builder name, driver and BuildKit reference come from the YAML, not from argv.

    `builder_name=None` on purpose: if the recorder needed the command line to know which
    builder the job names, `configured.workflow` would be a restatement of the invocation
    rather than a claim about the repository -- and the comparison would be the recorder
    agreeing with itself.
    """
    workflow = recorder.configured_workflow_values(str(CI_PATH), OBSERVATION_JOB, None)
    assert workflow["ok"] is True and workflow["status"] == "READ", workflow
    assert workflow["builder_name"] == BUILDER, "the job's builder is not readable from the file"
    assert workflow["driver"] == "docker-container"
    assert workflow["buildkit_image"] == BUILDKIT["image_ref"]
    assert workflow["buildkit_digest"] == BUILDKIT["digest"]
    assert workflow["buildx_version"] == BUILDX["version"]
    assert workflow["controlled_buildx_path"] == CONTROLLED, (
        "A2.1's producer is the executable the job names in its own env: block")
    assert workflow["controlled_pin_file"] == "scripts/release/controlled_buildx.json"
    assert workflow["declared_env_keys"] == ["CAP_OBSERVE_BUILDER", "CAP_OBSERVE_BUILDKIT",
                                             "CAP_OBSERVE_BUILDX_PATH", "CAP_OBSERVE_BUILDX_PIN",
                                             "CAP_OBSERVE_DRIVER"]
    assert "expressions" not in workflow, (
        f"an unexpanded ${{{{ }}}} is not a literal pin: {workflow.get('expressions')}")


def test_the_workflow_and_the_controlled_pin_name_the_same_executable() -> None:
    """Two files declare the producer's path, so the two have to be checked against each other.

    `configured.workflow` says where the job installs it; the pin file says the same thing. If
    they ever name different paths the job would install one binary and observe another, and
    the agreement is cheap to assert here rather than expensive to notice in CI.
    """
    workflow = recorder.configured_workflow_values(str(CI_PATH), OBSERVATION_JOB, None)
    pin = recorder.load_controlled_pin(str(PIN_PATH))
    assert pin["ok"] is True, pin
    assert workflow["controlled_buildx_path"] == pin["declared_path"], (
        f"the job installs {workflow['controlled_buildx_path']!r} but the pin says "
        f"{pin['declared_path']!r}")
    assert workflow["controlled_pin_file"] == "scripts/release/controlled_buildx.json", (
        workflow["controlled_pin_file"])
    assert (PROJECT_ROOT / workflow["controlled_pin_file"]).is_file(), (
        "the job installs from a pin file the repository does not carry")
    assert BUILDX["version"] == pin["version"], (
        "the release lock and the controlled pin name different buildx versions")


def test_a_digest_only_reference_reports_no_tag_rather_than_a_recycled_name() -> None:
    """The bytes a container pulled are named `repo@sha256:…`, and `RepoDigests` has no tag.

    Read from the real shapes: `moby/buildkit@sha256:ab…` used to come back with
    `tag: "moby/buildkit"`, because `rpartition(":")` returns the whole string in its third
    field when there is no colon. A tag that is really a repository name is the same class of
    error as a null read as a match -- the record states something that is not so.
    """
    both = recorder._ref_parts("moby/buildkit:v0.33.0@sha256:" + "6c" * 32)
    assert (both["repository"], both["tag"], both["digest"]) == (
        "moby/buildkit", "v0.33.0", "sha256:" + "6c" * 32)
    digest_only = recorder._ref_parts(f"moby/buildkit@{CHILD}")
    assert (digest_only["repository"], digest_only["tag"], digest_only["digest"]) == (
        "moby/buildkit", None, CHILD)
    tagged = recorder._ref_parts("redis:7-alpine")
    assert (tagged["repository"], tagged["tag"], tagged["digest"]) == ("redis", "7-alpine", None)
    ported = recorder._ref_parts("localhost:5000/cap/backend@sha256:" + "aa" * 32)
    assert ported["repository"] == "localhost:5000/cap/backend", (
        "a registry port is not a tag")
    assert (ported["tag"], ported["digest"]) == (None, "sha256:" + "aa" * 32)


def test_a_declaration_built_from_an_expression_is_not_compared_as_a_pin(tmp_path: Path) -> None:
    """Control over the reader: `${{ … }}` is recorded as an expression, never as a value."""
    path = tmp_path / "wf.yml"
    path.write_text(yaml.safe_dump({
        "jobs": {"j": {"env": {"CAP_OBSERVE_BUILDER": "${{ steps.x.outputs.name }}"},
                       "steps": [{"uses": "docker/setup-buildx-action@v3",
                                  "with": {"buildx-version": "v0.37.1"}}]}}}), "utf-8")
    read = recorder.configured_workflow_values(str(path), "j", "from-argv")
    assert read["builder_name"] == "from-argv", "argv is a fallback, not a substitute authority"
    assert read["expressions"] == {"CAP_OBSERVE_BUILDER": "${{ steps.x.outputs.name }}"}


def test_the_lock_and_the_ci_observation_job_score_conforming(tmp_path: Path) -> None:
    """M3's first requirement: the two authorities must agree before either is believed."""
    _, payload = recorded(tmp_path, observe_run())
    assert payload["comparison"]["lock_vs_workflow"]["status"] == "CONFORMING", \
        payload["comparison"]["lock_vs_workflow"]["fields"]
    assert payload["configured"]["lock"]["buildkit_digest"] == BUILDKIT["digest"]
    assert payload["configured"]["workflow"]["builder_name"] == BUILDER


# -- the seven controls -------------------------------------------------------------------

def test_control_1_lock_disagreeing_with_the_workflow_is_a_lock_vs_workflow_mismatch(
        tmp_path: Path) -> None:
    """A lock bumped without the workflow (or the other way round) is a defect in the file.

    The workflow here is a throwaway copy in tmp_path with one field changed -- the real
    ci.yml is not edited to make a test pass, and the recorder still reads it from disk.
    """
    path = tmp_path / "wf.yml"
    text = yaml.safe_load(CI_PATH.read_text("utf-8"))
    for job in text["jobs"].values():
        for step in job.get("steps", []):
            with_block = step.get("with") or {}
            if with_block.get("buildx-version") == BUILDX["version"]:
                with_block["buildx-version"] = "v0.0.1-drifted-away"
    path.write_text(yaml.safe_dump(text), "utf-8")
    code, payload = recorded(tmp_path, observe_run(), "--workflow", str(path))
    comparison = payload["comparison"]
    assert comparison["lock_vs_workflow"]["status"] == "MISMATCH"
    assert comparison["lock_vs_workflow"]["fields"]["buildx_version"] == {
        "declared": BUILDX["version"], "read_back": "v0.0.1-drifted-away",
        "relation": "different"}
    # Not collapsed: the workflow-vs-observed layer still reads its own answer.
    assert comparison["workflow_vs_observed"]["status"] == "MISMATCH"
    assert code == 0, "a mismatch is a measurement; nothing here blocks on it"


def test_control_2_the_runners_own_buildx_still_disagrees_and_is_still_recorded(
        tmp_path: Path) -> None:
    """F-44's measurement survives A2.1 on purpose.

    The runner answers `v0.37.0` to `docker buildx` while the repository's pin says v0.37.1.
    Before A2.1 that disagreement *was* the producer mismatch; now the build runs under the
    controlled executable, so the producer conforms while the runner's answer stays in the
    record as its own field. Deleting it would have made F-44 look resolved by stopping to
    measure it, which is the opposite of what it means.
    """
    _, payload = recorded(tmp_path, observe_run(version="v0.37.0"))
    assert statuses(payload) == {"lock_vs_workflow": "CONFORMING",
                                 "controlled_pin_vs_lock": "CONFORMING",
                                 "workflow_vs_observed": "CONFORMING",
                                 "lock_vs_observed": "CONFORMING"}
    fields = payload["comparison"]["workflow_vs_observed"]["fields"]
    assert fields["cli_plugin_buildx_version"] == {
        "declared": BUILDX["version"], "read_back": "v0.37.0", "relation": "different",
        "scored": False}, "the runner's own answer is kept, and marked as not the producer"
    assert fields["buildx_version"]["read_back"] == BUILDX["version"], (
        "the scored side is the binary that built")
    assert payload["comparison"]["buildx_binaries"]["cli_plugin_version"] == "v0.37.0"
    assert payload["comparison"]["buildx_binaries"]["scored_buildx_from"] == "controlled_buildx"


def test_control_2b_a_different_controlled_buildx_is_a_workflow_vs_observed_mismatch(
        tmp_path: Path) -> None:
    """The pinned executable itself answering the wrong version is the case that must bite.

    A v0.36.0 binary sitting at the pinned path passes a path check and fails a version check;
    the record has to say which one it failed, because the two mean different repairs.
    """
    _, payload = recorded(tmp_path, observe_run(controlled_version="v0.36.0"))
    assert statuses(payload)["workflow_vs_observed"] == "MISMATCH"
    assert statuses(payload)["lock_vs_observed"] == "MISMATCH"
    fields = payload["comparison"]["workflow_vs_observed"]["fields"]
    assert fields["controlled_buildx_version"]["relation"] == "different", fields[
        "controlled_buildx_version"]
    assert fields["controlled_buildx_version"]["read_back"] == "v0.36.0"
    assert fields["controlled_buildx_commit"]["relation"] == "equal", (
        "the commit is a separate read: a wrong version with a matching commit is its own finding")
    assert payload["producer_alignment"]["verdict"] == "MISMATCH"
    assert payload["producer_alignment"]["components"] == statuses(payload), (
        "the summary must carry the named answers, not replace them")


def test_control_2c_the_system_plugin_being_used_instead_is_a_mismatch(tmp_path: Path) -> None:
    """A2.1's central control: the build silently going back through `docker buildx`.

    Every declared value still agrees -- same path, same version, same hash -- because the
    pinned binary is installed and correct. What changed is which one the *build* ran under,
    and the invocation record is the only field that can see it.
    """
    _, payload = recorded(tmp_path, observe_run(),
                          build_argv=["docker", "buildx", "build", "--builder", BUILDER,
                                      "backend"])
    assert statuses(payload)["workflow_vs_observed"] == "MISMATCH"
    field = payload["comparison"]["workflow_vs_observed"]["fields"][
        "build_invoked_controlled_executable"]
    assert field["relation"] == "different", field
    assert field["read_back"] == "docker", field
    invocation = payload["observed"]["build_invocation"]
    assert invocation["dispatch"] == "docker CLI plugin", invocation
    assert payload["observed"]["controlled_buildx"]["status"] == "READ", (
        "the pinned binary was fine; it just was not the one that ran")


def test_control_3_a_foreign_running_digest_is_a_mismatch_and_a_tag_match_is_not(
        tmp_path: Path) -> None:
    """The container runs bytes the pinned index does not name.

    The tag on the container's config image is the *same tag* the lock pins -- so an
    implementation that compared tags, or trusted `buildx inspect`'s `Image:` line, would
    read this as a match. It is the mismatch that the digest layer exists to catch.
    """
    run = observe_run(repo_digests=[f"moby/buildkit@{FOREIGN}"],
                      index=index_json((CHILD,)))
    _, payload = recorded(tmp_path, run)
    relation = payload["comparison"]["lock_vs_observed"]["digest_relation"]
    assert relation["status"] == "MISMATCH"
    assert relation["relation"] == "running_digest_is_not_named_by_the_pinned_index"
    assert relation["pinned_digest"] == BUILDKIT["digest"]
    assert relation["running_digest"] == FOREIGN
    assert payload["comparison"]["lock_vs_observed"]["status"] == "MISMATCH"
    builder = payload["observed"]["builder"]
    assert builder["container_config_image"] == BUILDKIT["image_ref"], (
        "the tag-level view of these bytes still looks like the pin")
    assert builder["running_image"]["digest"] == FOREIGN, (
        "and it is the pull digest, not the tag, that the verdict reads")


def test_control_3b_a_platform_child_of_the_pinned_index_conforms(tmp_path: Path) -> None:
    """The conforming case is *not* string equality, and that is measured, not assumed.

    A pinned digest is a manifest-list; a pulled container names the child manifest for its
    platform. The two strings differ in the good case, so the registry read of the index is
    what licenses the verdict -- and a tag match alone would have proved nothing either way.
    """
    assert CHILD != BUILDKIT["digest"], "the fixture stopped exercising the layered case"
    _, payload = recorded(tmp_path, observe_run())
    relation = payload["comparison"]["lock_vs_observed"]["digest_relation"]
    assert relation["status"] == "CONFORMING"
    assert relation["relation"] == "running_is_a_child_of_the_pinned_index"
    assert CHILD in relation["index_children"]
    assert payload["comparison"]["lock_vs_observed"]["status"] == "CONFORMING"


def test_control_4_a_builder_that_is_not_docker_container_mismatches_the_declaration(
        tmp_path: Path) -> None:
    """`docker` as the driver means the daemon's kit ran, whatever the file says."""
    run = observe_run(driver="docker", inspect_image="", container_exists=False)
    _, payload = recorded(tmp_path, run)
    fields = payload["comparison"]["workflow_vs_observed"]["fields"]
    assert fields["driver"]["relation"] == "different", fields["driver"]
    assert payload["comparison"]["workflow_vs_observed"]["status"] == "MISMATCH"
    builder = payload["observed"]["builder"]
    assert builder["status"] == "NOT_APPLICABLE" and "embedded BuildKit" in builder["reason"], \
        builder
    assert "incomplete" not in payload, "a stated absence is not a lost read"


def test_control_5_an_unreadable_builder_is_unknown_and_never_a_pass(tmp_path: Path) -> None:
    """The no-null-as-match rule, on the layer where it is easiest to lose.

    With the builder unreadable, `lock_vs_workflow` is still CONFORMING -- it is a claim
    about two files, and files did not become unreadable -- while both layers that need the
    runner report UNKNOWN. `--self-check` fails on that, because the instrument broke.
    """
    run = observe_run(builder_exists=False)
    code, payload = recorded(tmp_path, run, "--self-check")
    assert code == 1, "a builder the observation promised and could not read is a broken run"
    assert statuses(payload) == {"lock_vs_workflow": "CONFORMING",
                                 "controlled_pin_vs_lock": "CONFORMING",
                                 "workflow_vs_observed": "UNKNOWN",
                                 "lock_vs_observed": "UNKNOWN"}
    builder = payload["observed"]["builder"]
    assert builder["status"] == "ERROR" and builder["ok"] is False
    assert 'no builder' in builder["error"], "the CLI's own complaint belongs in the record"
    for gap in ("observed.builder.builder", "observed.builder.driver", "observed.builder.nodes",
                "observed.builder.error"):
        assert gap in payload["contract_gaps"], payload["contract_gaps"]
    # The same record without --self-check: the release's own build path never dies on it.
    again, payload2 = recorded(tmp_path, observe_run(builder_exists=False))
    assert again == 0 and payload2["contract_gaps"] == payload["contract_gaps"]


def test_a_runner_that_answers_nothing_reads_unknown_rather_than_conforming(
        tmp_path: Path) -> None:
    """The half of control 5 that one unreadable layer does not reach: nothing answered.

    With every `docker …` read failed, each comparison has no field left to score, and a
    rule that treats "nothing to compare" as agreement would certify a release from an
    observation that observed nothing. The plugin binaries still answer, because a buildx
    does not need a daemon to report its own version -- which is why the record is not empty
    either, and why the verdict has to come from the reads and not from their count.
    """
    code, payload = recorded(tmp_path, observe_run(dead_daemon=True), "--self-check")
    assert statuses(payload) == {"lock_vs_workflow": "CONFORMING",
                                 "controlled_pin_vs_lock": "CONFORMING",
                                 "workflow_vs_observed": "UNKNOWN",
                                 "lock_vs_observed": "UNKNOWN"}, (
        "a pinned executable can still report its own version and hash with no daemon, and that "
        "must not be allowed to read as a conforming producer")
    assert payload["producer_alignment"]["verdict"] == "UNKNOWN"
    assert "not a pass" in payload["producer_alignment"]["meaning"]
    assert payload["observed"]["executing_buildx"]["ok"] is False
    assert payload["incomplete"] == ["buildx_version", "builder", "docker_engine"], \
        payload["incomplete"]
    assert code == 1, "a dead instrument is a failed observation, whatever it concludes"
    for gap in ("observed.executing_buildx.resolved_path", "observed.engine.version",
                "observed.builder.builder"):
        assert gap in payload["contract_gaps"], payload["contract_gaps"]


def no_path_run(version: str, path_version_pairs: dict[str, tuple[str, str]],
                commit: str = "ac30b249211430b85fb8f37b6e7154b5c47ba0b6"):
    """A runner whose `docker buildx version` prints no install path at all."""
    run = observe_run(version=version)

    def answering(argv: list[str]) -> tuple[int, str, str]:
        key = tuple(argv)
        if key == ("docker", "buildx", "version"):
            return 0, f"github.com/docker/buildx {version} {commit}", ""
        if len(argv) == 2 and argv[1] == "version" and argv[0] in path_version_pairs:
            printed, printed_commit = path_version_pairs[argv[0]]
            return 0, version_line(argv[0], printed, printed_commit), ""
        return run(argv)

    answering.seen = run.seen  # type: ignore[attr-defined]
    return answering


def test_a_cli_that_prints_no_path_is_still_answered_by_the_binary_on_disk(
        tmp_path: Path) -> None:
    """Stage 2's question has a second evidence route, and it is labelled as one.

    The candidate that reports the same version *and* commit is the same bytes, so when
    exactly one matches the executing line, the binary is identified -- while `path` stays
    null, because the CLI never said it. F-44's six records are this shape.
    """
    ci_commit = "ac30b249211430b85fb8f37b6e7154b5c47ba0b6"  # what F-44's records read back
    run = no_path_run("v0.37.0", {SYSTEM_PLUGIN: ("v0.37.0", ci_commit),
                                  ACTION_PLUGIN: ("v0.37.1", "62ab6b42a64bcf84f225559ff1015b98")})
    _, payload = recorded(tmp_path, run)
    executing = payload["observed"]["executing_buildx"]
    assert executing["path"] is None and "NOT_REPORTED" in executing["path_status"]
    assert executing["resolved_path"] == SYSTEM_PLUGIN
    assert "only installed candidate" in executing["identified_by"]
    assert payload["observed"]["docker_cli_plugin"]["resolves_to"] == SYSTEM_PLUGIN
    assert payload["comparison"]["buildx_binaries"]["executing"] == SYSTEM_PLUGIN
    assert payload["comparison"]["buildx_binaries"]["identified_by"] == executing["identified_by"]
    assert payload["comparison"]["buildx_binaries"]["installed_on_disk"] == {
        ACTION_PLUGIN: "v0.37.1", SYSTEM_PLUGIN: "v0.37.0"}
    assert "contract_gaps" not in payload, payload.get("contract_gaps")


def test_two_candidates_answering_identically_are_ambiguous_not_chosen(tmp_path: Path) -> None:
    """When two paths carry the same bytes, picking one would be a guess dressed as a read."""
    commit = "ac30b249211430b85fb8f37b6e7154b5c47ba0b6"
    run = no_path_run("v0.37.0", {SYSTEM_PLUGIN: ("v0.37.0", commit),
                                  ACTION_PLUGIN: ("v0.37.0", commit)})
    code, payload = recorded(tmp_path, run, "--self-check")
    executing = payload["observed"]["executing_buildx"]
    assert executing["resolved_path"] is None
    assert "ambiguous" in executing["identified_by"], executing["identified_by"]
    assert sorted(executing["ambiguous_candidates"]) == sorted([SYSTEM_PLUGIN, ACTION_PLUGIN])
    assert "observed.executing_buildx.resolved_path" in payload["contract_gaps"]
    assert code == 1, "an unresolved Stage 2 question is a broken observation, not a blank"


def test_a_version_no_candidate_answers_for_leaves_the_binary_unidentified(
        tmp_path: Path) -> None:
    """The third case: silent CLI, and nothing on disk reporting those bytes."""
    run = no_path_run("v0.37.0", {ACTION_PLUGIN: ("v0.37.1", "62ab6b42a64bcf84f225559ff1015b98")})
    _, payload = recorded(tmp_path, run)
    executing = payload["observed"]["executing_buildx"]
    assert executing["resolved_path"] is None
    assert "not identified" in executing["identified_by"]
    assert "ambiguous_candidates" not in executing
    assert payload["observed"]["docker_cli_plugin"]["resolves_to"] is None


# -- the shapes CI's first observation run printed (run 35826852945 at 13930dc) -----------

INSPECT_FROM_THE_RUNNER = '''Name:          cap3a-producer-observation
Driver:        docker-container
Last Activity: 2026-09-23 06:27:57 +0000 UTC

Nodes:
 Name:      cap3a-producer-observation0
 Endpoint:  unix:///var/run/docker.sock
 Status:    running
 Buildkit version:  v0.33.0
 Platforms: linux/amd64, linux/amd64/v2, linux/amd64/v3, linux/386
 Driver Options: image="moby/buildkit:v0.33.0@sha256:6c2fa84a6b61ccd72899dde4239f8d5717f05f9a''' \
'''8ca6f3cad185fb1a95a94de3"
 BuildKit daemon flags: --allow-insecure-entitlement=network.host
'''


def test_a_runner_that_prints_no_per_node_image_still_reports_its_container(
        tmp_path: Path) -> None:
    """Measured, not imagined: buildx v0.37 prints `Buildkit version:` and no `Image:` line.

    The first CI observation run therefore refused its whole builder record -- "the
    docker-container builder named no node image, so the BuildKit container it runs cannot be
    identified" -- while the container it was asking about was running and inspectable, and
    the build had already succeeded through it. The declared reference is now kept as a
    declaration and the running bytes come from the container read, which is the only layer
    that can be compared against the pinned index.
    """
    code, payload = recorded(tmp_path, observe_run(inspect_output=INSPECT_FROM_THE_RUNNER),
                             "--self-check")
    builder = payload["observed"]["builder"]
    assert builder["status"] == "READ" and builder["ok"] is True, builder
    assert builder["buildkit_version"] == "v0.33.0", "read under the label the tool prints"
    assert builder["node_image"] is None
    assert builder["node_driver_options_image"] == BUILDKIT["image_ref"], builder
    assert builder["container"] == CONTAINER and builder["node_status"] == "running"
    assert builder["node_endpoint"] == "unix:///var/run/docker.sock"
    assert builder["running_image"]["digest"] == CHILD
    assert "no per-node Image" in builder["node_image_note"]
    relation = payload["comparison"]["lock_vs_observed"]["digest_relation"]
    assert relation["status"] == "CONFORMING" and CHILD in relation["index_children"]
    assert code == 0, payload.get("contract_gaps")
    assert not [gap for gap in payload.get("contract_gaps", [])
                if gap.startswith("observed.builder")], payload.get("contract_gaps")
    assert "incomplete" not in payload, payload.get("incomplete")


def test_the_plugin_the_action_left_on_path_is_recorded_beside_the_one_that_ran(
        tmp_path: Path) -> None:
    """F-44's mechanism as two files and one answer.

    None of the four cli-plugins directories held the action's binary at the first CI run, so
    scanning only those would have said "the action installed nothing" while a v0.37.1 buildx
    sat on the machine where the CLI also looks. Both versions stay in the record; the
    comparison is computed against the binary that executed.
    """
    run = observe_run(version="v0.37.0", path=SYSTEM_PLUGIN, path_plugin="v0.37.1")
    _, payload = recorded(tmp_path, run)
    plugins = payload["observed"]["docker_cli_plugin"]
    assert PATH_PLUGIN in plugins["candidates"], sorted(plugins["candidates"])
    assert plugins["candidates"][PATH_PLUGIN]["version"] == "v0.37.1"
    assert plugins["candidates"][SYSTEM_PLUGIN]["version"] == "v0.37.0"
    assert plugins["path_lookup"]["status"] == "READ"
    assert plugins["resolves_to"] == SYSTEM_PLUGIN
    binaries = payload["comparison"]["buildx_binaries"]
    assert binaries["executing"] == SYSTEM_PLUGIN, "where `docker buildx` would have gone"
    assert binaries["cli_plugin_version"] == "v0.37.0"
    assert binaries["built_with"] == CONTROLLED, "and where the build actually went"
    assert payload["comparison"]["workflow_vs_observed"]["fields"]["buildx_version"][
        "read_back"] == BUILDX["version"]
    assert payload["comparison"]["workflow_vs_observed"]["fields"][
        "cli_plugin_buildx_version"]["read_back"] == "v0.37.0"


def test_a_path_lookup_that_finds_nothing_is_a_recorded_absence(tmp_path: Path) -> None:
    """`which docker-buildx` failing is a fact about the machine, not a missing field."""
    _, payload = recorded(tmp_path, observe_run())
    plugins = payload["observed"]["docker_cli_plugin"]
    assert plugins["path_lookup"]["status"] == "ERROR"
    assert "not found in path" in plugins["path_lookup"]["error"]
    assert PATH_PLUGIN not in plugins["candidates"]


def test_control_6_an_action_installed_binary_and_a_executing_one_are_both_kept(
        tmp_path: Path) -> None:
    """`setup-buildx-action` may install one buildx while the CLI dispatches to another.

    Nothing may pick the flattering answer, so the record carries the installed versions, the
    path the CLI would have taken and its version, *and* the controlled executable the build
    actually used. A2.1 added a third binary to a comparison that used to have two, which is
    the point: the runner's disagreement is still on the page, it just is not the producer.
    """
    run = observe_run(version="v0.37.0", path=SYSTEM_PLUGIN,
                      plugins={ACTION_PLUGIN: "v0.37.1", SYSTEM_PLUGIN: "v0.37.0"})
    _, payload = recorded(tmp_path, run)
    binaries = payload["comparison"]["buildx_binaries"]
    assert binaries["executing"] == SYSTEM_PLUGIN
    assert binaries["cli_plugin_version"] == "v0.37.0"
    assert binaries["built_with"] == CONTROLLED and binaries["controlled"] == CONTROLLED
    assert binaries["installed_on_disk"] == {ACTION_PLUGIN: "v0.37.1",
                                             SYSTEM_PLUGIN: "v0.37.0"}, "both, no cherry-picking"
    assert payload["observed"]["docker_cli_plugin"]["resolves_to"] == SYSTEM_PLUGIN
    assert payload["comparison"]["workflow_vs_observed"]["fields"]["buildx_version"][
        "read_back"] == BUILDX["version"], (
        "the scored side is the executable that built; the runner's answer is its own field")


def test_control_7_everything_aligning_reads_conforming(tmp_path: Path) -> None:
    """A2.1's target shape: the pinned executable, read back, hashed, and doing the building."""
    _, payload = recorded(tmp_path, observe_run())
    assert statuses(payload) == {"lock_vs_workflow": "CONFORMING",
                                 "controlled_pin_vs_lock": "CONFORMING",
                                 "workflow_vs_observed": "CONFORMING",
                                 "lock_vs_observed": "CONFORMING"}
    assert payload["producer_alignment"]["verdict"] == "CONFORMING"
    assert "contract_gaps" not in payload, payload.get("contract_gaps")
    assert payload["observed"]["executing_buildx"]["path"] == SYSTEM_PLUGIN
    controlled = payload["observed"]["controlled_buildx"]
    assert controlled["status"] == "READ" and controlled["version"] == BUILDX["version"]
    assert controlled["commit"] == CONTROLLED_COMMIT
    assert controlled["integrity"] == {
        "expected": PIN["integrity"]["expected"], "computed": PIN["integrity"]["expected"],
        "command": controlled["integrity"]["command"], "relation": "equal",
        "status": "CONFORMING"}, controlled["integrity"]
    assert payload["observed"]["build_invocation"]["controlled"]["status"] == "CONFORMING"


# -- field-level controls over the read-back layers ---------------------------------------

def test_a_version_line_without_an_install_path_invents_none() -> None:
    """Docker Desktop's CLI really prints `github.com/docker/buildx v0.35.0-desktop.2 <commit>`.

    Read from this machine, not imagined: a positional parse filed the module name under
    `path`, which would have made "which binary executed the build" a fabricated answer.
    """
    no_path = recorder.parse_version_line(
        "github.com/docker/buildx v0.35.0-desktop.2 b554ce1decd8b509893b1e7c6227eabfb923d094")
    assert no_path["path"] is None and no_path["module"] == "github.com/docker/buildx"
    assert no_path["version"] == "v0.35.0-desktop.2"
    assert no_path["commit"].startswith("b554ce1d")
    assert "NOT_REPORTED" in no_path["path_status"]
    with_path = recorder.parse_version_line(version_line(SYSTEM_PLUGIN, "v0.37.1"))
    assert with_path["path"] == SYSTEM_PLUGIN and with_path["path_status"] == "READ"
    assert with_path["version"] == "v0.37.1"


def test_the_buildkit_container_is_discovered_not_assumed(tmp_path: Path) -> None:
    """CI disproved the naming guess, so the daemon's own listing decides.

    The first observation runs asked for `buildx_buildkit_<builder>` and got
    `Error: No such object` -- while the scratch build through that very builder had already
    succeeded. buildx names the container after the *node* (`…-observation0`), and the node's
    attributes carry its id as `org.mobyproject.buildkit.worker.hostname`; joining the two is
    a read, guessing a convention is not, and the record says which produced the answer.
    """
    node_container = f"buildx_buildkit_{NODE}"
    run = observe_run(
        worker="ffffffffffff",
        container_rows=["\t".join(("d613bc1212b9", CONTAINER, BUILDKIT["image_ref"],
                                   "running")),
                        "\t".join(("ffffffffffff", node_container, BUILDKIT["image_ref"],
                                   "running"))])
    recorded(tmp_path, run)
    lookups = {argv[-1] for argv in run.seen if argv[:2] == ("docker", "inspect")}
    assert lookups == {node_container}, (
        f"the hostname join should have inspected {node_container!r}, not {sorted(lookups)}")

    # With a listing that names nothing, the convention is a labelled fallback, not a fact.
    _, payload = recorded(tmp_path, observe_run(container_rows=[]))
    lookup = payload["observed"]["builder"]["container_lookup"]
    assert lookup["daemon_rows"] == []
    assert lookup["matched_by"] == "guessed-from-naming-rule"
    assert lookup["candidates"] == [node_container, CONTAINER], "node first: the measured order"


def test_nodes_are_read_as_nodes_with_their_own_identity(tmp_path: Path) -> None:
    """The schema's `nodes` block: name, endpoint, status, BuildKit version, declared image."""
    _, payload = recorded(tmp_path, observe_run())
    node = payload["observed"]["builder"]["nodes"][0]
    assert node == {"name": NODE, "endpoint": "unix:///var/run/docker.sock",
                    "status": "running", "buildkit": "v0.33.0",
                    "image": BUILDKIT["image_ref"]}, node
    builder = payload["observed"]["builder"]
    assert builder["builder"] == BUILDER and builder["driver"] == "docker-container"
    assert builder["builder_last_activity"].startswith("2026-09-23"), builder
    assert builder["requested_builder"] == BUILDER, "the record says which builder was asked"
    assert builder["container_config_image"] == BUILDKIT["image_ref"]
    assert builder["container_image_id"] == CONFIG_ID
    assert builder["config_digest"] == CONFIG_ID.split(":")[-1]
    assert builder["running_image"]["digest"] == CHILD, (
        "the manifest side and the config side are different facts, and only the first is "
        "comparable to the pin")


def test_a_container_with_no_repo_digests_reports_unknown_rather_than_a_config_match(
        tmp_path: Path) -> None:
    """`.Image` is a config digest. Comparing it to an index pin would be a category error."""
    _, payload = recorded(tmp_path, observe_run(repo_digests=[]))
    builder = payload["observed"]["builder"]
    assert builder["running_manifest_digest"]["status"] == "UNKNOWN"
    assert builder["running_image"]["digest"] is None
    relation = payload["comparison"]["lock_vs_observed"]["digest_relation"]
    assert relation["status"] == "UNKNOWN" and relation["running_digest"] is None
    assert "never scored as a match" in relation["reason"]
    assert payload["comparison"]["lock_vs_observed"]["status"] == "UNKNOWN"


def test_the_pinned_index_read_keeps_attestation_descriptors_apart(tmp_path: Path) -> None:
    """A registry index names runnable images and attestation manifests; they are not one list."""
    _, payload = recorded(tmp_path, observe_run())
    pin = payload["pinned_index_resolution"]
    assert pin["ok"] is True and pin["is_index"] is True
    assert [child["digest"] for child in pin["children"]] == [CHILD]
    assert pin["children"][0]["platform"] == "linux/amd64"
    assert [item["digest"] for item in pin["attestation_descriptors"]] == [ATTESTATION]
    assert ATTESTATION not in json.dumps(pin["children"])
    assert payload["observed"]["docker_engine"]["value"] == "28.0.4"
    assert payload["observed"]["engine"]["version"] == "28.0.4"
    assert payload["observed"]["builders"]["status"] == "READ"
    assert any(BUILDER in line for line in payload["observed"]["builders"]["lines"]), (
        "`buildx ls` is the runner's own list of builders; the record keeps it verbatim")
    assert payload["runner"]["runner_environment"] == "GitHub-ACTIONS"
    assert payload["runner"]["imageos"] == "ubuntu2404"


def test_an_unresolvable_pinned_index_is_unknown_not_a_mismatch(tmp_path: Path) -> None:
    """If the registry read fails, the child comparison cannot be made at all."""
    run = observe_run(repo_digests=[f"moby/buildkit@{FOREIGN}"], index="")
    _, payload = recorded(tmp_path, run)
    assert payload["pinned_index_resolution"]["status"] == "ERROR"
    relation = payload["comparison"]["lock_vs_observed"]["digest_relation"]
    assert relation["status"] == "UNKNOWN"
    assert relation["relation"] == "pinned_index_unresolved", relation


# -- the two digest layers, each read from the object that actually carries it -------------

def test_the_pull_digest_is_asked_of_the_object_that_has_it(tmp_path: Path) -> None:
    """`RepoDigests` is an image field: a container cannot answer it, and CI proved so.

    The first record whose builder layer came back clean carried
    `map has no entry for key "RepoDigests"` and then reported `UNKNOWN` forever, because a
    question that can never be answered was being asked of the wrong object. The container
    read still supplies the image id; the digest read has to target that id.
    """
    run = observe_run()
    recorded(tmp_path, run)
    assert ("docker", "image", "inspect", "--format", "{{json .RepoDigests}}",
            CONFIG_ID) in run.seen, run.seen
    container_digest_asks = [argv for argv in run.seen
                              if "{{json .RepoDigests}}" in argv
                              and argv[:2] == ("docker", "inspect")]
    assert not container_digest_asks, (
        f"a container cannot answer an image field: {container_digest_asks}")
    _, payload = recorded(tmp_path, observe_run())
    read = payload["observed"]["builder"]["repo_digests_read"]
    assert read["status"] == "READ" and read["asked_of"] == CONFIG_ID, read
    assert "image-object field" in read["note"], read


def test_a_lost_platform_read_leaves_the_child_unrequested_not_guessed(
        tmp_path: Path) -> None:
    """Which platform to compare is a read, not an assumption: when it fails, nothing is picked."""
    _, payload = recorded(tmp_path, observe_run(platform=None))
    builder = payload["observed"]["builder"]
    assert builder["image_platform_status"] == "ERROR", builder
    assert builder["repo_digests"] == [f"moby/buildkit@{CHILD}"], (
        "the digest read is independent of the platform read -- one failing must not erase the "
        "other")
    child = payload["pinned_index_resolution"]["platform_child"]
    assert child["status"] == "NOT_REQUESTED" and child["platform"] is None, child
    assert payload["comparison"]["lock_vs_observed"]["config_digest_relation"]["status"] \
        == "UNKNOWN"
    assert "contract_gaps" not in payload, payload.get("contract_gaps")


def test_the_pinned_child_is_read_down_to_its_own_config_digest(tmp_path: Path) -> None:
    """Stage 3's verification layer: the running image id against the pin's child config.

    The index names a manifest, the container runs a config, and the only place the two
    meet is inside that child manifest. So the record fetches the child for the platform the
    *image* reports -- not the runner's environment, not the tag -- and scores it apart from
    the manifest layer.
    """
    _, payload = recorded(tmp_path, observe_run())
    child = payload["pinned_index_resolution"]["platform_child"]
    assert child["status"] == "READ" and child["platform"] == "linux/amd64", child
    assert child["manifest_digest"] == CHILD and child["config_digest"] == CONFIG_ID, child
    assert child["layers"] == 1, child
    relation = payload["comparison"]["lock_vs_observed"]["config_digest_relation"]
    assert relation["status"] == "CONFORMING", relation
    assert relation["relation"] == "running_config_is_the_pinned_child_config", relation
    assert relation["pinned_child_manifest"] == CHILD
    field = payload["comparison"]["lock_vs_observed"]["fields"]["buildkit_child_config"]
    assert field["relation"] == "equal" and field["declared"] == field["read_back"], field


def test_a_different_config_digest_is_a_mismatch_on_that_layer(tmp_path: Path) -> None:
    """The control that keeps the layer from being an approval by default."""
    _, payload = recorded(tmp_path, observe_run(child_config=FOREIGN))
    relation = payload["comparison"]["lock_vs_observed"]["config_digest_relation"]
    assert relation["status"] == "MISMATCH", relation
    assert relation["relation"] == "running_config_is_not_the_pinned_child_config", relation
    assert relation["pinned_config_digest"] == FOREIGN, relation
    assert relation["running_config_digest"] == CONFIG_ID, relation


def test_the_two_digest_layers_are_never_collapsed_into_one_claim(tmp_path: Path) -> None:
    """Manifest unreadable, config readable: the record says so layer by layer.

    The `.Image` of a container cannot answer the manifest question -- that is the category
    error the layering exists to prevent -- so a conforming config layer must not turn the
    manifest layer's `UNKNOWN` into a match, and the required-field rule must not move to
    the easier layer.
    """
    _, payload = recorded(tmp_path, observe_run(repo_digests=[]))
    compared = payload["comparison"]["lock_vs_observed"]
    assert compared["digest_relation"]["status"] == "UNKNOWN", compared["digest_relation"]
    assert compared["config_digest_relation"]["status"] == "CONFORMING"
    assert compared["fields"]["buildkit_digest"]["relation"] is None, (
        "a config digest must not satisfy the manifest field")
    assert list(compared["required"]) == ["buildx_version", "buildkit_digest",
                                          "buildkit_child_config"], (
        "A2.2 blocks on both layers, so neither can answer for the other: requiring the config "
        "layer is the stricter rule, not a route around the manifest one")
    assert compared["status"] == "UNKNOWN", (
        "one readable layer and one unreadable one is not a pass on either")
    assert compared["layers"] == {
        "manifest": "unknown", "config": "scored",
        "note": compared["layers"]["note"]}, compared["layers"]


def test_a_lost_child_manifest_read_fails_the_instrument(tmp_path: Path) -> None:
    """The registry refused the second read: that is a broken observation, not a measurement."""
    _, payload = recorded(tmp_path, observe_run(child_read_fails=True))
    child = payload["pinned_index_resolution"]["platform_child"]
    assert child["status"] == "ERROR" and child["manifest_digest"] == CHILD, child
    assert "failed to resolve manifest" in child["error"], child
    assert payload["comparison"]["lock_vs_observed"]["config_digest_relation"]["status"] \
        == "UNKNOWN"
    assert "pinned_index_resolution.platform_child" in payload["contract_gaps"], (
        payload["contract_gaps"])


def test_an_ambiguous_platform_child_is_refused_rather_than_picked(tmp_path: Path) -> None:
    """Two children for one platform is not a licence to compare against the nicer one."""
    _, payload = recorded(tmp_path, observe_run(index=index_json((CHILD, FOREIGN))))
    child = payload["pinned_index_resolution"]["platform_child"]
    assert child["status"] == "AMBIGUOUS", child
    assert sorted(child["candidates"]) == sorted([CHILD, FOREIGN]), child
    relation = payload["comparison"]["lock_vs_observed"]["config_digest_relation"]
    assert relation["status"] == "UNKNOWN" and relation["pinned_config_digest"] is None, relation
    assert "contract_gaps" not in payload, (
        f"an ambiguity is a measurement, not a broken read: {payload.get('contract_gaps')}")


def test_a_platform_the_pinned_index_does_not_name_is_not_a_child(tmp_path: Path) -> None:
    """`linux/arm64` on an amd64-only index has no counterpart, and that is stated."""
    _, payload = recorded(tmp_path, observe_run(platform="linux/arm64"))
    child = payload["pinned_index_resolution"]["platform_child"]
    assert child["status"] == "NOT_NAMED" and child["candidates"] == [], child
    assert payload["comparison"]["lock_vs_observed"]["config_digest_relation"]["status"] \
        == "UNKNOWN"


# -- the F-39 measurement fields, from an OCI archive rather than from a registry ---------

def write_oci_tar(path: Path, layer_count: int = 2) -> dict:
    """A hand-built OCI archive: index, manifest, config, layers, attestation."""

    def blob(doc: dict) -> str:
        import hashlib
        data = json.dumps(doc, sort_keys=True).encode()
        return "sha256:" + hashlib.sha256(data).hexdigest(), data

    layers = []
    for index in range(layer_count):
        digest, _ = blob({"layer": index})
        layers.append({"digest": digest,
                       "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                       "size": 10})
    config_digest, config_bytes = blob({
        "architecture": "amd64", "os": "linux", "created": "2026-09-23T00:00:00Z",
        "rootfs": {"type": "layers", "diff_ids": [layer["digest"] for layer in layers]},
        "history": [{"created_by": "FROM python"}]})
    manifest = {"schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"digest": config_digest, "mediaType":
                           "application/vnd.oci.image.config.v1+json", "size": 10},
                "layers": layers}
    manifest_digest, manifest_bytes = blob(manifest)
    attestation_digest, attestation_bytes = blob({"subject": manifest_digest})
    index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
             "manifests": [
                 {"digest": manifest_digest,
                  "mediaType": "application/vnd.oci.image.manifest.v1+json", "size": 10,
                  "platform": {"os": "linux", "architecture": "amd64"}},
                 {"digest": attestation_digest,
                  "mediaType": "application/vnd.oci.image.manifest.v1+json", "size": 10,
                  "platform": {"os": "unknown", "architecture": "unknown"},
                  "annotations": {"vnd.docker.reference.type": "attestation-manifest",
                                  "vnd.docker.reference.digest": manifest_digest}}]}
    index_bytes = json.dumps(index, sort_keys=True).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w") as archive:

        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            import io
            archive.addfile(info, io.BytesIO(data))

        add("index.json", index_bytes)
        add(f"blobs/sha256/{manifest_digest.split(':')[-1]}", manifest_bytes)
        add(f"blobs/sha256/{attestation_digest.split(':')[-1]}", attestation_bytes)
        add(f"blobs/sha256/{config_digest.split(':')[-1]}", config_bytes)
    return {"manifest": manifest_digest, "config": config_digest,
            "attestation": attestation_digest, "layers": [layer["digest"] for layer in layers]}


def test_the_oci_archive_yields_the_f39_fields_from_one_build(tmp_path: Path) -> None:
    """Config digest, layer digests, diff_ids, platform manifest, index composition, attestations.

    One build, so the measurement says PARTIAL and `builds_compared` is 1: attributing a
    digest difference needs two independent builds of one commit, and no amount of field
    coverage changes that. This is the format, not the experiment.
    """
    tar = tmp_path / "scratch.oci.tar"
    written = write_oci_tar(tar)
    _, payload = recorded(tmp_path, observe_run(), "--oci-tar", str(tar),
                          "--dockerfile", str(PROJECT_ROOT / "backend/Dockerfile"))
    f39 = payload["f39_measurement"]
    assert f39["ok"] is True, f39
    assert f39["platform_manifest"]["digest"] == written["manifest"]
    assert f39["platform_manifest"]["config_digest"] == written["config"]
    assert f39["platform_manifest"]["layer_digests"] == written["layers"]
    assert f39["image_config"]["diff_ids"] == written["layers"]
    assert f39["image_config"]["architecture"] == "amd64"
    assert len(f39["index_composition"]) == 2
    assert sorted(item["attestation"] for item in f39["index_composition"]) == [False, True]
    assert f39["attestation_descriptors"] == [written["attestation"]]
    assert f39["frontend"].startswith("# syntax=docker/dockerfile:1@sha256:")
    assert f39["builds_compared"] == 1
    assert f39["measurement_status"].startswith("PARTIAL"), f39["measurement_status"]
    assert f39["status"] == "READ", "the fact status and the measurement claim stay separate"


def test_a_missing_archive_is_recorded_as_not_provided(tmp_path: Path) -> None:
    _, payload = recorded(tmp_path, observe_run(), "--oci-tar",
                          str(tmp_path / "never-written.tar"))
    assert payload["f39_measurement"]["status"] == "NOT_PROVIDED"
    assert payload["f39_measurement"]["builds_compared"] == 1


# -- what the observation job is allowed to touch, and what it is not ---------------------

def observation_scripts() -> str:
    job = (CI["jobs"].get(OBSERVATION_JOB) or {})
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


def test_the_observation_job_publishes_nothing() -> None:
    """No login, no push, no tag, no registry credential: this job makes local bytes.

    Stage 3's constraint -- a NON-PUBLISHING builder -- is checked as text, because the
    difference between "observed the producer" and "shipped an image" is exactly one flag.
    """
    job = CI["jobs"][OBSERVATION_JOB]
    script = observation_scripts()
    for forbidden in ("--push", "login-action", "ghcr.io", "--tag", "docker push",
                      "registry:", "-sS https://", "| sh ", "| bash "):
        assert forbidden not in script, f"the observation job must never {forbidden!r}"
    uses = " ".join(str(step.get("uses", "")) for step in job["steps"])
    assert "login" not in uses, "an observation job that can log in can also push"
    assert "--builder \"$CAP_OBSERVE_BUILDER\"" in script, "the build must go to the named builder"
    assert "type=oci" in script, "the F-39 fields come from an OCI archive, not from the daemon"
    assert "--docker-cli-build" not in script, (
        "observe mode reads a container builder; claiming the docker CLI built it would be false")
    assert "record_build_producer.py" in script and "--self-check" in script
    assert job.get("permissions") is None, (
        "the workflow-level contents: read is all this job needs; a permissions block here "
        "would replace it and could widen it")


def test_the_observation_job_builds_with_the_controlled_executable_not_the_plugin() -> None:
    """A2.1's point, checked as text: no build in this job goes through `docker buildx`.

    The runner's plugin is still *read* -- that is F-44's evidence -- but the five builds and the
    builder creation all name the installed path, and nothing in the job writes to a plugin
    directory. A job that installed a pinned binary and then built with `docker buildx` would
    produce exactly the record A2.1 exists to prevent: a producer that looks pinned.

    BATCH 3 A2.2 moved the install recipe itself into `scripts/release/install_controlled_buildx.py`
    so the release jobs run the same mechanism instead of a copy. The assertions that used to live
    here as greps over job text -- the hash happening before anything is executed, a plugin target
    refused before a byte is downloaded, the version read gating the builder creation -- are now
    behavioural tests in `backend/tests/test_controlled_buildx_install.py`, which is a stronger
    place for them: they exercise the one implementation rather than the shape of one caller. What
    stays here is the job's side of the bargain, which is which values it hands to that mechanism.
    """
    script = observation_scripts()
    assert "docker buildx build" not in script, (
        "the build must run through the controlled executable, not the CLI plugin")
    assert "docker buildx create" not in script, "the named builder is created by the pinned binary"
    assert "docker buildx version" not in script, (
        "the runner's plugin is read by the recorder and the installer, not restated here")
    for argument in ('--pin "$CAP_OBSERVE_BUILDX_PIN"', '--path "$CAP_OBSERVE_BUILDX_PATH"',
                     '--builder "$CAP_OBSERVE_BUILDER"', '--driver "$CAP_OBSERVE_DRIVER"',
                     '--buildkit "$CAP_OBSERVE_BUILDKIT"',
                     "--lock deployment/third-party-images.json"):
        assert argument in script, f"the job no longer hands the mechanism {argument}"
    assert 'BX="$CAP_OBSERVE_BUILDX_PATH"' in script, "the builds use the installed path"
    assert 'COMMON=(--builder "$CAP_OBSERVE_BUILDER"' in script, (
        "every build names the builder the mechanism created")
    assert "/usr/libexec/docker/cli-plugins" not in script, (
        "the runner's plugin is read by the recorder, never written by the job")
    install_call = script.index("install_controlled_buildx.py")
    assert install_call < script.index('build "${COMMON'), (
        "a build that runs before the install verifies nothing")


def test_the_observation_job_exercises_all_five_images_through_one_producer() -> None:
    """Five images, one producer, one round -- each recorded and then scored together.

    The set is the acceptance object, so a job that built four and forgot the fifth has to
    fail here rather than in a verdict somebody reads off a log.
    """
    script = observation_scripts()
    for image in FIVE:
        assert f"record_and_run {image}" in script, f"{image} is not built through the pin"
        assert f"record_one {image}" in script, f"{image} is never recorded"
    assert "--combine" in script and script.count("--expected-image") == 5, (
        "the five records are not scored as one set")


def test_the_browser_image_is_bound_to_this_rounds_base_without_a_registry() -> None:
    """`cap-sandbox-browser` on the HTTP image of *this* round, named by digest.

    The release binds that base to a digest it pushed. Here nothing is pushed, so the binding
    has to come from the round's own OCI archive addressed by its manifest digest -- and if
    that digest cannot be read the build is refused rather than run against a base nobody can
    name. A `:latest` or a bare name would make the fifth image's producer claim weaker than
    the other four.
    """
    script = observation_scripts()
    assert "oci-layout://" in script, "the same-round base has to reach the builder somehow"
    assert "cap-sandbox-http.oci.tar" in script and "http-layout" in script, (
        "the browser's base is unpacked from this round's archive")
    assert 'SANDBOX_HTTP_BASE=cap-sandbox-http' in script, (
        "the Dockerfile's base argument names the build context, not a tag")
    assert "HTTP_BASE_DIGEST" in script and 'refused' in script, (
        "an unreadable base digest must stop the build, not pass silently")
    assert "docker load" not in script and "docker pull" not in script, (
        "no registry and no host store: the docker-container builder cannot see either")


def test_the_release_build_path_is_still_not_switched() -> None:
    """Batch 3A approves Observation Step 1 only, so this is a fence, not a wish.

    If Step 2 is approved later, this test failing is the intended signal that the decision
    was made -- it is not a blocker to work round. It reads the release path's own text
    because "we did not change the release build" is the kind of sentence that has to be
    re-checked rather than remembered.
    """
    release = (PROJECT_ROOT / ".github/workflows/release.yml").read_text("utf-8")
    script = (PROJECT_ROOT / "scripts/release/build_release_image.sh").read_text("utf-8")
    assert "--builder " not in release, "release.yml now selects a builder: Step 2 happened"
    assert "--builder " not in script, "the build script now selects a builder: Step 2 happened"
    assert OBSERVATION_JOB not in release, "the release graph does not depend on the observation"
    assert "record_build_producer.py" in script, "the release evidence still carries a producer"
    for marker in ("--out \"$PRODUCER_FILE\"", "--lock deployment/third-party-images.json"):
        assert marker in script, f"the release recorder lost its argument {marker!r}"


def test_build_mode_still_carries_the_legacy_shape_and_no_obligation(tmp_path: Path) -> None:
    """The release path calls the recorder with `--out` and `--lock`, and nothing else.

    Batch 3A's additions must be additive: in build mode there is no named builder and no
    workflow read, so those fields are recorded as absent and the observation contract does
    not claim them.
    """
    out = tmp_path / "producer.json"
    code = recorder.main(["--out", str(out), "--lock", str(LOCK_PATH)],
                         run=observe_run(), environ={"HOME": HOME})
    assert code == 0
    payload = json.loads(out.read_text("utf-8"))
    assert payload["mode"] == "build"
    assert payload["configured"]["workflow"]["status"] == "NOT_PROVIDED"
    assert payload["pinned_index_resolution"]["status"] == "NOT_APPLICABLE"
    assert not [gap for gap in payload.get("contract_gaps", [])
                if gap.startswith(("configured.workflow", "observed.builder"))], \
        payload.get("contract_gaps")
    assert payload["configured"]["buildx_version"] == BUILDX["version"], (
        "the flat keys build_release_image.sh's evidence already carries stay put")
    assert payload["observed"]["controlled_buildx"]["status"] == "NOT_PROVIDED", (
        "the release path names no controlled executable yet -- A2.2 gives it one")
    assert not [gap for gap in payload.get("contract_gaps", [])
                if gap.startswith(("observed.controlled_buildx", "observed.build_invocation",
                                   "configured.controlled"))], payload.get("contract_gaps")


# -- A2.1: the six producer controls Batch 3A's seven do not reach -------------------------

def test_a_controlled_executable_answering_another_commit_is_a_mismatch(tmp_path: Path) -> None:
    """Wrong buildx commit: the version string matches, the bytes do not.

    A release rebuilt from a different commit prints the same `v0.37.1`, so a version check
    alone would pass it. The commit the binary reports is compared separately for that reason,
    and it is derived from the annotated tag rather than from a log line somebody liked.
    """
    _, payload = recorded(tmp_path, observe_run(controlled_commit="0" * 40))
    fields = payload["comparison"]["workflow_vs_observed"]["fields"]
    assert fields["controlled_buildx_version"]["relation"] == "equal", "the version still matches"
    assert fields["controlled_buildx_commit"]["relation"] == "different", fields[
        "controlled_buildx_commit"]
    assert payload["comparison"]["workflow_vs_observed"]["status"] == "MISMATCH"


def test_a_controlled_executable_printing_another_path_is_a_mismatch(tmp_path: Path) -> None:
    """Where a buildx *does* name its path, a contradiction with the declaration must bite.

    The field is not required -- see the companion test for why -- but `blocking` means it
    cannot report a different path and be filed away as information.
    """
    elsewhere = "/tmp/cap-a21-controlled-buildx/wrapper"
    _, payload = recorded(tmp_path, observe_run(controlled_reports_path=elsewhere))
    fields = payload["comparison"]["workflow_vs_observed"]["fields"]
    assert fields["controlled_buildx_path"] == {
        "declared": CONTROLLED, "read_back": elsewhere, "relation": "different"}, fields[
        "controlled_buildx_path"]
    assert payload["observed"]["controlled_buildx"]["path_reported_by_binary"] == elsewhere
    assert payload["comparison"]["workflow_vs_observed"]["status"] == "MISMATCH", (
        "a path that contradicts the declaration is a disagreement even though the version, "
        "commit, hash and invocation all agree")


def test_a_controlled_executable_printing_the_declared_path_agrees(tmp_path: Path) -> None:
    """The other arm of the same field: a printed path that matches is scored, not flagged.

    Without this, an implementation that treated *any* printed path as a contradiction would
    pass the controls -- the rule is "agree when it agrees", not "ignore what it prints".
    """
    _, payload = recorded(tmp_path, observe_run(controlled_reports_path=CONTROLLED))
    field = payload["comparison"]["workflow_vs_observed"]["fields"]["controlled_buildx_path"]
    assert field == {"declared": CONTROLLED, "read_back": CONTROLLED, "relation": "equal"}, field
    assert payload["comparison"]["workflow_vs_observed"]["status"] == "CONFORMING"
    assert "contract_gaps" not in payload, payload.get("contract_gaps")


def test_a_controlled_executable_that_names_no_path_is_still_identified(tmp_path: Path) -> None:
    """The shape every GitHub runner gives, and the reason path identity is not a printed string.

    Measured at run `35866400091`: the pinned binary answered
    `github.com/docker/buildx v0.37.1 0b265a9f…` with no path. The first A2.1 implementation
    required the path the binary reports, so all five conforming records came back UNKNOWN and
    the job went red -- a requirement no runner can satisfy is an instrument bug, and the fix is
    not to compare the declared path against itself.
    """
    _, payload = recorded(tmp_path, observe_run())
    controlled = payload["observed"]["controlled_buildx"]
    assert controlled["path_reported_by_binary"] is None, controlled
    assert controlled["path"] == CONTROLLED, "the invoked path is carried as what was invoked"
    assert "NOT_REPORTED" in controlled["path_status"], controlled["path_status"]
    field = payload["comparison"]["workflow_vs_observed"]["fields"]["controlled_buildx_path"]
    assert field["relation"] is None and field["scored"] is False, field
    assert "digest of the file at the declared path" in field["why"], field
    assert payload["comparison"]["workflow_vs_observed"]["status"] == "CONFORMING", (
        "version, commit, integrity and invocation answer the question the printed path cannot")
    assert "contract_gaps" not in payload, payload.get("contract_gaps")


def test_a_controlled_executable_that_hashes_differently_is_a_mismatch(tmp_path: Path) -> None:
    """Integrity failure: the release asset behind the pinned URL is not the pinned bytes.

    The comparison is the repository's digest against a hash of the file on the machine that is
    about to run it -- so a replaced asset fails here even though every string the binary
    prints is still the one the pin expects.
    """
    _, payload = recorded(tmp_path, observe_run(controlled_sha="00" + "ab" * 31))
    integrity = payload["observed"]["controlled_buildx"]["integrity"]
    assert integrity["status"] == "MISMATCH" and integrity["relation"] == "different", integrity
    assert integrity["computed"] == "sha256:" + "00" + "ab" * 31, integrity
    assert integrity["expected"] == PIN["integrity"]["expected"]
    assert payload["comparison"]["workflow_vs_observed"]["fields"][
        "controlled_buildx_integrity"]["relation"] == "different"
    assert payload["comparison"]["workflow_vs_observed"]["status"] == "MISMATCH"
    assert "contract_gaps" not in payload, (
        "a disagreement is measured evidence, not a broken instrument")


def test_a_controlled_executable_that_is_not_there_fails_the_instrument(tmp_path: Path) -> None:
    """The install step did not run, or ran into a different path: that is not "no producer".

    Missing bytes are a gap the job goes red on, because a record that quietly scored the
    builder through `docker buildx` after failing to install the pinned executable would look
    like a clean observation of a producer that was never selected.
    """
    code, payload = recorded(tmp_path, observe_run(controlled_exists=False), "--self-check")
    controlled = payload["observed"]["controlled_buildx"]
    assert controlled["status"] == "ERROR", controlled
    assert "no such file" in controlled["error"], controlled
    assert controlled["integrity"]["status"] == "ERROR", controlled["integrity"]
    for gap in ("observed.controlled_buildx.status", "observed.controlled_buildx.version",
                "observed.controlled_buildx.integrity.computed"):
        assert gap in payload["contract_gaps"], payload["contract_gaps"]
    assert code == 1, "a producer that could not be read is a broken observation"
    assert payload["comparison"]["workflow_vs_observed"]["status"] == "UNKNOWN"


def test_a_build_that_recorded_no_invocation_is_a_gap_not_a_pass(tmp_path: Path) -> None:
    """Missing observation evidence, the cheapest way for a green job to mean nothing."""
    out = tmp_path / "evidence.json"
    code = recorder.main(
        ["--out", str(out), "--lock", str(LOCK_PATH), "--mode", "observe", "--self-check",
         "--builder", BUILDER, "--workflow", str(CI_PATH), "--job", OBSERVATION_JOB,
         "--controlled-pin", str(PIN_PATH), "--image", "cap-backend",
         "--build-command", str(tmp_path / "nobody-wrote-this.json")],
        run=observe_run(), environ={"HOME": HOME})
    payload = json.loads(out.read_text("utf-8"))
    assert code == 1
    assert payload["observed"]["build_invocation"]["status"] == "ERROR"
    assert "observed.build_invocation.status" in payload["contract_gaps"], payload["contract_gaps"]


def test_the_instrument_asks_the_builder_through_the_controlled_executable(
        tmp_path: Path) -> None:
    """The reads go through the pinned binary too, or the observation is made by an unknown CLI.

    Batch 3A had to learn buildx's output shapes from three CI runs because `buildx inspect`
    differs between versions; an instrument that prints a pinned BuildKit container by asking a
    runner-chosen CLI is measuring the machine it is trying to rule out.
    """
    run = observe_run()
    recorded(tmp_path, run)
    seen = [tuple(argv) for argv in run.seen]
    assert (CONTROLLED, "inspect", f"--builder={BUILDER}") in seen, seen
    assert (CONTROLLED, "ls") in seen, seen
    assert (CONTROLLED, "imagetools", "inspect", BUILDKIT["image_ref"], "--raw") in seen, seen
    assert ("docker", "buildx", "inspect", f"--builder={BUILDER}") not in seen, (
        "the builder must not be read through the runner's plugin once a pin answers")
    assert ("docker", "buildx", "version") in seen, (
        "the plugin's own answer is still asked, because it is still reported")


# -- A2.1's base bindings: what each image was built ON, and whether it is named -----------

BROWSER_DOCKERFILE = str(PROJECT_ROOT / "backend/docker/sandbox-browser/Dockerfile")
HTTP_DOCKERFILE = str(PROJECT_ROOT / "backend/docker/sandbox-http/Dockerfile")


def test_a_same_round_base_named_by_digest_is_bound_immutably() -> None:
    """The browser image's base is this round's HTTP sandbox, addressed by its own digest.

    A layout *directory* is re-writable, so the binding carries the digest the layout's index
    named and the record says which of the two it holds. Without a digest the same reference is
    a MISMATCH: "built on something in that folder" is not the claim A2.1 needs.
    """
    layout = "/home/runner/work/cap/cap/outputs/producer-observation/http-layout"
    binding = recorder.base_image_bindings(
        BROWSER_DOCKERFILE, ["SANDBOX_HTTP_BASE=cap-sandbox-http"],
        [f"cap-sandbox-http=oci-layout://{layout}@{CHILD}"])
    assert binding["status"] == "READ", binding
    base, = binding["bases"]
    assert base["resolved_from_arg"] == "SANDBOX_HTTP_BASE"
    assert base["ref"] == "cap-sandbox-http"
    assert base["binding"] == "same_round_oci_layout" and base["digest"] == CHILD, base
    assert base["status"] == "READ", base


def test_a_same_round_base_without_a_digest_is_not_bound() -> None:
    binding = recorder.base_image_bindings(
        BROWSER_DOCKERFILE, ["SANDBOX_HTTP_BASE=cap-sandbox-http"],
        ["cap-sandbox-http=oci-layout:///tmp/http-layout"])
    base, = binding["bases"]
    assert base["status"] == "MISMATCH", base
    assert "not bound immutably" in base["error"], base


def test_a_registry_base_is_recorded_by_digest_and_a_tag_alone_is_a_mismatch() -> None:
    """The shipped sandbox bases pin digests; a tag would be a pointer somebody else moves.

    Read from the real Dockerfiles, because this is a claim about what ships rather than about
    what the recorder can parse.
    """
    shipped, = recorder.base_image_bindings(HTTP_DOCKERFILE, [], [])["bases"]
    assert shipped["binding"] == "registry_reference", shipped
    assert shipped["digest"] and shipped["digest"].startswith("sha256:"), shipped
    assert shipped["status"] == "READ", shipped
    moved = recorder.base_image_bindings(
        BROWSER_DOCKERFILE, ["SANDBOX_HTTP_BASE=moby/buildkit:v0.33.0"], [])
    base, = moved["bases"]
    assert base["status"] == "MISMATCH" and base["digest"] is None, base


def test_the_frontend_is_stated_rather_than_left_blank(tmp_path: Path) -> None:
    """`frontend: null` would be read as "not recorded", and it is not what it means.

    Two real shapes: `backend/Dockerfile` pins a syntax digest, the sandbox Dockerfiles name no
    directive at all -- so the frontend that parsed those is the built-in one inside the
    *pinned* BuildKit container, which is a knowable answer and gets said out loud.
    """
    tar = tmp_path / "one.oci.tar"
    write_oci_tar(tar)
    _, pinned = recorded(tmp_path, observe_run(), "--oci-tar", str(tar),
                         dockerfile=str(PROJECT_ROOT / "backend/Dockerfile"))
    assert pinned["f39_measurement"]["frontend_status"] == "PINNED_BY_DIRECTIVE"
    assert pinned["f39_measurement"]["frontend"].startswith(
        "# syntax=docker/dockerfile:1@sha256:"), pinned["f39_measurement"]["frontend"]
    _, builtin = recorded(tmp_path, observe_run(), "--oci-tar", str(tar),
                          dockerfile=HTTP_DOCKERFILE)
    assert builtin["f39_measurement"]["frontend"] is None
    assert builtin["f39_measurement"][
        "frontend_status"] == "BUILTIN_OF_THE_RUNNING_BUILDKIT", builtin["f39_measurement"]
    assert "built-in dockerfile frontend" in builtin["f39_measurement"]["frontend_note"]


def test_an_unresolved_base_argument_is_reported_not_invented() -> None:
    """`ARG SANDBOX_HTTP_BASE` has no default on purpose, so nothing may fill it in silently."""
    binding = recorder.base_image_bindings(BROWSER_DOCKERFILE, [], [])
    base, = binding["bases"]
    assert base["status"] == "ERROR", base
    assert "resolves to nothing" in base["error"], base


# -- A2.1's acceptance rule, over the five images together ---------------------------------

FIVE = ["cap-backend", "cap-frontend", "cap-sandbox-http", "cap-sandbox-browser",
        "cap-egress-proxy"]

#: Which Dockerfile each shipped image is built from, and the flags its recorded build
#: carried. The browser's base arrives only through its build argv, because that is the
#: evidence the recorder reads -- nothing re-types the digest it is meant to be checking.
IMAGE_BUILDS = {
    "cap-backend": (str(PROJECT_ROOT / "backend/Dockerfile"), []),
    "cap-frontend": (str(PROJECT_ROOT / "frontend/Dockerfile"), []),
    "cap-sandbox-http": (HTTP_DOCKERFILE, []),
    "cap-egress-proxy": (str(PROJECT_ROOT / "backend/docker/egress-proxy/Dockerfile"), []),
    "cap-sandbox-browser": (BROWSER_DOCKERFILE, [
        "--build-arg", "SANDBOX_HTTP_BASE=cap-sandbox-http",
        "--build-context", f"cap-sandbox-http=oci-layout:///workspace/http-layout@{CHILD}"]),
}


def five_records(tmp_path: Path, broken: str | None = None,
                 broken_run: str | None = None) -> list[Path]:
    """Produce one observation record per image, and mutate exactly one of them.

    `broken` names the image whose record is made untrue in the way the caller asks for, so a
    set verdict can be tested against a real set rather than against a hand-written summary of
    one. `broken_run` chooses the untruth: a disagreeing controlled version, an unreadable
    builder, a failed build, or the same conforming record carrying a different commit -- the
    last being how a set could otherwise be assembled from whichever runs looked good.
    """
    paths = []
    for image in FIVE:
        dockerfile, flags = IMAGE_BUILDS[image]
        record_dir = tmp_path / image
        record_dir.mkdir()
        argv = [CONTROLLED, "build", "--builder", BUILDER, "--file", dockerfile, *flags, image]

        def write(payload: dict, into: Path = record_dir / "producer-evidence.json") -> None:
            into.write_text(json.dumps(payload), "utf-8")

        code, payload = recorded(record_dir, observe_run(), image=image, dockerfile=dockerfile,
                                 build_argv=argv,
                                 build_exit=1 if broken == image and broken_run == "exit" else 0)
        assert code == 0, payload
        write(payload)
        if broken == image and broken_run == "mismatch":
            _, payload = recorded(record_dir, observe_run(controlled_version="v0.36.0"),
                                  image=image, dockerfile=dockerfile, build_argv=argv)
            write(payload)
        if broken == image and broken_run == "unknown":
            _, payload = recorded(record_dir, observe_run(builder_exists=False), image=image,
                                  dockerfile=dockerfile, build_argv=argv)
            write(payload)
        if broken == image and broken_run == "round":
            payload["runner"]["github_sha"] = "a-different-commit"
            write(payload)
        paths.append(record_dir / "producer-evidence.json")
    return paths


def scored(tmp_path: Path, records: list[Path], expected: list[str]) -> dict:
    out = tmp_path / "producer-set.json"
    argv = ["--out", str(out), "--combine", *[str(path) for path in records]]
    for image in expected:
        argv += ["--expected-image", image]
    recorder.main(argv)
    return json.loads(out.read_text("utf-8"))


def test_a_conforming_set_of_five_authorises_a2_2(tmp_path: Path) -> None:
    doc = scored(tmp_path, five_records(tmp_path), FIVE)
    assert doc["verdict"] == "CONFORMING", doc["problems"]
    assert doc["a2_1_acceptance"]["verdict"] == "BATCH 3 A2.1 PRODUCER CONFORMING"
    assert doc["authorizes_a2_2"] is True
    assert len(doc["images"]) == 5
    assert all(row["status"] == "CONFORMING" and row["build_exit"] == 0
               for row in doc["images"].values()), doc["images"]


def test_one_mismatching_image_refuses_the_set(tmp_path: Path) -> None:
    """Four conforming images do not outvote a fifth that disagrees.

    This is the rule A2.2's blocking contract is built on, and it is the failure mode a
    hand-written summary would hide: the interesting image is always the one that disagreed.
    """
    doc = scored(tmp_path, five_records(tmp_path, broken="cap-frontend",
                                        broken_run="mismatch"), FIVE)
    assert doc["verdict"] == "MISMATCH"
    assert doc["authorizes_a2_2"] is False
    assert doc["a2_1_acceptance"]["verdict"] == "BATCH 3 A2.1 PRODUCER NOT CONFORMING"
    assert doc["images"]["cap-frontend"]["status"] == "MISMATCH"
    assert doc["images"]["cap-backend"]["status"] == "CONFORMING", (
        "the conforming rows stay conforming; one bad image does not rewrite the others")


def test_one_unknown_image_refuses_the_set(tmp_path: Path) -> None:
    """Four conforming and one unreadable is not four and a quarter."""
    doc = scored(tmp_path, five_records(tmp_path, broken="cap-sandbox-browser",
                                        broken_run="unknown"), FIVE)
    assert doc["verdict"] == "UNKNOWN" and doc["authorizes_a2_2"] is False
    assert doc["images"]["cap-sandbox-browser"]["status"] == "UNKNOWN"


def test_one_failed_build_refuses_the_set_even_with_a_perfect_producer(tmp_path: Path) -> None:
    """"Can the pinned producer build all five?" is half of what A2.1 has to answer.

    A record with every comparison CONFORMING and a non-zero build exit would otherwise say the
    producer works, which is true of the CLI and false of the image.
    """
    doc = scored(tmp_path, five_records(tmp_path, broken="cap-egress-proxy",
                                        broken_run="exit"), FIVE)
    assert doc["authorizes_a2_2"] is False
    assert doc["images"]["cap-egress-proxy"]["build_exit"] == 1
    assert any("did not succeed" in problem for problem in doc["problems"]), doc["problems"]


def test_a_missing_image_record_is_a_failure_of_the_set(tmp_path: Path) -> None:
    """Four of five is a missing image, not a smaller question."""
    records = five_records(tmp_path)[:-1]
    doc = scored(tmp_path, records, FIVE)
    assert doc["authorizes_a2_2"] is False
    assert doc["images"][FIVE[-1]]["status"] == "MISSING"
    assert any("no observation record" in problem for problem in doc["problems"]), doc["problems"]


def test_records_from_two_rounds_are_refused(tmp_path: Path) -> None:
    """No cherry-picking a conforming record from a run that happened to look good.

    Five records can all say CONFORMING while describing five different builds. The set is
    about one round, so a second commit in the inputs is a problem whoever wrote the records.
    """
    doc = scored(tmp_path, five_records(tmp_path, broken="cap-sandbox-http",
                                        broken_run="round"), FIVE)
    assert doc["authorizes_a2_2"] is False
    assert any("different runs/commits" in problem for problem in doc["problems"]), doc["problems"]
    assert len(doc["rounds"]) == 2, doc["rounds"]


def test_an_unexpected_image_in_the_set_is_reported(tmp_path: Path) -> None:
    """Dropping an expected image from the list is a way to make a set look clean."""
    doc = scored(tmp_path, five_records(tmp_path), FIVE[:-1])
    assert any("not an expected image" in problem for problem in doc["problems"]), doc["problems"]
    assert doc["authorizes_a2_2"] is False


def test_scoring_no_records_at_all_fails_rather_than_reading_a_machine(tmp_path: Path) -> None:
    """`--combine` with nothing to score must not fall through into an observation.

    On a runner the fall-through would have run the recorder's docker reads and written a set
    verdict built from no image at all -- the one outcome where a green `set.exit` could mean
    nothing happened.
    """
    out = tmp_path / "producer-set.json"
    code = recorder.main(["--out", str(out), "--combine"])
    assert code == 2, "an empty set is a usage failure, not a verdict"
    assert not out.exists(), out


def test_the_same_round_base_binding_is_read_out_of_the_build_itself(tmp_path: Path) -> None:
    """The browser's base digest comes from its argv, because that is the build's fact.

    Passing `--build-context` to the recorder as well would let a record state a binding the
    builder never used. Nothing here passes it on the command line: the value is read back out
    of the invocation the step recorded, which is the same route the executable's identity
    takes.
    """
    record_dir = tmp_path / "browser"
    record_dir.mkdir()
    dockerfile, flags = IMAGE_BUILDS["cap-sandbox-browser"]
    argv = [CONTROLLED, "build", "--builder", BUILDER, "--file", dockerfile, *flags,
            "outputs/producer-observation/context-browser"]
    _, payload = recorded(record_dir, observe_run(), image="cap-sandbox-browser",
                          dockerfile=dockerfile, build_argv=argv)
    base, = payload["base_images"]["bases"]
    assert base["binding"] == "same_round_oci_layout", base
    assert base["digest"] == CHILD, base
    assert base["status"] == "READ", base


def test_an_image_built_on_an_unnamed_base_refuses_the_set(tmp_path: Path) -> None:
    """A pinned producer on an un-pinned base has produced something nobody can re-derive.

    Every comparison can read CONFORMING while the image itself floats. The set rule says no
    anyway, which is the difference between measuring the producer and measuring the build.
    """
    records = five_records(tmp_path)
    browser = tmp_path / "cap-sandbox-browser/producer-evidence.json"
    payload = json.loads(browser.read_text("utf-8"))
    payload["base_images"] = recorder.base_image_bindings(
        IMAGE_BUILDS["cap-sandbox-browser"][0], ["SANDBOX_HTTP_BASE=cap-sandbox-http:latest"],
        [])
    browser.write_text(json.dumps(payload), "utf-8")
    doc = scored(tmp_path, records, FIVE)
    assert doc["authorizes_a2_2"] is False, records
    row = doc["images"]["cap-sandbox-browser"]
    assert row["status"] == "UNKNOWN" and row["comparisons"]["lock_vs_observed"] == "CONFORMING", (
        "the producer agreed; the base did not, and the row says so")
    assert any("not bound by digest" in problem for problem in doc["problems"]), doc["problems"]
