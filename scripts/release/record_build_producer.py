"""Record the *producer* of a build, separating what was declared from what was read back.

B2 of the post-rc hardening plan pinned the builder; Batch 3A added the half that was missing,
and Batch 3 A2.1 added the half 3A could only measure. Four sources are kept apart on purpose:

    configured.lock       what deployment/third-party-images.json declares
    configured.workflow   what the workflow file declares (its own pins, its own builder name)
    configured.controlled what scripts/release/controlled_buildx.json pins the executable to be
    observed.*            read-back of the runner, the CLI, the builder, its container and the
                          controlled executable -- plus a registry read of the pinned reference

and they are compared by name (``lock_vs_workflow``, ``controlled_pin_vs_lock``,
``workflow_vs_observed``, ``lock_vs_observed``) rather than collapsed into one boolean: a
release that agrees with itself while disagreeing with the runner is exactly the F-44
situation these files were written to make visible.

A2.1 also changes *which* buildx a record is about. ``observed.controlled_buildx`` invokes the
pinned executable by absolute path and reads back its version, its commit and a hash of its
bytes; ``observed.build_invocation`` reads back the argv the build step recorded, so "the
pinned executable built these bytes" is falsifiable rather than asserted; and once that binary
answers, the builder and registry reads go through it too. What ``docker buildx`` would have
dispatched to is still read and still reported, unscored -- A2.1 removes the runner from
producer identity, it does not delete the measurement that showed the runner was there.

Nothing is invented. A read that fails is reported with its status and the complaint that
explains it; an unreadable value never becomes ``None``-and-therefore-matches, because a
blank field a reader cannot distinguish from an unrecorded one is how F-33's gate read a
verdict nobody wrote.

``--mode observe`` is the non-publishing observation path CI's producer-observation job calls,
once per image, and ``--combine`` scores those records as one round. The default mode is the
one ``build_release_image.sh`` calls, and its keys stay additive: A2.1 does not switch the
release build onto the pinned executable, so ``--mode build`` records the controlled layer as
``NOT_PROVIDED`` rather than as a producer nobody declared. ``--self-check`` fails on a broken
instrument, never on an unwelcome measurement.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path

#: One command read: exit code, stdout and stderr, with the argv that answered them.
CommandRunner = Callable[[list[str]], tuple[int, str, str]]

#: How much of a complaint to carry into the evidence. Enough to identify a usage
#: error, short enough that a stack trace cannot bury the record it explains.
ERROR_TEXT_LIMIT = 300

#: The three answers a comparison is allowed to give. A fourth would be a judgement.
CONFORMING = "CONFORMING"
MISMATCH = "MISMATCH"
UNKNOWN = "UNKNOWN"

#: Where the docker CLI looks for a `docker buildx` plugin, in the CLI's own order. The
#: action installs into the first; the runner image ships the others. Which one answers is
#: not assumed -- it is read out of the version line the CLI itself prints.
PLUGIN_DIRS = (
    "{home}/.docker/cli-plugins",
    "/usr/local/lib/docker/cli-plugins",
    "/usr/libexec/docker/cli-plugins",
    "/usr/lib/docker/cli-plugins",
)

ATTESTATION_REF_TYPE = "oci.ref.attestation"

#: The buildx every read used before Batch 3 A2.1, and the one the release path still uses:
#: `docker buildx`, whose plugin dispatch is exactly what A2.1 removes from producer identity.
#: Once a controlled executable has been read off the machine, `buildx_prefix` replaces this.
DEFAULT_BUILDX_PREFIX = ("docker", "buildx")


def run_command(argv: list[str]) -> tuple[int, str, str]:
    """The real runner: argv, no shell, exit code and both output streams handed back.

    Stderr is part of the contract on purpose. The first version of this recorder
    kept only stdout, and when `docker buildx version --format` was refused by the
    CLI the evidence said `{"error": "exit 125"}` -- the number a usage error ends
    with, with the one sentence that explains it thrown away. A reader could not
    tell a missing binary from a bad flag from a dead daemon.
    """
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, "", f"{type(error).__name__}: {error}"
    return completed.returncode, (completed.stdout or "").strip(), (completed.stderr or "").strip()


def read(run: CommandRunner, name: str, argv: list[str]) -> dict:
    """One command, recorded honestly: what it answered, or that it did not answer."""
    code, out, err = run(argv)
    command = " ".join(shlex.quote(part) for part in argv)
    first = out.splitlines()[0].strip() if out else ""
    if code != 0 or not first:
        complaint = (err.splitlines() or [""])[0].strip()[:ERROR_TEXT_LIMIT]
        return {"ok": False, "command": command, "exit": code, "status": "ERROR",
                "error": complaint or (out[:ERROR_TEXT_LIMIT] or f"exit {code}")}
    return {"ok": True, "command": command, "exit": code, "status": "READ", "value": first}


def read_all(run: CommandRunner, name: str, argv: list[str]) -> dict:
    """The same contract for output that is a table rather than a value.

    `docker buildx ls` answers with one row per builder and a marker on the default one, and
    which builder a runner considers default is exactly the kind of fact that must not be
    reduced to its first line. The text is carried verbatim and unparsed: a table the CLI
    may reformat is not something to build fields on.
    """
    code, out, err = run(argv)
    command = " ".join(shlex.quote(part) for part in argv)
    lines = [line.rstrip() for line in out.splitlines() if line.strip()]
    if code != 0 or not lines:
        complaint = (err.splitlines() or [""])[0].strip()[:ERROR_TEXT_LIMIT]
        return {"ok": False, "command": command, "exit": code, "status": "ERROR",
                "error": complaint or (out[:ERROR_TEXT_LIMIT] or f"exit {code}")}
    return {"ok": True, "command": command, "exit": code, "status": "READ", "lines": lines}


def parse_version_line(value: str) -> dict:
    """Split a buildx/docker version line into path, module, version and commit.

    The line is anchored on its module token rather than on position, because the two
    shapes this box has actually seen differ by a field: a GitHub runner prints
    `/home/runner/.docker/cli-plugins/docker-buildx github.com/docker/buildx v0.37.1 <commit>`,
    while Docker Desktop's CLI prints `github.com/docker/buildx v0.35.0-desktop.2 <commit>`
    with no install path at all. A positional parse would have filed that module name under
    `path` -- a filesystem path that does not exist, in the one field Stage 2 exists to
    answer. The raw line stays (`value`) so a reader can check the parse against the tool.
    """
    tokens = value.split()
    module_at = next((i for i, token in enumerate(tokens) if token.startswith("github.com/")),
                     None)
    first = tokens[0] if tokens else None
    looks_like_path = bool(first) and (first.startswith(("/", "\\"))
                                       or re.match(r"^[A-Za-z]:[\\/]", first))
    path = first if module_at and looks_like_path else None
    tail = tokens[module_at + 1:] if module_at is not None else tokens[1:]
    parsed = {"module": tokens[module_at] if module_at is not None else None,
              "path": path,
              "path_status": ("READ" if path else
                              "NOT_REPORTED -- the CLI printed no install path, so which "
                              "binary answered is unknown here rather than absent"),
              "version": tail[0] if tail else None,
              "commit": tail[1] if len(tail) > 1 else None,
              "build_metadata": " ".join(tail[2:]) if len(tail) > 2 else None}
    return parsed


def _ref_parts(ref: str | None) -> dict:
    """A container reference taken apart, so a tag and a digest are never confused.

    `moby/buildkit:v0.33.0` and `moby/buildkit@sha256:6c2f…` are different claims: the
    first is a pointer the vendor can move, the second is the bytes. The lock pins both,
    and the evidence records which one it is comparing -- and what a running container
    pulled by digest reports is `tag: null`, not a tag invented out of the repository name.
    """
    if not ref:
        return {"repository": None, "tag": None, "digest": None}
    left, at, digest = ref.rpartition("@")
    if not at:
        left, digest = ref, ""
    repository, colon, tag = left.rpartition(":")
    if not colon or "/" in tag:  # no colon, or a registry port: `left` is all repository
        repository, tag = left, ""
    return {"repository": repository or left, "tag": tag or None,
            "digest": digest or None, "ref": ref}


def digest_relation(pinned: dict, running: dict, children: list[dict]) -> dict:
    """How the pinned BuildKit reference relates to the image the container actually runs.

    Three layers, kept apart because collapsing them is the error this whole stage exists
    to avoid: the lock's digest is a *manifest-list* (multi-arch index), while a running
    container's `RepoDigests` entry names the *child manifest* the daemon pulled for its
    platform, and `docker inspect`'s image id is the *config* digest. None of those three
    strings is equal to another in the normal, conforming case, so equality-by-string
    would report a mismatch that is not one -- and inferring "same tag" from any of them
    would report a match that is not one either.
    """
    child_digests = [str(item.get("digest") or "") for item in children]
    pinned_digest, running_digest = pinned.get("digest"), running.get("digest")
    if not pinned_digest or not running_digest:
        return {"status": UNKNOWN,
                "reason": ("the pinned digest or the running image's digest could not be read "
                           "-- an unreadable side is never scored as a match"),
                "pinned_digest": pinned_digest, "running_digest": running_digest}
    if running_digest == pinned_digest:
        return {"status": CONFORMING, "relation": "same_digest",
                "pinned_digest": pinned_digest, "running_digest": running_digest}
    if running_digest in child_digests:
        return {"status": CONFORMING, "relation": "running_is_a_child_of_the_pinned_index",
                "pinned_digest": pinned_digest, "running_digest": running_digest,
                "index_children": child_digests}
    if children:
        return {"status": MISMATCH,
                "relation": "running_digest_is_not_named_by_the_pinned_index",
                "pinned_digest": pinned_digest, "running_digest": running_digest,
                "index_children": child_digests}
    return {"status": UNKNOWN,
            "relation": "pinned_index_unresolved",
            "reason": ("the running image is not the pinned digest and the pinned reference "
                       "could not be resolved to its child manifests, so no claim is made"),
            "pinned_digest": pinned_digest, "running_digest": running_digest}


def config_digest_relation(pinned_child: dict | None, running_config: str | None) -> dict:
    """The second digest layer: the running image's config digest against the pin's own child.

    `docker inspect` gives a container's image as a *config* digest, and the only pinned-side
    string on that layer is the `config.digest` inside the child manifest the index names for
    this platform -- which is why the registry read goes one level down. Equality here says
    the running image's content is the content the pinned index selects; it does not say the
    manifest bytes match, and it is reported beside that layer rather than instead of it.
    Nothing is inferred from a tag, an inspect line or a driver option: either both sides
    carry a config digest or the answer is UNKNOWN.
    """
    pinned = (pinned_child or {}).get("config_digest")
    left = pinned.split(":", 1)[-1] if pinned else None
    right = running_config.split(":", 1)[-1] if running_config else None
    answer = {"pinned_config_digest": pinned,
              "running_config_digest": f"sha256:{right}" if right else running_config,
              "pinned_child_manifest": (pinned_child or {}).get("manifest_digest"),
              "pinned_child_status": (pinned_child or {}).get("status")}
    if not left or not right:
        return {**answer, "status": UNKNOWN,
                "reason": ("the pinned child's config digest or the running image's config "
                           "digest could not be read -- an unreadable side is never scored as "
                           "a match")}
    return {**answer,
            "status": CONFORMING if left == right else MISMATCH,
            "relation": ("running_config_is_the_pinned_child_config" if left == right
                         else "running_config_is_not_the_pinned_child_config")}


def _inspect_sections(text: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    """The builder block and every node block of `docker buildx inspect`.

    The command's layout carries the facts: a builder header (`Name:`, `Driver:`), then a
    `Nodes:` section with per-node keys (`Name:`, `Endpoint:`, `Status:`, `Buildkit:`,
    `Flags:`, `Image:`). Labels are parsed rather than positions, so a buildx release that
    adds or reorders a line cannot silently shift a value into the wrong field.
    """
    head: dict[str, str] = {}
    nodes: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_nodes = False
    for line in text.splitlines():
        if line.strip() == "Nodes:":
            in_nodes = True
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            continue
        key, value = key.strip().lower(), value.strip()
        if not in_nodes:
            head.setdefault(key, value)
            continue
        if key == "name":  # a new node block starts at its own Name: line
            current = {"name": value}
            nodes.append(current)
            continue
        if current is None:
            current = {}
            nodes.append(current)
        if current.get(key) is None or key == "image":
            current[key] = value
    return head, nodes


def _driver_option_image(text: str | None) -> str | None:
    """The `image=` entry of a buildx `Driver Options:` line, quoted or not.

    `buildx inspect` prints the node's declared image inside the driver options, e.g.
    `image="moby/buildkit:v0.33.0@sha256:6c2f…"`. This is a *declaration* of what the builder
    was told to run, not a read of what the container runs, so it gets its own field and is
    never compared as the running identity.
    """
    for token in (text or "").replace(",", " ").split():
        key, sep, value = token.partition("=")
        if sep and key.strip() == "image":
            return value.strip().strip('"').strip("'") or None
    return None


def _buildkit_containers(run: CommandRunner) -> tuple[list[dict], str]:
    """Every container the daemon holds whose name starts with `buildx_buildkit_`.

    This is the layer that says what is *actually running*, and it is read from the daemon
    rather than derived from a naming convention -- which mattered: the first two CI
    observation runs proved that `buildx_buildkit_<builder>` is not the container a named
    builder starts (`Error: No such object: buildx_buildkit_cap3a-producer-observation`),
    while the node's own attributes name the container by its id.
    """
    record = read_all(run, "buildkit_containers", [
        "docker", "ps", "-a", "--filter", "name=buildx_buildkit_", "--format",
        "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.State}}"])
    rows = []
    for line in record.get("lines", []) if record["ok"] else []:
        parts = line.split("\t")
        if len(parts) < 2:  # `docker ps` may print a column header; it is not a container
            continue
        rows.append({"id": parts[0], "name": parts[1],
                     "image": parts[2] if len(parts) > 2 else None,
                     "state": parts[3] if len(parts) > 3 else None})
    if not record["ok"] and not rows:
        # An unreadable listing is a lost observation about the machine -- recorded, never
        # folded into "the daemon has no buildx containers".
        return [], record.get("status", "ERROR")
    return rows, record.get("status", "READ")


def observe_builder(run: CommandRunner, name: str | None = None,
                    bx: tuple[str, ...] = DEFAULT_BUILDX_PREFIX) -> dict:
    """The builder that actually ran: driver, nodes, and the BuildKit identity behind them.

    `--builder <name>` is passed when the caller named one explicitly, because `docker
    buildx inspect` without it answers for the *current* default builder -- which, on a
    runner where `setup-buildx-action` ran, is a builder nobody chose by name. Recording
    the named one is the difference between "a builder exists with these settings" and
    "the build went there".

    `bx` says which buildx asks the question. Once A2.1 has read a controlled executable off
    the machine the instrument uses it: a pinned BuildKit container read through an unpinned
    CLI would leave the runner inside the measurement that is supposed to be about the pin.
    """
    argv = [*bx, "inspect"] + ([f"--builder={name}"] if name else [])
    command = " ".join(shlex.quote(part) for part in argv)
    code, out, err = run(argv)
    if code != 0 or not out.strip():
        complaint = (err.splitlines() or [""])[0].strip()[:ERROR_TEXT_LIMIT]
        return {"ok": False, "status": "ERROR", "command": command, "exit": code,
                "requested_builder": name,
                "error": complaint or f"exit {code}",
                "reason": "the named builder could not be inspected"}
    head, node_rows = _inspect_sections(out)
    driver, builder = head.get("driver", ""), head.get("name", "")
    node = node_rows[0] if node_rows else {}
    observed: dict = {"ok": True, "status": "READ", "command": command, "exit": code,
                      "requested_builder": name, "builder": builder or None,
                      "driver": driver or None, "builder_last_activity": head.get("last activity"),
                      "nodes": node_rows,
                      # buildx v0.37 labels this `Buildkit version:`; older prints `Buildkit:`.
                      "buildkit_version": node.get("buildkit") or node.get("buildkit version"),
                      "node_image": node.get("image") or None,
                      "node_driver_options_image": _driver_option_image(node.get("driver options")),
                      "node_status": node.get("status") or None,
                      "node_endpoint": node.get("endpoint") or None,
                      "node_platforms": node.get("platforms") or None}
    if not observed["node_image"]:
        # Measured at the first CI observation run (13930dc): buildx printed no `Image:` for
        # the node at all, only `Driver Options: image="…"`. Reading that as "the container
        # cannot be identified" discarded an inspectable container; the container read below
        # is what says what runs, and the declared reference is kept as a separate layer.
        observed["node_image_note"] = (
            "buildx inspect printed no per-node Image:, so the declared reference is read "
            "from the node's Driver Options and the running bytes from the container read")
    # Which container is this builder's BuildKit? CI measured that the naming convention
    # cannot be assumed: `buildx_buildkit_<builder>` did not exist for a builder created with
    # `--name cap3a-producer-observation` ("Error: No such object"), while the node's own
    # attributes carried `org.mobyproject.buildkit.worker.hostname` -- the id of the container
    # that runs the worker. So the daemon's container list is read and joined on that id, and
    # only if the join is unavailable do the two naming conventions get used, with the reason
    # for the answer recorded beside it. A guess here costs the whole builder layer.
    worker = (node.get("org.mobyproject.buildkit.worker.hostname") or "").strip()
    inventory, inventory_status = _buildkit_containers(run)
    candidates = [f"buildx_buildkit_{part}" for part in
                  (node.get("name") or "", builder or "") if part]
    container, matched_by = "", None
    if worker:
        joined = [row for row in inventory if row["id"] == worker
                  or row["id"].startswith(worker) or worker.startswith(row["id"])]
        if len(joined) == 1:
            container, matched_by = joined[0]["name"], "worker-hostname"
    if not container:
        for candidate in candidates:
            if any(row["name"] == candidate for row in inventory):
                container, matched_by = candidate, "name-candidate-in-daemon-list"
                break
    if not container:
        container = candidates[0] if candidates else ""
        matched_by = ("guessed-from-naming-rule" if candidates else "no-container-name")
    observed["container_lookup"] = {
        "worker_hostname": worker or None, "candidates": candidates,
        "daemon_rows": inventory, "daemon_rows_status": inventory_status, "matched_by": matched_by,
        "note": ("joined on the worker hostname the builder itself reports" if matched_by
                 == "worker-hostname" else
                 "the hostname join did not resolve, so a naming convention was used and the "
                 "answer is weaker than a join"),
    }
    image = read(run, "container_image", ["docker", "inspect", "--format",
                                          "{{.Config.Image}}	{{.Image}}", container])
    if not image["ok"]:
        # Only the docker driver has an excuse for no container to inspect: it runs the
        # engine's embedded BuildKit. With a docker-container builder that container *is*
        # the builder, so a failed read is a lost observation, and the two must not be
        # recorded in the same shape.
        if driver == "docker":
            return {**observed, "error": image["error"], "status": "NOT_APPLICABLE",
                    "reason": ("the docker driver runs the engine's embedded BuildKit, so no "
                               "BuildKit container or image digest belongs to this build")}
        return {**observed, "ok": False, "status": "ERROR",
                "error": f"{container} is this builder's BuildKit container and could not be "
                         f"read: {image['error']}"}
    reference, _, image_id = image["value"].partition("	")
    # The running container's *pull digest* is a different fact from its config id, so it is
    # asked for on its own -- one command per layer of evidence, never one format string that
    # quietly makes two claims depend on the same parse. The object matters as much as the
    # separation: `RepoDigests` belongs to an image, so asking a container for it is answered
    # "map has no entry for key RepoDigests" and the manifest layer then reads as absent
    # forever. The container's own `.Image` names the image to ask.
    digest_target = image_id or reference or container
    digests = read(run, "image_repo_digests", ["docker", "image", "inspect", "--format",
                                               "{{json .RepoDigests}}", digest_target])
    platform = read(run, "image_platform", ["docker", "image", "inspect", "--format",
                                            "{{.Os}}/{{.Architecture}}", digest_target])
    repo_digests = []
    if digests["ok"]:
        try:
            repo_digests = json.loads(digests["value"]) or []
        except json.JSONDecodeError:
            observed["repo_digest_parse_error"] = digests["value"][:ERROR_TEXT_LIMIT]
    else:
        observed["repo_digests_status"] = {"status": digests["status"],
                                           "error": digests.get("error")}
    observed["repo_digests_read"] = {"command": digests["command"], "status": digests["status"],
                                     "asked_of": digest_target,
                                     "note": ("RepoDigests is an image-object field: this read "
                                              "targets the image id the container reports, so "
                                              "an empty answer means the daemon recorded no "
                                              "pull digest rather than the question failing")}
    running = _ref_parts(repo_digests[0]) if repo_digests else {"digest": None}
    config_digest = image_id.split(":", 1)[1] if image_id.startswith("sha256:") else None
    # Two different digests live here and they are never interchangeable: `.Image` is the
    # image *config* digest, `RepoDigests[]` is the manifest digest the daemon pulled. Only
    # the second can ever be compared against a pinned index digest or its children, so
    # `running_image.digest` carries the manifest side alone and `config_digest` is labelled
    # as what it is.
    if not repo_digests:
        observed["running_manifest_digest"] = {
            "status": "UNKNOWN",
            "reason": ("the image the container runs reports no RepoDigests, so only its "
                       "config digest is known -- a config digest is not a manifest digest "
                       "and is never compared against the pin by string")}
    return {**observed, "container": container,
            "container_config_image": reference or None,
            "image_id": image_id or None,
            "container_image_id": image_id or None,
            "repo_digests": repo_digests,
            "image_platform": (platform.get("value") or "").strip() or None,
            "image_platform_status": platform.get("status"),
            "container_reference": (repo_digests[0] if repo_digests else reference) or None,
            "running_image": {**running, "image_id": image_id or None,
                              "config_digest": config_digest},
            "config_digest": config_digest,
            "digest": config_digest}


def resolve_pinned_ref(run: CommandRunner, ref: str | None,
                       target_platform: str | None = None,
                       bx: tuple[str, ...] = DEFAULT_BUILDX_PREFIX) -> dict:
    """Ask the *registry* what the pinned BuildKit reference contains -- a separate layer.

    The pinned digest is a manifest-list, so the only way to check a running child
    against it without trusting a tag is to read the index itself. `imagetools` goes to
    the registry rather than the daemon, which keeps this from becoming "the daemon says
    it pulled what I asked for". Attestation entries (`unknown/unknown`) are kept but
    labelled, because they are descriptors with nothing to do with the runnable images.

    One step further: the child for the platform the running image reports is fetched as
    well, because a container's `.Image` is a *config* digest and the only pinned-side
    config digest that can be compared with it lives inside that child manifest. Asking
    the index alone leaves the two layers permanently incommensurable -- which is exactly
    how the first conforming observation run came to report `UNKNOWN` about a builder that
    was in fact running the pinned bytes.
    """
    if not ref:
        return {"ok": False, "status": "NOT_APPLICABLE",
                "error": "no pinned BuildKit reference was declared"}
    code, out, err = run([*bx, "imagetools", "inspect", ref, "--raw"])
    if code != 0 or not out.strip():
        return {"ok": False, "status": "ERROR", "ref": ref, "exit": code,
                "error": ((err.splitlines() or [""])[0].strip() or out)[:ERROR_TEXT_LIMIT]}
    try:
        doc = json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "status": "ERROR", "ref": ref,
                "error": f"the registry answered non-JSON: {out[:120]!r}"}
    children, attestations = [], []
    for item in doc.get("manifests", []) if isinstance(doc, dict) else []:
        platform = item.get("platform") or {}
        entry = {"digest": item.get("digest"), "media_type": item.get("mediaType"),
                 "platform": f"{platform.get('os', '')}/{platform.get('architecture', '')}"
                             + (f"/{platform['variant']}" if platform.get("variant") else ""),
                 "attestation": (platform.get("architecture") == "unknown"
                                 or (item.get("annotations") or {}).get(
                                     "vnd.docker.reference.type") == "attestation-manifest")}
        (attestations if entry["attestation"] else children).append(entry)
    return {"ok": True, "status": "READ", "ref": ref,
            "media_type": (doc.get("mediaType") if isinstance(doc, dict) else None),
            "is_index": bool(isinstance(doc, dict) and doc.get("manifests")),
            "children": children, "attestation_descriptors": attestations,
            "platform_child": _pinned_platform_child(run, ref, children, target_platform, bx)}


def _pinned_platform_child(run: CommandRunner, ref: str, children: list[dict],
                           target_platform: str | None,
                           bx: tuple[str, ...] = DEFAULT_BUILDX_PREFIX) -> dict:
    """The pinned index's entry for one platform, read down to its config digest."""
    if not target_platform:
        return {"platform": None, "status": "NOT_REQUESTED",
                "reason": ("the running image's platform was not read, so no child of the "
                           "pinned index is being claimed as its counterpart")}
    named = [child for child in children if child.get("platform") == target_platform]
    if len(named) != 1:
        return {"platform": target_platform,
                "status": "AMBIGUOUS" if named else "NOT_NAMED",
                "candidates": [child.get("digest") for child in named],
                "reason": (f"the pinned index names {len(named)} children for "
                           f"{target_platform}; a child is only compared when exactly one is "
                           "named")}
    repository = _ref_parts(ref)["repository"]
    child_digest = named[0]["digest"]
    coordinate = f"{repository}@{child_digest}" if repository else None
    if not coordinate or not child_digest:
        return {"platform": target_platform, "status": "ERROR",
                "error": f"the child {child_digest!r} cannot be addressed on {repository!r}"}
    code, out, err = run([*bx, "imagetools", "inspect", coordinate, "--raw"])
    child = {"platform": target_platform, "manifest_digest": child_digest,
             "coordinate": coordinate}
    if code != 0 or not out.strip():
        return {**child, "status": "ERROR", "exit": code,
                "error": ((err.splitlines() or [""])[0].strip()
                          or out)[:ERROR_TEXT_LIMIT]}
    try:
        doc = json.loads(out)
    except json.JSONDecodeError:
        return {**child, "status": "ERROR",
                "error": f"the registry answered non-JSON for {coordinate}"}
    config = (doc.get("config") or {}) if isinstance(doc, dict) else {}
    if not config.get("digest"):
        return {**child, "status": "ERROR",
                "error": "the child manifest names no config digest, so nothing can be "
                         "compared against the running image id"}
    return {**child, "status": "READ", "config_digest": config["digest"],
            "media_type": doc.get("mediaType"), "layers": len(doc.get("layers") or [])}


