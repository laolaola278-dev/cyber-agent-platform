"""Batch 3A: the producer *observation* contract -- three authorities, seven controls.

B2's recorder pinned a builder and read one version line back. Batch 3A separates the
producer into the three things that can disagree, and keeps them apart in the record:

    configured.lock        deployment/third-party-images.json
    configured.workflow    what the workflow file itself declares, read from the file
    observed.*             what the runner, the CLI, the builder and its container answer

The tests below are the seven controls the batch specifies, plus a field-level control for
every new field. Two rules run through all of them:

* A **mismatch is evidence, never a failure.** F-44 is open precisely because the declared
  buildx and the executing buildx differ, and hiding that behind a red/green light would
  lose the measurement. So `--self-check` exits non-zero only when a field the observation
  *promised* could not be read -- a broken instrument -- and one test below asserts that a
  genuine version mismatch still exits 0.
* **An unreadable value is never a match.** `null` must not read as agreement, so every
  unknown side carries a status and a reason.

Only `docker` is stubbed. No recorder logic is stubbed, and no test here asserts that the
pins agree -- they assert what the record *says* about whether they agree.
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
                "--job", OBSERVATION_JOB]


def version_line(path: str, version: str, commit: str = "62ab6b42a64bcf84f225559ff1015b98") -> str:
    """The shape `docker buildx version` prints on a GitHub runner."""
    return f"{path} github.com/docker/buildx {version} {commit}"


def inspect_text(driver: str = "docker-container", image: str | None = None,
                 worker: str | None = None) -> str:
    node_image = BUILDKIT["image_ref"] if image is None else image
    head = (f"Name:          {BUILDER}\n"
            f"Driver:        {driver}\n"
            "Last Activity: 2026-09-23 05:00:00 +0000 UTC\n\nNodes:\n")
    node = (f"Name:      {NODE}\n"
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
                child_read_fails: bool = False):
    """A runner that answers every command the observation path issues.

    Answers are keyed by the exact argv, which makes the fixture a regression guard on the
    commands themselves: `docker buildx inspect --format`, a `docker inspect` that asks
    for a config image and a repo digest in one call, and a `docker inspect` of a
    *container* for `RepoDigests` -- an image-object field, which the daemon therefore
    refuses to template -- each broke an implementation, and each is visible here as an
    "unexpected command".
    """
    repo_digests = [f"moby/buildkit@{CHILD}"] if repo_digests is None else repo_digests
    child_config = CONFIG_ID if child_config is None else child_config
    plugins = {ACTION_PLUGIN: BUILDX["version"], SYSTEM_PLUGIN: version} if plugins is None \
        else plugins
    rows = container_rows if container_rows is not None else [
        "\t".join(("abc123def456", CONTAINER, BUILDKIT["image_ref"], "running"))]
    listed = rows[0].split("\t")[1] if rows else CONTAINER
    answers: dict[tuple[str, ...], tuple[int, str, str]] = {
        ("docker", "buildx", "version"): (0, version_line(path, version), ""),
        ("docker", "buildx", "ls"): (0, "\n".join((
            "NAME/NODE  DRIVER/ENDPOINT    STATUS   BUILDKIT  PLATFORMS",
            f"{BUILDER}*  {driver}  running  v0.33.0   linux/amd64",
            "default    docker                        ")), ""),
        ("docker", "buildx", "inspect", f"--builder={BUILDER}"): (
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
        ("docker", "buildx", "imagetools", "inspect", BUILDKIT["image_ref"], "--raw"): (
            0, index if index is not None else index_json(), ""),
        ("docker", "version", "--format", "{{.Server.Version}}"): (0, server, ""),
        ("which", "docker"): (0, "/usr/bin/docker", ""),
        ("which", "docker-buildx"): (
            (0, PATH_PLUGIN, "") if path_plugin else (1, "", "docker-buildx not found in path")),
    }
    served = index if index is not None else index_json()
    try:
        named = [item["digest"] for item in json.loads(served).get("manifests", [])
                 if (item.get("platform") or {}).get("architecture") == "amd64"]
    except (json.JSONDecodeError, AttributeError, KeyError):
        named = []
    if len(named) == 1:
        coordinate = f"moby/buildkit@{named[0]}"
        answers[("docker", "buildx", "imagetools", "inspect", coordinate, "--raw")] = (
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


def recorded(tmp_path: Path, run, *extra: str, environ=None) -> tuple[int, dict]:
    out = tmp_path / "producer-evidence.json"
    argv = ["--out", str(out), "--lock", str(LOCK_PATH), *OBSERVE_ARGS, *extra]
    code = recorder.main(argv, run=run,
                         environ=environ if environ is not None else {"HOME": HOME,
                                                                      "RUNNER_ENVIRONMENT":
                                                                      "GitHub-ACTIONS",
                                                                      "ImageOS": "ubuntu2404",
                                                                      "GITHUB_SHA": "abc123"})
    return code, json.loads(out.read_text("utf-8"))


def statuses(payload: dict) -> dict:
    return {name: payload["comparison"][name]["status"]
            for name in ("lock_vs_workflow", "workflow_vs_observed", "lock_vs_observed")}


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
    assert workflow["declared_env_keys"] == ["CAP_OBSERVE_BUILDER", "CAP_OBSERVE_BUILDKIT",
                                             "CAP_OBSERVE_DRIVER"]
    assert "expressions" not in workflow, (
        f"an unexpanded ${{{{ }}}} is not a literal pin: {workflow.get('expressions')}")


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


def test_control_2_a_different_executing_buildx_is_a_workflow_vs_observed_mismatch(
        tmp_path: Path) -> None:
    """F-44, reproduced as a field: v0.37.1 declared, v0.37.0 executed.

    The three-way model matters exactly here -- the file is internally consistent, so
    `lock_vs_workflow` reads CONFORMING while the runner disagrees with both.
    """
    _, payload = recorded(tmp_path, observe_run(version="v0.37.0"))
    assert statuses(payload) == {"lock_vs_workflow": "CONFORMING",
                                 "workflow_vs_observed": "MISMATCH",
                                 "lock_vs_observed": "MISMATCH"}
    field = payload["comparison"]["workflow_vs_observed"]["fields"]["buildx_version"]
    assert field == {"declared": BUILDX["version"], "read_back": "v0.37.0",
                     "relation": "different"}
    assert payload["producer_alignment"]["verdict"] == "MISMATCH"
    assert payload["producer_alignment"]["components"] == statuses(payload), (
        "the summary must carry the three answers, not replace them")


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
                                 "workflow_vs_observed": "UNKNOWN",
                                 "lock_vs_observed": "UNKNOWN"}
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
    assert binaries["executing"] == SYSTEM_PLUGIN and binaries["executing_version"] == "v0.37.0"
    assert payload["comparison"]["workflow_vs_observed"]["fields"]["buildx_version"][
        "read_back"] == "v0.37.0"


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

    Nothing may pick the flattering answer, so the record carries both versions, the path
    that answered, and the comparison computed against the *executing* one.
    """
    run = observe_run(version="v0.37.0", path=SYSTEM_PLUGIN,
                      plugins={ACTION_PLUGIN: "v0.37.1", SYSTEM_PLUGIN: "v0.37.0"})
    _, payload = recorded(tmp_path, run)
    binaries = payload["comparison"]["buildx_binaries"]
    assert binaries["executing"] == SYSTEM_PLUGIN
    assert binaries["executing_version"] == "v0.37.0"
    assert binaries["installed_on_disk"] == {ACTION_PLUGIN: "v0.37.1",
                                             SYSTEM_PLUGIN: "v0.37.0"}, "both, no cherry-picking"
    assert payload["observed"]["docker_cli_plugin"]["resolves_to"] == SYSTEM_PLUGIN
    assert payload["comparison"]["workflow_vs_observed"]["fields"]["buildx_version"][
        "read_back"] == "v0.37.0", "the comparison uses the binary that ran, not the newest read"


def test_control_7_everything_aligning_reads_conforming(tmp_path: Path) -> None:
    _, payload = recorded(tmp_path, observe_run())
    assert statuses(payload) == {"lock_vs_workflow": "CONFORMING",
                                 "workflow_vs_observed": "CONFORMING",
                                 "lock_vs_observed": "CONFORMING"}
    assert payload["producer_alignment"]["verdict"] == "CONFORMING"
    assert "contract_gaps" not in payload, payload.get("contract_gaps")
    assert payload["observed"]["executing_buildx"]["path"] == SYSTEM_PLUGIN


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
    assert list(compared["required"]) == ["buildx_version", "buildkit_digest"], (
        "the required layer did not move to the easier one")
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
    """No login, no push, no tag: this job can only ever produce local bytes.

    Stage 3's constraint -- a NON-PUBLISHING builder -- is checked as text, because the
    difference between "observed the producer" and "shipped an image" is exactly one flag.
    """
    job = CI["jobs"][OBSERVATION_JOB]
    script = observation_scripts()
    for forbidden in ("--push", "login-action", "ghcr.io", "--tag", "docker push"):
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