def executing_buildx(run: CommandRunner, plugins: dict | None = None) -> dict:
    """The buildx that answered `docker buildx` -- which is not necessarily the installed one.

    The version line's first field is the binary the CLI plugin machinery dispatched to *when
    the CLI prints one*. It does not always: this box's Docker Desktop prints
    `github.com/docker/buildx v0.35.0-desktop.2 <commit>`, and the shape is the same one CI's
    own producer records carry. A blank path would then be the record's answer to the only
    question Stage 2 asks, so the resolution falls back to the binaries actually on disk: a
    candidate reporting the same version **and** commit as the executing line is the same
    bytes, and when exactly one such candidate exists the binary is identified -- labelled as
    a match, never as a printed fact. Several matching candidates are ambiguous and none is
    unidentified; both stay gaps rather than becoming a guess.
    """
    record = read(run, "executing_buildx", ["docker", "buildx", "version"])
    if record["ok"]:
        record.update(parse_version_line(record["value"]))
    which = read(run, "docker_cli", ["which", "docker"])
    record["docker_cli_path"] = which.get("value") if which["ok"] else None
    if not which["ok"]:
        record["docker_cli_path_status"] = which.get("status")
    candidates = (plugins or {}).get("candidates") or {}
    matches = [path for path, entry in candidates.items()
               if entry.get("version") and entry.get("version") == record.get("version")
               and entry.get("commit") == record.get("commit")]
    if record.get("path"):
        record["resolved_path"] = record["path"]
        record["identified_by"] = "install path printed by the CLI"
    elif record.get("ok") and len(matches) == 1:
        record["resolved_path"] = matches[0]
        record["identified_by"] = ("the only installed candidate whose version and commit "
                                   "equal the executing line's")
    else:
        record["resolved_path"] = None
        record["identified_by"] = (
            "not identified: the CLI printed no install path and "
            + ("no installed candidate answers with this version and commit" if not matches
               else f"{len(matches)} installed candidates answer identically, so the dispatch "
                    "target is ambiguous")
            + (" -- the candidates are recorded beside this, and none of them is a guess"
               if matches else ""))
        if matches:
            record["ambiguous_candidates"] = matches
    return record


def cli_plugins(run: CommandRunner, environ) -> dict:
    """Every candidate `docker-buildx` plugin on disk, each asked its own version.

    This is where "the action installed it" and "the CLI used it" are told apart: both the
    user-level plugin directory the action writes to and the system directories the runner
    image ships are recorded, so a version difference is two measurements rather than a
    mystery. Nothing here cherry-picks the convenient one -- `resolves_to` says which of
    these the CLI actually dispatched to.
    """
    home = environ.get("HOME") or os.path.expanduser("~")
    candidates = [str(Path(template.format(home=home)) / "docker-buildx")
                  for template in PLUGIN_DIRS]
    # The four plugin directories are not the whole story: CI's first observation run found
    # `docker-buildx` in none of them, because `setup-buildx-action` puts one where the docker
    # CLI also looks (PATH, and /usr/local/bin ahead of it). Asking the shell resolves both
    # possibilities with the tool that actually has to answer, and the result is scanned like
    # any other candidate -- version and commit read back out of the binary itself.
    which = read(run, "docker_buildx_in_path", ["which", "docker-buildx"])
    path_status = "READ"
    if which["ok"] and which["value"] not in candidates:
        candidates.append(which["value"])
    elif not which["ok"]:
        path_status = which["status"]
    else:
        path_status = "READ"
    found: dict[str, dict] = {}
    for path in candidates:
        record = read(run, f"plugin:{path}", [path, "version"])
        entry = {"exists": bool(record["ok"]), "status": "READ" if record["ok"] else "ERROR"}
        if record["ok"]:
            entry.update(parse_version_line(record["value"]))
        else:
            entry["error"] = record.get("error")
        found[path] = entry
    return {"candidates": found,
            "any_installed": [p for p, e in found.items() if e["exists"]],
            "path_lookup": {"status": path_status,
                            "error": which.get("error") if which["ok"] is False else None},
            "resolves_to": None}


def buildx_prefix(controlled: dict | None) -> tuple[str, ...]:
    """Which buildx the *instrument* runs on, kept separate from which one built.

    Once a controlled executable has been read back from the machine, the recorder asks the
    builder and the registry through that binary rather than through `docker buildx`: an
    observation that reads a pinned BuildKit container with an unpinned CLI is measuring the
    runner again. With no controlled path the prefix stays `docker buildx`, which is what the
    release path still uses -- A2.1 observes, it does not switch anything.
    """
    path = (controlled or {}).get("path")
    if path and (controlled or {}).get("status") == "READ":
        return (path,)
    return DEFAULT_BUILDX_PREFIX


def load_controlled_pin(path: str | None) -> dict:
    """What the repository declares about the one buildx executable it will run.

    Read from `scripts/release/controlled_buildx.json` rather than from the job: the lock file
    under `deployment/` is what the shipped deployment reads (F-20), and a CI-time executable
    pin does not belong in it. Both name a buildx version, so the two are *compared*, not
    merged -- a pin bumped without the lock is a finding about the repository.
    """
    if not path or not Path(path).exists():
        return {"ok": False, "status": "NOT_PROVIDED",
                "error": f"no controlled buildx pin at {path!r}"}
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return {"ok": False, "status": "ERROR", "source": path,
                "error": f"{type(error).__name__}: {error}"[:ERROR_TEXT_LIMIT]}
    return {"ok": True, "status": "READ", "source": path,
            "version": doc.get("version"),
            "expected_git_commit": doc.get("expected_git_commit"),
            "expected_sha256": (doc.get("integrity") or {}).get("expected"),
            "integrity_algorithm": (doc.get("integrity") or {}).get("algorithm"),
            "download_url": (doc.get("source") or {}).get("download_url"),
            "platform": (doc.get("source") or {}).get("platform"),
            "declared_path": (doc.get("install") or {}).get("path")}


def observe_controlled_buildx(run: CommandRunner, pin: dict, declared_path: str | None) -> dict:
    """Ask the pinned executable who it is, and hash the bytes it is made of.

    Two independent reads, because either one alone can be satisfied by the wrong binary:

    * `version` -- the pinned path is invoked directly and answers with its version and the
      commit it was built from. A standalone buildx prints **no install path** (measured at CI
      run `35866400091`), so this read identifies the bytes, not the file: `path` below is the
      path that was invoked, and `path_status` says whether the binary corroborated it.
    * `sha256sum` -- the file the job is about to run, hashed on the machine that runs it. The
      expected value comes from the repository's pin, not from the server the file came from,
      so a replaced release asset or a truncated download cannot pass by being self-consistent.

    Together those two, plus the recorded build argv, are what say which executable built the
    image. A missing or unreadable file is ERROR, never "unknown version, so nothing disagrees".
    """
    path = declared_path or (pin or {}).get("declared_path")
    answer = {"declared_path": path, "pin_source": (pin or {}).get("source")}
    if not (pin or {}).get("ok") and not declared_path:
        # The release build path passes no pin, because it still builds through `docker buildx`.
        # That is a stated absence, not a failed read: calling it ERROR would make every existing
        # release record look like a broken instrument for a change it was never asked to make.
        return {**answer, "ok": False, "status": "NOT_PROVIDED",
                "reason": ("no controlled buildx pin was given, so this record describes a build "
                           "whose executable the repository does not pin -- the release path "
                           "until A2.2 switches it, the observation job as a defect after A2.1"),
                "integrity": {"status": "NOT_PROVIDED"}}
    if not path:
        return {**answer, "ok": False, "status": "ERROR",
                "error": ((pin or {}).get("error")
                          or "no controlled buildx path was declared, so no executable can be "
                             "named as the producer"),
                "integrity": {"status": "ERROR",
                              "reason": "no file was named, so nothing was hashed"}}
    if not (pin or {}).get("ok"):
        # A path without a readable pin is not "no requirement": the job installed something and
        # nothing declares what its bytes should be. The reads still happen -- an unreadable
        # expected digest shows up as an integrity ERROR, which is the honest report -- but the
        # record says out loud that the comparison has no declared side.
        answer["pin_error"] = (pin or {}).get("error") or "no pin was readable"
    record = read(run, "controlled_buildx_version", [path, "version"])
    if record["ok"]:
        # parse_version_line puts the leading token in `path` only when it looks like one, and
        # a standalone binary's own line always does.
        answer.update({key: value for key, value in parse_version_line(record["value"]).items()
                       if key in ("version", "commit", "build_metadata", "module")})
        answer["status"] = "READ"
        answer["value"] = record["value"]
        answer["path_reported_by_binary"] = parse_version_line(record["value"]).get("path")
        answer["path"] = answer["path_reported_by_binary"] or path
        answer["path_status"] = ("READ" if answer["path_reported_by_binary"] else
                                 "NOT_REPORTED -- the binary printed no argv[0], so the path "
                                 "carried here is the one that was invoked, not one it stated")
    else:
        answer["status"] = "ERROR"
        answer["error"] = record.get("error")
        answer["command"] = record.get("command")
        answer["path"] = path
    expected = (pin or {}).get("expected_sha256")
    hashed = read(run, "controlled_buildx_sha256", ["sha256sum", path])
    computed = None
    if hashed["ok"]:
        computed = hashed["value"].split()[0].strip()
    integrity = {"expected": expected, "computed": (f"sha256:{computed}" if computed else None),
                 "command": hashed.get("command")}
    if not expected or not computed:
        integrity["status"] = "ERROR"
        integrity["reason"] = (
            "the pin names no expected digest" if not expected else
            "sha256sum did not answer for the controlled path")
    else:
        match = computed == expected.split(":", 1)[-1]
        integrity["status"] = "CONFORMING" if match else "MISMATCH"
        integrity["relation"] = "equal" if match else "different"
    answer["integrity"] = integrity
    answer["ok"] = answer["status"] == "READ"
    return answer


def observe_build_invocation(run: CommandRunner, command_file: str | None,
                             controlled_path: str | None) -> dict:
    """Which executable the *build* actually ran under -- read from the job, not from intent.

    The observation step writes the argv it used into a file and the recorder reads it back.
    Without that, "the controlled binary built this image" would be a sentence in this script;
    with it, the claim is falsifiable: an argv headed `docker buildx` is a plugin dispatch and
    scores MISMATCH even when every declared value agrees with itself.
    """
    if not command_file:
        return {"status": "NOT_PROVIDED", "required_by": "observe mode",
                "error": "no build-command file was given, so what executed is unrecorded"}
    if not Path(command_file).exists():
        return {"status": "ERROR", "source": command_file,
                "error": f"no build-command file at {command_file!r}"}
    try:
        doc = json.loads(Path(command_file).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return {"status": "ERROR", "source": command_file,
                "error": f"{type(error).__name__}: {error}"[:ERROR_TEXT_LIMIT]}
    argv = [str(part) for part in (doc.get("argv") or [])]
    answer = {"status": "READ" if argv else "ERROR", "source": command_file, "argv": argv,
              "argv0": argv[0] if argv else None,
              "image": doc.get("image"), "build_exit": doc.get("exit")}
    if not argv:
        answer["error"] = "the recorded build command carried no argv"
        return answer
    through_cli = argv[0] == "docker" and len(argv) > 1 and argv[1] == "buildx"
    answer["dispatch"] = ("docker CLI plugin" if through_cli else
                          "invoked directly" if "/" in argv[0] else "unqualified executable")
    if controlled_path:
        match = argv[0] == controlled_path
        answer["controlled"] = {"declared": controlled_path, "invoked": argv[0],
                                "relation": "equal" if match else "different",
                                "status": "CONFORMING" if match else "MISMATCH"}
    else:
        answer["controlled"] = {"status": "ERROR",
                                "reason": "no controlled executable was declared to have run it"}
    return answer


def build_flags_from_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    """The `--build-arg` and `--build-context` values a recorded build actually carried.

    Read back rather than re-typed: the same-round base binding is a fact about the build, and
    the build's own argv is the evidence for it. Repeating the value on the recorder's command
    line would let a record state a binding the builder never used, which is the restatement
    this whole file is written to avoid.
    """
    build_args: list[str] = []
    contexts: list[str] = []
    for index, token in enumerate(argv):
        if token not in ("--build-arg", "--build-context") or index + 1 >= len(argv):
            continue
        value = argv[index + 1]
        (build_args if token == "--build-arg" else contexts).append(value)
    return build_args, contexts


def _merge_flags(cli_values: list[str], recorded_values: list[str]) -> list[str]:
    """One entry per key, with the recorded build winning over what was typed by hand."""
    merged: dict[str, str] = {}
    for token in list(cli_values) + list(recorded_values):
        key, sep, value = token.partition("=")
        if sep:
            merged[key] = value
    return [f"{key}={value}" for key, value in merged.items()]


def base_image_bindings(dockerfile: str | None, build_args: list[str],
                        named_contexts: list[str]) -> dict:
    """What each image was built ON, with the reference that actually resolved.

    `cap-sandbox-browser` has no default base: the HTTP sandbox image of the same round is
    handed to it as a named build context (`--build-context name=oci-layout://dir@sha256:...`),
    so the evidence has to say which *bytes* the base was -- a tag would be a pointer somebody
    else can move. The resolution mirrors what the builder does: the Dockerfile's `ARG` default,
    overridden by `--build-arg`, then replaced by a named context when the resulting name is one.
    """
    if not dockerfile or not Path(dockerfile).exists():
        return {"status": "NOT_PROVIDED", "error": f"no Dockerfile at {dockerfile!r}", "bases": []}
    text = Path(dockerfile).read_text(encoding="utf-8")
    args = dict(re.findall(r"^ARG\s+(\w+)=(\S+)", text, re.MULTILINE))
    for token in build_args:
        if "=" in token:
            name, _, value = token.partition("=")
            args[name] = value
    contexts = {}
    for token in named_contexts:
        name, sep, value = token.partition("=")
        if sep:
            contexts[name] = value
    bases = []
    for ref in re.findall(r"^FROM\s+(\S+)", text, re.MULTILINE):
        entry = {"declared_in_dockerfile": ref}
        if ref.startswith("${"):
            key = ref[2:-1]
            ref = args.get(key, "")
            entry["resolved_from_arg"] = key
        if not ref:
            bases.append({**entry, "status": "ERROR",
                          "error": "the base resolves to nothing in this Dockerfile"})
            continue
        binding = {"status": "READ", "ref": ref}
        if ref in contexts:
            value = contexts[ref]
            binding["named_context"] = value
            if value.startswith("oci-layout://"):
                location, _, digest = value.partition("@")
                binding["binding"] = "same_round_oci_layout"
                binding["digest"] = digest or None
                binding["layout"] = location.removeprefix("oci-layout://")
                if not digest:
                    binding["status"] = "MISMATCH"
                    binding["error"] = ("a same-round base named without a digest is not bound "
                                        "immutably: the layout directory could be re-written")
            else:
                binding["binding"] = "named_context"
                _left, _, digest = value.rpartition("@")
                binding["digest"] = digest if digest.startswith("sha256:") else None
        else:
            _left, _, tail = ref.rpartition("@")
            binding["binding"] = "registry_reference"
            binding["digest"] = tail if tail.startswith("sha256:") else None
            if not binding["digest"]:
                # The rule reads the *normalised* digest, not the raw split: a reference with
                # no "@" leaves the whole string in rpartition's last field, which would make
                # every tag-only base look "not empty" and therefore pinned.
                binding["status"] = "MISMATCH"
                binding["error"] = "the base names a tag but no digest, so it is not pinned"
        bases.append({**entry, **binding})
    return {"status": "READ", "bases": bases,
            "note": ("digests are the ones the build resolved, read out of the Dockerfile and "
                     "the arguments it was given -- not re-stated from the produced image")}


def docker_cli_build_note(engine_version, buildx_version) -> dict:
    """What `docker build` means for a builder: the daemon's kit produced the bytes."""
    return {
        "ok": True, "status": "READ",
        "build_path": "docker CLI (`docker build`)",
        "driver": "engine-embedded BuildKit",
        "engine_version": engine_version,
        "buildx_cli": buildx_version,
        "note": ("`docker build` runs the daemon's embedded BuildKit, so no BuildKit "
                 "container or image digest belongs to this build; the builder that "
                 "`buildx inspect` names did not produce these bytes"),
    }


def observe(run: CommandRunner, docker_cli_build: bool = False, builder_name: str | None = None,
            environ=None, controlled_pin: dict | None = None,
            build_command_file: str | None = None,
            controlled_path: str | None = None) -> dict:
    """Every producer field the tooling can be asked about, read back.

    Order matters here. The controlled executable is read first because once it answers, it is
    the buildx the *instrument* uses for the builder and registry reads too; and the `docker
    buildx` plugin dispatch is still read and still recorded, because "what the runner would have
    given you" is the fact F-44 was filed over and A2.1 must not delete it to look clean.
    """
    env = environ if environ is not None else os.environ
    controlled = observe_controlled_buildx(run, controlled_pin or {}, controlled_path)
    bx = buildx_prefix(controlled)
    invocation = observe_build_invocation(run, build_command_file,
                                          controlled.get("declared_path"))
    buildx = read(run, "buildx_version", ["docker", "buildx", "version"])
    if buildx["ok"]:
        buildx.update(parse_version_line(buildx["value"]))
    engine = read(run, "docker_server", ["docker", "version", "--format", "{{.Server.Version}}"])
    builder = observe_builder(run, builder_name, bx)
    plugins = cli_plugins(run, env)
    # The candidates are read first because they are the second evidence route: a CLI that
    # prints no install path can still have its executing binary identified by matching the
    # version and commit against what is actually on disk.
    executing = executing_buildx(run, plugins)
    plugins["resolves_to"] = executing.get("resolved_path")
    if docker_cli_build and not builder.get("ok"):
        builder = {**docker_cli_build_note(engine.get("value"), buildx.get("version")),
                   "inspected_builder": builder}
    return {"buildx_version": buildx, "builder": builder, "docker_engine": engine,
            "executing_buildx": executing, "docker_cli_plugin": plugins,
            "controlled_buildx": controlled, "build_invocation": invocation,
            "buildx_reads_via": list(bx),
            "builders": read_all(run, "buildx_ls", [*bx, "ls"]),
            "engine": {**engine, "version": engine.get("value")}}


def runner_identity(environ) -> dict:
    """What the runner says about itself, absent where it is not a hosted runner."""
    keys = ("RUNNER_ENVIRONMENT", "RUNNER_NAME", "ImageOS", "ImageVersion", "RUNNER_ARCH",
            "RUNNER_TEMP", "GITHUB_RUN_ID", "GITHUB_SHA")
    return {key.lower(): environ.get(key) for key in keys if environ.get(key)}


def configured_values(lock: dict, buildkit_entry: str, buildx_entry: str = "buildx") -> dict:
    """What this repository declares, kept apart from what ran.

    Both halves come from `deployment/third-party-images.json` rather than from an
    environment variable, so the release cannot record a pin it did not ship: that file is
    the same one the deployment gates already read, and a workflow that disagrees with it
    is a finding -- `configured.workflow` is where that disagreement becomes visible.
    """
    image = next((item for item in lock["images"] if item["name"] == buildkit_entry), None)
    binary = next((item for item in lock.get("binaries", []) if item["name"] == buildx_entry),
                  None)
    ref = image["image_ref"] if image else None
    parts = _ref_parts(ref)
    lock_block = {
        "buildx_version": binary.get("version") if binary else None,
        "buildx_source": binary.get("source") if binary else None,
        "buildkit_image": ref,
        "buildkit_repository": parts["repository"],
        "buildkit_tag": parts["tag"],
        "buildkit_digest": parts["digest"],
        "source": "deployment/third-party-images.json",
        "entries": {"buildkit": buildkit_entry if image else None,
                    "buildx": buildx_entry if binary else None},
    }
    return {**lock_block, "lock": lock_block,
            "buildx_version": lock_block["buildx_version"],
            "buildkit_image": ref,
            "lock_entries": {"buildkit": buildkit_entry if image else None,
                             "buildx": buildx_entry if binary else None}}


def _step_with(job: dict, needle: str) -> dict:
    """The `with:` block of the first step in `job` that mentions `needle`."""
    for step in job.get("steps", []):
        if needle in str(step.get("uses", "")) or needle in str(step.get("name", "")):
            return step.get("with") or {}
    return {}


def configured_workflow_values(path: str | None, job_name: str | None,
                               builder_name: str | None) -> dict:
    """What the workflow file itself declares -- read from the file, not from a copy.

    The point is a second authority: a lock that says `v0.37.1` while the workflow says
    something else is a real defect, and it can only be caught by reading both. The
    builder name and driver come from the observation job's own declared environment
    because `docker buildx create` takes them as arguments, and a value a script builds
    from a variable cannot be pinned down in YAML.
    """
    if not path or not Path(path).exists():
        return {"ok": False, "status": "NOT_PROVIDED",
                "error": f"no workflow file was given to read ({path!r})"}
    import yaml  # local import: the legacy build path does not need PyYAML at all

    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    job = (doc.get("jobs") or {}).get(job_name or "") or {}
    if not job:
        return {"ok": False, "status": "NOT_PROVIDED",
                "error": f"job {job_name!r} is not in {path}"}
    step = _step_with(job, "setup-buildx-action")
    driver_opts = str(step.get("driver-opts") or "")
    image_ref = None
    for token in driver_opts.replace("\n", ",").split(","):
        key, sep, value = token.partition("=")
        if sep and key.strip() == "image":
            image_ref = value.strip()
    # The observation job names its builder, its driver and its BuildKit reference once, in
    # its `env:` block, and passes `$CAP_OBSERVE_*` to `docker buildx create`. Reading them
    # from the file keeps `configured.workflow` a claim about the YAML: a builder name the
    # recorder was *told* on argv is not something the workflow declared, and a comparison
    # against it would be the recorder agreeing with its own argument.
    declared_env: dict[str, str] = {}
    expressions: dict[str, str] = {}

    def collect(source: dict) -> None:
        for key, value in (source or {}).items():
            if not str(key).startswith("CAP_OBSERVE_"):
                continue
            if not isinstance(value, str):
                continue
            # `${{ … }}` is a declaration the file cannot answer for: the value exists only
            # once the expression engine has run, so comparing it as a string would be a
            # comparison against an expression, not against a pin.
            if "${{" in value:
                expressions[str(key)] = value
                continue
            declared_env[str(key)] = value.strip()

    collect(job.get("env"))  # step-level declarations win: they are the more specific claim
    for candidate in job.get("steps", []):
        collect(candidate.get("env"))
    builder = declared_env.get("CAP_OBSERVE_BUILDER") or builder_name
    driver = declared_env.get("CAP_OBSERVE_DRIVER")
    buildkit = declared_env.get("CAP_OBSERVE_BUILDKIT") or image_ref
    declared = {"buildx_version": step.get("buildx-version"),
                "builder_name": builder, "driver": driver,
                "buildkit_image": buildkit,
                "driver_opts_image": image_ref,
                # A2.1's two new claims, read out of the same env: block as the builder name.
                # The path is a literal because a producer identity assembled from
                # `${{ runner.temp }}` is a claim about the runner, not about the repository.
                "controlled_buildx_path": declared_env.get("CAP_OBSERVE_BUILDX_PATH"),
                "controlled_pin_file": declared_env.get("CAP_OBSERVE_BUILDX_PIN"),
                "declared_env_keys": sorted(declared_env),
                "source": path, "job": job_name}
    if expressions:
        declared["expressions"] = expressions
    if buildkit:
        parts = _ref_parts(buildkit)
        declared.update({f"buildkit_{key}": value
                         for key, value in parts.items() if key != "ref"})
    return {"ok": True, "status": "READ", **declared}


def _controlled_path_field(workflow: dict, controlled: dict) -> dict:
    """The declared executable path against the path the binary itself named, if it names one.

    Measured at CI run `35866400091`: `/tmp/cap-a21-controlled-buildx/buildx version` answered
    `github.com/docker/buildx v0.37.1 0b265a9f…` with **no path at all** -- the design assumption
    that a standalone buildx prints its own argv[0] came from a version-line shape this
    instrument had only ever *parsed*, never observed on a runner. So this field cannot be a
    requirement: requiring it would leave every real observation UNKNOWN, and the temptation
    then is to make the comparison read the invoked path against itself, which agrees by
    construction and proves nothing.

    Path identity is therefore carried by two fields that always answer: the hash of the file at
    the declared path (`controlled_buildx_integrity`) and the path the build was actually
    invoked with (`build_invoked_controlled_executable`). Where a buildx *does* print a path,
    this field compares it and a contradiction blocks the comparison -- `blocking` in
    `workflow_vs_observed` -- so the optional evidence can never be quietly dropped.
    """
    declared = workflow.get("controlled_buildx_path")
    reported = controlled.get("path_reported_by_binary")
    if not reported:
        return {"declared": declared, "read_back": None, "relation": None, "scored": False,
                "status": controlled.get("path_status") or "NOT_READ",
                "why": ("the pinned executable printed no install path, so no path claim is "
                        "scored here; identity rests on the digest of the file at the declared "
                        "path and on the recorded build argv")}
    return {"declared": declared, "read_back": reported,
            "relation": "equal" if reported == declared else "different"}


def _pair(left, right) -> dict:
    return {"declared": left, "read_back": right,
            "relation": (None if left is None or right is None
                         else ("equal" if left == right else "different"))}


def _status_of(fields: dict, required: tuple[str, ...],
               blocking: tuple[str, ...] = ()) -> str:
    """CONFORMING only when every required field is present and equal.

    UNKNOWN wins over MISMATCH nowhere: a missing side is reported as UNKNOWN so that an
    unreadable observation can never be read as agreement, and a field that is present and
    unequal is reported even when another field is unreadable -- hiding a known
    disagreement behind an unknown one would be the same mistake in the other direction.

    `blocking` names the fields that may not disagree without being *required* to answer. A
    value that is only sometimes observable -- the install path a buildx prints for itself,
    which CI measured that no runner's buildx actually prints -- cannot be a requirement without
    making the whole comparison permanently UNKNOWN; but when it *is* observed and it
    contradicts the declaration, that is a disagreement and must not be filed away as
    information.
    """
    contradiction = [name for name in blocking
                     if fields.get(name, {}).get("relation") == "different"]
    if contradiction:
        return MISMATCH
    known = [name for name in required if fields.get(name, {}).get("relation") is not None]
    if not known:
        return UNKNOWN
    if any(fields[name]["relation"] == "different" for name in known):
        return MISMATCH
    return CONFORMING if len(known) == len(required) else UNKNOWN


def compare(configured: dict, observed: dict, pin: dict | None = None) -> dict:
    """The named comparisons M3 asks for, each with its own answer.

    One boolean would let a release agree with its own YAML while disagreeing with the
    machine that built it -- which is precisely what F-44 was. `configured.lock` and
    `configured.workflow` are two authorities that must agree with each other before
    either can be believed against the runner, and A2.1 adds a third declared authority --
    the controlled-executable pin -- which is compared rather than merged.
    """
    lock = configured.get("lock") or {}
    workflow = configured.get("workflow") or {}
    controlled_pin = configured.get("controlled") or {}
    executing = observed.get("executing_buildx") or {}
    controlled = observed.get("controlled_buildx") or {}
    invocation = observed.get("build_invocation") or {}
    builder = observed.get("builder") or {}
    cli_plugin_buildx = executing.get("version") or (observed.get("buildx_version") or {}
                                                     ).get("version")
    # Which buildx a comparison is *about*. A2.1's whole claim is that the producer is the
    # controlled executable rather than whatever plugin the runner dispatches to, so once that
    # binary has been read back it is the side the pins are scored against -- and the runner's
    # own answer stays in the record as `cli_plugin_buildx_version`, because deleting the fact
    # that F-44 measured would make a green A2.1 mean less, not more.
    seen_buildx = controlled.get("version") or cli_plugin_buildx
    scored_from = ("controlled_buildx" if controlled.get("version") else
                   "docker CLI plugin dispatch (no controlled executable was read)")
    running = builder.get("running_image") or {}
    running_image = {"repository": running.get("repository"), "tag": running.get("tag"),
                     "digest": running.get("digest")}
    legacy_buildx = (observed.get("buildx_version") or {}).get("version")
    pinned_buildx = configured.get("buildx_version")
    lock_vs_workflow = {
        "fields": {"buildx_version": _pair(lock.get("buildx_version"),
                                           workflow.get("buildx_version")),
                   "buildkit_repository": _pair(lock.get("buildkit_repository"),
                                                workflow.get("buildkit_repository")),
                   "buildkit_digest": _pair(lock.get("buildkit_digest"),
                                            workflow.get("buildkit_digest")),
                   # The action's `driver-opts` and the observation builder's `--driver-opt`
                   # are two declarations in one file. They are both held against the lock
                   # because a workflow that pins one BuildKit for the action and another for
                   # its own builder disagrees with itself, and that is a fact about the file.
                   "buildkit_digest_setup_action": _pair(
                       lock.get("buildkit_digest"),
                       _ref_parts(workflow.get("driver_opts_image"))["digest"])},
        "required": ("buildx_version", "buildkit_digest", "buildkit_digest_setup_action"),
    }
    lock_vs_workflow["status"] = _status_of(lock_vs_workflow["fields"],
                                           lock_vs_workflow["required"])
    integrity = (controlled.get("integrity") or {}).get("relation")
    invoked = (invocation.get("controlled") or {}).get("relation")
    workflow_vs_observed = {
        "fields": {
            "buildx_version": _pair(workflow.get("buildx_version"), seen_buildx),
            "builder_name": _pair(workflow.get("builder_name"), builder.get("builder")),
            "driver": _pair(workflow.get("driver"), builder.get("driver")),
            # The A2.1 claims about the producer itself. `buildx_version` alone can be
            # satisfied by any binary reporting the right string, so the executable is also
            # identified by the commit it prints, by a hash of the bytes that ran, and --
            # separately -- by which file the build was actually invoked with. Each answers a
            # different failure: a replaced release asset passes the version and fails the
            # hash; a job that quietly went back to `docker buildx` passes both of those and
            # fails the invocation. The path the binary prints for itself, when it prints one
            # at all, is in `controlled_buildx_path` and can contradict without being silent.
            "controlled_buildx_version": _pair(workflow.get("buildx_version"),
                                               controlled.get("version")),
            "controlled_buildx_commit": _pair(controlled_pin.get("expected_git_commit"),
                                              controlled.get("commit")),
            "controlled_buildx_path": _controlled_path_field(workflow, controlled),
            "controlled_buildx_integrity": {"declared": controlled_pin.get("expected_sha256"),
                                            "read_back": (controlled.get("integrity") or {}).get(
                                                "computed"),
                                            "relation": integrity},
            "build_invoked_controlled_executable": {
                "declared": (invocation.get("controlled") or {}).get("declared"),
                "read_back": (invocation.get("controlled") or {}).get("invoked"),
                "relation": invoked},
            # Kept, never scored here: what the runner's `docker buildx` would have dispatched
            # to. This is the field F-44 was filed over, and A2.1 proving the build did not go
            # through it is only meaningful while the disagreement stays on the record.
            "cli_plugin_buildx_version": {"declared": workflow.get("buildx_version"),
                                          "read_back": cli_plugin_buildx,
                                          "relation": (None if not cli_plugin_buildx else
                                                       ("equal" if cli_plugin_buildx ==
                                                        workflow.get("buildx_version")
                                                        else "different")),
                                          "scored": False},
        },
        "required": ("buildx_version", "builder_name", "driver", "controlled_buildx_version",
                     "controlled_buildx_commit", "controlled_buildx_integrity",
                     "build_invoked_controlled_executable"),
        # The printed path is not required -- see _controlled_path_field -- but it may not
        # contradict the declaration silently either.
        "blocking": ("controlled_buildx_path",),
        "scored_buildx_from": scored_from,
    }
    workflow_vs_observed["status"] = _status_of(workflow_vs_observed["fields"],
                                                workflow_vs_observed["required"],
                                                workflow_vs_observed["blocking"])
    relation = digest_relation({"digest": lock.get("buildkit_digest")}, running_image,
                              (pin or {}).get("children", []))
    child = (pin or {}).get("platform_child") or {}
    running_config = builder.get("config_digest")
    child_relation = config_digest_relation(child, running_config)
    lock_vs_observed = {
        "fields": {"buildx_version": _pair(lock.get("buildx_version"), seen_buildx),
                   "buildkit_digest": {"declared": lock.get("buildkit_digest"),
                                       "read_back": running_image.get("digest"),
                                       "relation": {"CONFORMING": "equal",
                                                    "MISMATCH": "different",
                                                    "UNKNOWN": None}[relation["status"]]},
                   # The config layer, scored on its own terms: both sides carry a `sha256:`
                   # prefix here so a prefix difference cannot invent a disagreement, and the
                   # answer stays a field of its own because a config digest is not a manifest
                   # digest -- two layers agreeing is a different claim from one layer.
                   "buildkit_child_config": _pair(child.get("config_digest"),
                                                  f"sha256:{running_config}" if running_config
                                                  and not str(running_config).startswith("sha256:")
                                                  else running_config),
                   "pinned_index_resolution": {"declared": lock.get("buildkit_image"),
                                               "read_back": (pin or {}).get("status"),
                                               "relation": None if not pin or not pin.get("ok")
                                               else "resolved"}},
        # Both digest layers are required from A2.1 on, because A2.2 is going to block on them:
        # the contract names "running BuildKit digest" and "running config relationship" as
        # separate fields. Requiring both is not the substitution Batch 3A refused -- each is
        # scored against its own pinned-side value, so a conforming config layer can never stand
        # in for a manifest layer that was not read, and the manifest layer can never answer for
        # content the config layer was asked about.
        "required": ("buildx_version", "buildkit_digest", "buildkit_child_config"),
        "digest_relation": relation,
        "config_digest_relation": child_relation,
        "layers": {"manifest": ("scored" if relation.get("relation")
                                else relation["status"].lower()),
                   "config": ("scored" if child_relation.get("relation")
                              else child_relation["status"].lower()),
                   "note": ("manifest layer: the pulled child of the pinned index; config "
                            "layer: the running image id against the pinned child's own config "
                            "digest. Neither is inferred from a tag, `buildx inspect` text or "
                            "`driver-opts`")},
    }
    lock_vs_observed["status"] = _status_of(lock_vs_observed["fields"],
                                            lock_vs_observed["required"])
    plugins = (observed.get("docker_cli_plugin") or {}).get("candidates") or {}
    # A fourth authority, compared rather than merged. `deployment/third-party-images.json` pins
    # the buildx the release *asks* for; `scripts/release/controlled_buildx.json` pins the bytes
    # A2.1 installs and runs. Two files naming one tool is a drift risk created by this batch, so
    # it is scored: bumping the lock without the pin (or the reverse) reads MISMATCH here instead
    # of becoming a record where the declared producer quietly means two things.
    controlled_pin_vs_lock = {
        "fields": {"buildx_version": _pair(lock.get("buildx_version"),
                                           controlled_pin.get("version")),
                   "buildx_source": _pair(lock.get("buildx_source"),
                                          controlled_pin.get("download_url"))},
        "required": ("buildx_version",),
        "note": ("both files declare a buildx version and a source for the same tool; agreeing "
                 "is a precondition for calling either one the pin. The source is recorded and "
                 "compared but not required, because the lock's entry is prose about the action "
                 "while the pin's is the URL A2.1 downloads from"),
    }
    controlled_pin_vs_lock["status"] = _status_of(controlled_pin_vs_lock["fields"],
                                                  controlled_pin_vs_lock["required"])
    return {
        # Kept for the evidence the release already publishes.
        "buildx_version": {"pinned": pinned_buildx, "observed": legacy_buildx,
                           "matches": (legacy_buildx == pinned_buildx
                                       if legacy_buildx and pinned_buildx else None)},
        "buildkit_image": {"pinned": configured.get("buildkit_image"),
                           "observed": builder.get("container_reference"),
                           "matches": _images_match(configured.get("buildkit_image"),
                                                    builder.get("container_reference"))},
        "lock_vs_workflow": lock_vs_workflow,
        "workflow_vs_observed": workflow_vs_observed,
        "lock_vs_observed": lock_vs_observed,
        "controlled_pin_vs_lock": controlled_pin_vs_lock,
        "buildx_binaries": {
            "built_with": (invocation.get("controlled") or {}).get("invoked"),
            "controlled": controlled.get("path"),
            "controlled_status": controlled.get("status"),
            "executing": executing.get("resolved_path"),
            "identified_by": executing.get("identified_by"),
            "executing_version": seen_buildx,
            "cli_plugin_version": cli_plugin_buildx,
            "scored_buildx_from": scored_from,
            "reads_via": observed.get("buildx_reads_via"),
            "installed_on_disk": {path: (entry.get("version"))
                                  for path, entry in plugins.items() if entry.get("exists")},
            "note": ("`built_with` is the executable the build was actually invoked with, read "
                     "back from the job's own record of its argv; `executing` is what "
                     "`docker buildx` would have dispatched to. Both stay in the record because "
                     "the difference between them is what A2.1 exists to remove."),
        },
    }


def _images_match(pinned: str | None, seen: str | None) -> bool | None:
    """Do the pinned BuildKit image and the one that ran name the same bytes?

    By digest when both carry one, because a tag on either side is a pointer the vendor
    can move; by whole reference otherwise. Neither side being readable is `None`, not
    `False` -- the evidence must not turn a missing observation into an accusation. This
    is the legacy single-boolean form; `digest_relation` is the layered replacement and
    the two are kept side by side so an old reader is not silently reinterpreted.
    """
    if not pinned or not seen:
        return None
    pinned_digest = pinned.rsplit("@", 1)[-1] if "@" in pinned else None
    seen_digest = seen.rsplit("@", 1)[-1] if "@" in seen else None
    if pinned_digest and seen_digest:
        return pinned_digest == seen_digest
    return pinned == seen


def producer_alignment(comparison: dict) -> dict:
    """The one-sentence answer, built from the named comparisons rather than replacing them.

    Every component listed here is a separate answer with its own fields; the verdict only
    summarises them. Adding the controlled-executable comparison did not merge any of the three
    M3 asked for, and dropping a name from this tuple is what would collapse them.
    """
    names = ["lock_vs_workflow", "workflow_vs_observed", "lock_vs_observed"]
    if (comparison.get("controlled_pin_vs_lock") or {}).get("status"):
        # Only a record that was given a controlled-executable pin can answer for it. In the
        # release path, which has none until A2.2, leaving it out keeps the verdict about the
        # authorities that actually exist rather than manufacturing an UNKNOWN.
        names.append("controlled_pin_vs_lock")
    statuses = {name: (comparison.get(name) or {}).get("status") for name in names}
    if MISMATCH in statuses.values():
        verdict = MISMATCH
    elif UNKNOWN in statuses.values():
        verdict = UNKNOWN
    else:
        verdict = CONFORMING
    return {"verdict": verdict, "components": statuses,
            "meaning": ("the declared pins, the workflow's own pins and the runner agree"
                        if verdict == CONFORMING else
                        "at least one layer disagrees -- see the named comparison that says so"
                        if verdict == MISMATCH else
                        "at least one layer could not be read, which is not a pass")}


def oci_layout_facts(tar_path: str | None) -> dict:
    """The F-39 measurement fields obtainable from an OCI archive, without a registry.

    Stage 6 of Batch 3A wants a two-build comparison that can attribute a difference:
    config digest, layer digests, `diff_ids`, the platform manifest, what the index names
    and which descriptors are attestations. All of that is *inside* an `--output
    type=oci` tarball, and reading it from the tar rather than from the daemon keeps the
    measurement independent of whichever container runtime happened to be installed.
    """
    if not tar_path or not Path(tar_path).exists():
        return {"ok": False, "status": "NOT_PROVIDED",
                "error": f"no OCI archive at {tar_path!r}"}
    wanted = "index.json"
    blobs: dict[str, bytes] = {}
    index = None
    with tarfile.open(tar_path) as archive:
        for member in archive.getmembers():
            name = member.name.removeprefix("./")
            if not member.isfile():
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            data = handle.read()
            if name == wanted:
                index = json.loads(data)
            elif name.startswith("blobs/"):
                blobs[name.split("/")[-1]] = data
    if index is None:
        return {"ok": False, "status": "ERROR", "error": "the archive carried no index.json"}
    manifests = index.get("manifests", [])
    output = {"ok": True, "status": "READ", "index_media_type": index.get("mediaType"),
              "index_composition": [{"digest": m.get("digest"),
                                     "media_type": m.get("mediaType"),
                                     "platform": (m.get("platform") or {}).get("architecture")
                                                 and f"{(m['platform'] or {}).get('os')}/"
                                                     f"{(m['platform'] or {}).get('architecture')}",
                                     "attestation": (m.get("annotations") or {}).get(
                                         "vnd.docker.reference.type") == "attestation-manifest"}
                                    for m in manifests],
              "attestation_descriptors": [m.get("digest") for m in manifests if
                                          (m.get("annotations") or {}).get(
                                              "vnd.docker.reference.type",
                                              "") == "attestation-manifest"],
              "blobs_present": len(blobs)}
    for entry in manifests:
        annotations = entry.get("annotations") or {}
        if annotations.get("vnd.docker.reference.type") == "attestation-manifest":
            continue
        digest = (entry.get("digest") or "").split(":")[-1]
        manifest = blobs.get(digest)
        if not manifest:
            continue
        doc = json.loads(manifest)
        config_digest = (doc.get("config") or {}).get("digest", "").split(":")[-1]
        config = blobs.get(config_digest)
        config_doc = json.loads(config) if config else {}
        output["platform_manifest"] = {"digest": entry.get("digest"),
                                      "media_type": entry.get("mediaType"),
                                      "config_digest": (doc.get("config") or {}).get("digest"),
                                      "layer_digests": [layer.get("digest") for layer
                                                        in doc.get("layers", [])]}
        output["image_config"] = {"created": config_doc.get("created"),
                                 "diff_ids": config_doc.get("rootfs", {}).get("diff_ids", []),
                                 "architecture": config_doc.get("architecture"),
                                 "os": config_doc.get("os"),
                                 "history_entries": len(config_doc.get("history", []))}
        output["frontend"] = None  # filled from the Dockerfile directive by the caller
        break
    return output


#: What a record must carry to be an observation at all. A value in here being *disagreed
#: with* is never a gap -- that is the measurement. A gap is a field the instrument promised
#: and could not fill.
CONTRACT_REQUIRED = (
    "observed.executing_buildx.resolved_path", "observed.executing_buildx.version",
    "observed.docker_cli_plugin.candidates", "observed.docker_cli_plugin.resolves_to",
    "observed.builder.builder", "observed.builder.driver", "observed.builder.nodes",
    "observed.engine.version", "configured.lock.buildx_version",
    "configured.lock.buildkit_digest", "configured.workflow.buildx_version",
    "comparison.lock_vs_workflow.status", "comparison.workflow_vs_observed.status",
    "comparison.lock_vs_observed.digest_relation.status",
    # A2.1: the producer is a file on the machine, so the record has to name that file, what
    # it says it is, and the bytes it is made of -- and separately show that the *build* was
    # invoked with it. A controlled executable that was installed but never run is not a
    # deterministic producer, which is why `observed.build_invocation.argv` is in the contract
    # and not merely a convenience.
    "observed.controlled_buildx.version", "observed.controlled_buildx.commit",
    "observed.controlled_buildx.path_status",
    "observed.controlled_buildx.integrity.expected",
    "observed.controlled_buildx.integrity.computed",
    "observed.build_invocation.argv", "configured.controlled.version",
    "configured.controlled.expected_git_commit", "configured.controlled.expected_sha256",
    "configured.workflow.controlled_buildx_path",
)

#: Contract keys only the observation path promises. The release build path declares none of
#: this yet -- A2.2 switches it -- so requiring it there would fail every existing record for
#: a change that has not been made.
OBSERVE_ONLY_PREFIXES = ("configured.workflow", "configured.controlled", "observed.builder",
                         "observed.controlled_buildx", "observed.build_invocation",
                         "configured.lock.buildx_source")


def _dig(payload: dict, dotted: str):
    node = payload
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def contract_gaps(payload: dict, observe_mode: bool) -> list[str]:
    """Fields the *observation contract* promises and this record does not carry.

    Deliberately blind to whether the values agree: a disagreement is a measurement and
    must be recorded, not failed on (Batch 3A does not block anything on it). A missing
    or unreadable field, though, is a broken instrument, and the observation job's whole
    value is that the instrument is trustworthy.
    """
    gaps = []
    promised = (CONTRACT_REQUIRED if observe_mode else
                tuple(k for k in CONTRACT_REQUIRED if not k.startswith(OBSERVE_ONLY_PREFIXES)))
    for dotted in promised:
        value = _dig(payload, dotted)
        if value in (None, "", [], {}):
            gaps.append(dotted)
    builder = payload.get("observed", {}).get("builder", {})
    if observe_mode and builder.get("status") == "ERROR":
        gaps.append("observed.builder.error")
    # A layer the daemon or the registry refused to answer is a broken instrument; a layer
    # that answered "there is nothing here" is a measurement. Only the first is a gap, or the
    # docker driver's stated absence would fail the job it correctly describes.
    if observe_mode and builder.get("ok") and (builder.get("repo_digests_read") or {}) \
            .get("status") == "ERROR":
        gaps.append("observed.builder.repo_digests_read")
    child = (payload.get("pinned_index_resolution") or {}).get("platform_child") or {}
    if observe_mode and child.get("status") == "ERROR":
        gaps.append("pinned_index_resolution.platform_child")
    # The same rule one layer up. A controlled executable that hashes differently from the pin
    # is a finding the evidence must carry; one that could not be read or hashed at all, or a
    # build whose argv was never recorded, means the job is not measuring what it claims, and
    # `NOT_PROVIDED` is included there because after A2.1 the observation job always has a pin
    # to pass -- an absent one is a step that stopped running, not a mode of working.
    controlled = (payload.get("observed") or {}).get("controlled_buildx") or {}
    if observe_mode and controlled.get("status") in ("ERROR", "NOT_PROVIDED"):
        gaps.append("observed.controlled_buildx.status")
    if observe_mode and (controlled.get("integrity") or {}).get("status") in ("ERROR",
                                                                             "NOT_PROVIDED"):
        gaps.append("observed.controlled_buildx.integrity.status")
    if observe_mode and (payload.get("observed") or {}).get("build_invocation", {}) \
            .get("status") in ("ERROR", "NOT_PROVIDED"):
        gaps.append("observed.build_invocation.status")
    return gaps


def set_acceptance(records: list[dict], expected_images: list[str]) -> dict:
    """Score a whole set of per-image observation records -- A2.1's acceptance rule.

    Three rules shape this function, and each one is here because a set verdict can be made to
    look good without it:

    * **Every image separately.** A set is CONFORMING only when each named image's own record
      says so. Two conforming images cannot average out a third that disagreed, and an image
      with no record is a failure of the set rather than an absent row in a table.
    * **Same round.** The five images of one round have to come from one commit and one run, or
      "all five conforming" would be true of a set assembled from whichever runs happened to
      look good -- which is the cherry-picking A2.2's evidence rules forbid, applied here so
      the habit does not start at the stage where it is easier to avoid.
    * **A build that failed is not a conforming image,** even when the producer read perfectly.
      "Can the pinned producer build all five CAP images?" is half the question A2.1 exists to
      answer, so the build's own exit status is part of the row, not a separate report.
    """
    comparisons = ("lock_vs_workflow", "workflow_vs_observed", "lock_vs_observed",
                   "controlled_pin_vs_lock")
    by_image: dict[str, list[dict]] = {}
    for record in records:
        by_image.setdefault(str(record.get("image") or "<unnamed>"), []).append(record)
    rows: dict[str, dict] = {}
    problems: list[str] = []
    for image in expected_images:
        found = by_image.get(image) or []
        if not found:
            problems.append(f"no observation record was produced for {image}")
            rows[image] = {"status": "MISSING", "problems": ["no record"]}
            continue
        if len(found) > 1:
            problems.append(f"{image} has {len(found)} records; a set row needs exactly one")
        record = found[0]
        comparison = record.get("comparison") or {}
        statuses = {name: (comparison.get(name) or {}).get("status") for name in comparisons}
        alignment = (record.get("producer_alignment") or {}).get("verdict")
        invocation = (record.get("observed") or {}).get("build_invocation") or {}
        build_exit = invocation.get("build_exit")
        gaps = record.get("contract_gaps") or []
        row = {"comparisons": statuses, "producer_alignment": alignment,
               "build_exit": build_exit, "contract_gaps": gaps,
               "built_with": (comparison.get("buildx_binaries") or {}).get("built_with"),
               "bases": (record.get("base_images") or {}).get("bases") or [],
               "running_buildkit_digest": ((record.get("observed") or {}).get("builder") or {}
                                           ).get("running_image", {}).get("digest")}
        row_problems = []
        if MISMATCH in list(statuses.values()) + [alignment]:
            row_problems.append("a required comparison disagrees")
        if UNKNOWN in list(statuses.values()) + [alignment]:
            row_problems.append("a required layer could not be read")
        if build_exit != 0:
            row_problems.append(f"the build did not succeed (exit {build_exit!r})")
        unbound = [base for base in row["bases"] if base.get("status") != "READ"]
        if unbound:
            # A pinned producer building on a base nobody can name has produced something this
            # record cannot re-derive, which is the whole point of pinning it. So an unbound
            # base is a set problem even with every comparison green, and a base list that
            # came back empty is treated the same way -- no bases read is not bases conforming.
            row_problems.append("a base image is not bound by digest: "
                                + "; ".join(str(base.get("ref") or base.get("status"))
                                           for base in unbound))
        elif not row["bases"]:
            row_problems.append("no base image was resolved from the Dockerfile and argv")
        if gaps:
            row_problems.append("the record itself is incomplete: " + ", ".join(gaps))
        row["problems"] = row_problems
        row["status"] = CONFORMING if not row_problems else (
            MISMATCH if "a required comparison disagrees" in row_problems else UNKNOWN)
        problems.extend(f"{image}: {problem}" for problem in row_problems)
        rows[image] = row
    for image in sorted(set(by_image) - set(expected_images)):
        problems.append(f"{image} was recorded but is not an expected image of the set")
    rounds = {(str((record.get("runner") or {}).get("github_sha") or "<unknown sha>"),
               str((record.get("runner") or {}).get("github_run_id") or "<unknown run>"))
              for record in records}
    if len(rounds) > 1:
        problems.append(f"the records came from {len(rounds)} different runs/commits: "
                        + "; ".join(f"{sha}#{run}" for sha, run in sorted(rounds)))
    verdict = CONFORMING if not problems else (
        MISMATCH if any("disagrees" in problem for problem in problems) else UNKNOWN)
    return {"expected_images": list(expected_images), "images": rows,
            "rounds": [{"source_revision": sha, "run_id": run} for sha, run in sorted(rounds)],
            "verdict": verdict, "problems": problems,
            "authorizes_a2_2": verdict == CONFORMING,
            "meaning": ("every image of the set was built by the pinned producer, from one "
                        "round, with every required layer read and agreeing"
                        if verdict == CONFORMING else
                        "at least one image is not conforming evidence, which is not a pass")}


def combine_records(args, run: CommandRunner | None = None) -> int:
    """The A2.1 acceptance step: read the per-image records, score the set, write the verdict.

    It reads files rather than machines, so the same scoring runs locally against artifacts
    downloaded from CI -- and it exits non-zero only on an *instrument* problem (a missing or
    incomplete record). A measured MISMATCH is written out and reported, because failing the
    step there would throw away the evidence the next decision is made from.
    """
    records = []
    unreadable = []
    for path in args.combine:
        try:
            records.append(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            unreadable.append(f"{path}: {type(error).__name__}")
    expected = list(args.expected_images or [])
    if not expected:
        expected = [str(record.get("image")) for record in records if record.get("image")]
    payload = set_acceptance(records, expected)
    payload["record_files"] = list(args.combine)
    if unreadable:
        payload["problems"] = payload["problems"] + [f"record file unreadable: {item}"
                                                     for item in unreadable]
        payload["status"] = "ERROR"
    if not expected:
        payload["problems"] = payload["problems"] + ["no images were declared or recorded"]
        payload["status"] = "ERROR"
    payload["verdict"] = CONFORMING if not payload["problems"] else payload["verdict"]
    payload["a2_1_acceptance"] = {
        "verdict": "BATCH 3 A2.1 PRODUCER CONFORMING" if not payload["problems"]
        and payload["verdict"] == CONFORMING else "BATCH 3 A2.1 PRODUCER NOT CONFORMING",
        "requires": ("every expected image: four named comparisons CONFORMING, "
                     "producer_alignment CONFORMING, build exit 0, no contract gaps, "
                     "one round for all records")}
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(payload, sort_keys=True))
    if args.self_check and (payload.get("status") == "ERROR" or unreadable
                            or any(problem.startswith("no observation record")
                                   for problem in payload["problems"])):
        print("the producer set could not be scored from the records given", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None, run: CommandRunner | None = None,
         environ=None) -> int:
    parser = argparse.ArgumentParser(description="record the producer of a build")
    parser.add_argument("--out", required=True)
    parser.add_argument("--buildkit-entry", default="buildkit-buildkit")
    parser.add_argument("--buildx-entry", default="buildx")
    parser.add_argument("--docker-cli-build", action="store_true",
                        help="the build ran through `docker build`, whose BuildKit is the daemon")
    parser.add_argument("--lock", default="deployment/third-party-images.json")
    parser.add_argument("--mode", choices=("build", "observe"), default="build",
                        help="observe is the non-publishing CI observation; it changes no build")
    parser.add_argument("--builder", default=None,
                        help="the explicitly named builder to inspect in observe mode")
    parser.add_argument("--workflow", default=None,
                        help="workflow file to read the declared pins from")
    parser.add_argument("--job", default=None, help="job name inside --workflow")
    parser.add_argument("--oci-tar", default=None,
                        help="an --output type=oci archive from the build, for the F-39 fields")
    parser.add_argument("--dockerfile", default=None,
                        help="the Dockerfile built, read only for its `# syntax=` directive")
    parser.add_argument("--controlled-pin", default=None,
                        help="the JSON pinning the buildx executable this build must run under")
    parser.add_argument("--image", default=None, help="which image this record is about")
    parser.add_argument("--build-command", default=None,
                        help="a JSON file the build step wrote, carrying the argv it used")
    parser.add_argument("--build-arg", action="append", default=[], dest="build_args",
                        help="KEY=VALUE passed to the build, used to resolve the base it named")
    parser.add_argument("--build-context", action="append", default=[], dest="named_contexts",
                        help="name=value named context, used to record a same-round base binding")
    parser.add_argument("--combine", nargs="*", default=None, metavar="RECORD.json",
                        help="score a set of per-image observation records instead of reading "
                             "a machine -- the A2.1 acceptance step")
    parser.add_argument("--expected-image", action="append", default=[], dest="expected_images",
                        help="with --combine: an image the set must contain a record for")
    parser.add_argument("--self-check", action="store_true",
                        help="exit non-zero when the observation contract is not satisfied")
    args = parser.parse_args(argv)
    runner = run or run_command
    env = environ if environ is not None else os.environ

    if args.combine is not None:
        if not args.combine:
            print("--combine was given no record files to score", file=sys.stderr)
            return 2
        return combine_records(args, run=runner)

    with open(args.lock, encoding="utf-8") as handle:
        lock = json.load(handle)
    configured = configured_values(lock, args.buildkit_entry, args.buildx_entry)
    configured["workflow"] = configured_workflow_values(args.workflow, args.job, args.builder)
    configured["controlled"] = load_controlled_pin(args.controlled_pin)
    # The path the job declared in its own `env:` wins over the pin file's, because
    # `configured.workflow` is the authority being compared; the pin is what that path has to
    # hash and version-check against.
    declared_path = (configured["workflow"].get("controlled_buildx_path")
                     if configured["workflow"].get("ok") else None)
    observed = observe(runner, docker_cli_build=args.docker_cli_build,
                       builder_name=args.builder if args.mode == "observe" else None,
                       environ=env, controlled_pin=configured["controlled"],
                       build_command_file=args.build_command,
                       controlled_path=declared_path)
    pin = (resolve_pinned_ref(runner, configured["lock"]["buildkit_image"],
                              (observed.get("builder") or {}).get("image_platform"),
                              buildx_prefix(observed.get("controlled_buildx")))
           if args.mode == "observe" else {"ok": False, "status": "NOT_APPLICABLE"})
    comparison = compare(configured, observed, pin)
    f39 = oci_layout_facts(args.oci_tar)
    # The build's own argv is the authority on what it was given. A flag repeated on the
    # recorder's command line is a claim; the same flag in the recorded invocation is a fact,
    # and the same-round base digest only means something as the latter.
    recorded_args, recorded_contexts = build_flags_from_argv(
        (observed.get("build_invocation") or {}).get("argv") or [])
    bases = base_image_bindings(args.dockerfile,
                                _merge_flags(args.build_args, recorded_args),
                                _merge_flags(args.named_contexts, recorded_contexts))
    if f39.get("ok") and args.dockerfile and Path(args.dockerfile).exists():
        for line in Path(args.dockerfile).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("# syntax=") or line.strip().startswith("#syntax="):
                f39["frontend"] = line.strip()
                f39["frontend_status"] = "PINNED_BY_DIRECTIVE"
                break
        if "frontend_status" not in f39:
            # A null `frontend` would otherwise be read as "not recorded". It means something
            # specific: this Dockerfile names no syntax directive, so the frontend that parsed
            # it is the one built into the daemon the builder is running -- which for a pinned
            # docker-container builder is the pinned BuildKit, and is therefore knowable.
            f39["frontend_status"] = "BUILTIN_OF_THE_RUNNING_BUILDKIT"
            f39["frontend_note"] = (
                "no `# syntax=` directive, so the daemon's built-in dockerfile frontend "
                "parsed this image; the daemon is the container read in observed.builder")
    payload = {
        "mode": args.mode,
        "image": args.image,
        "dockerfile": args.dockerfile,
        "base_images": bases,
        "configured": configured,
        "observed": observed,
        "pinned_index_resolution": pin,
        "comparison": comparison,
        "producer_alignment": producer_alignment(comparison),
        "f39_measurement": {**f39, "builds_compared": 1,
                            "measurement_status": "PARTIAL -- one build measured; attributing a "
                                                  "digest difference needs two independent builds "
                                                  "of one commit"},
        "runner": runner_identity(env),
    }
    # Only the three primary reads can be "incomplete": the aggregates beside them
    # (`executing_buildx`, `docker_cli_plugin`, `engine`) are views of the same commands,
    # and reporting a view as a lost read would double-count one failure. A *stated* absence
    # -- the docker driver, or a `docker build` whose BuildKit is the daemon's -- answers with
    # `ok: true` and a reason, so `ok` alone separates "there is nothing here" from "this
    # read was lost", which is the distinction F-44's evidence needed a field for.
    missing = [key for key in ("buildx_version", "builder", "docker_engine")
               if not payload["observed"][key].get("ok")]
    if missing:
        payload["incomplete"] = missing
    gaps = contract_gaps(payload, observe_mode=args.mode == "observe")
    if gaps:
        payload["contract_gaps"] = gaps
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    # An unreadable producer is reported on stdout and still exits 0: the build already
    # happened, and failing here would hide the build evidence behind a recorder's own
    # complaint. `incomplete` is the field the release gate reads, and a *mismatch* is
    # evidence rather than a failure -- only --self-check turns a broken instrument into a
    # non-zero exit, and only in the observation job that promises to be one.
    print(json.dumps(payload, sort_keys=True))
    if args.self_check and gaps:
        print("producer observation contract not satisfied: " + ", ".join(gaps), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
