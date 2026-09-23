"""Record the *producer* of a build, separating what was declared from what was read back.

B2 of the post-rc hardening plan pinned the builder; Batch 3A adds the half that was
missing. Pinning is only a claim until the evidence says **which binary executed the build,
which builder it selected, and what image that builder's container is actually running** --
and it says so from the tool, not from the YAML that expressed the wish. Three sources are
therefore kept apart on purpose:

    configured.lock       what deployment/third-party-images.json declares
    configured.workflow   what the workflow file declares (its own pins, its own builder name)
    observed.*            independent read-back of the runner, the CLI, the builder and its
                          container -- plus a registry read of the pinned reference

and they are compared three ways (``lock_vs_workflow``, ``workflow_vs_observed``,
``lock_vs_observed``) rather than collapsed into one boolean: a release that agrees with
itself while disagreeing with the runner is exactly the F-44 situation this file was written
to make visible.

Nothing is invented. A read that fails is reported with its status and the complaint that
explains it; an unreadable value never becomes ``None``-and-therefore-matches, because a
blank field a reader cannot distinguish from an unrecorded one is how F-33's gate read a
verdict nobody wrote.

``--mode observe`` is the non-publishing observation path used by CI's producer-observation
job. The default mode is the one ``build_release_image.sh`` calls, and its keys are
additive: switching the release build onto the pinned builder is a later decision, so
nothing here changes what the release build command is or whether a mismatch blocks
publication -- it only makes the difference readable, and fails on a *broken observation*
rather than on an unwelcome measurement.
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


def observe_builder(run: CommandRunner, name: str | None = None) -> dict:
    """The builder that actually ran: driver, nodes, and the BuildKit identity behind them.

    `--builder <name>` is passed when the caller named one explicitly, because `docker
    buildx inspect` without it answers for the *current* default builder -- which, on a
    runner where `setup-buildx-action` ran, is a builder nobody chose by name. Recording
    the named one is the difference between "a builder exists with these settings" and
    "the build went there".
    """
    argv = ["docker", "buildx", "inspect"] + ([f"--builder={name}"] if name else [])
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
    # quietly makes two claims depend on the same parse.
    digests = read(run, "container_repo_digests", ["docker", "inspect", "--format",
                                                   "{{json .RepoDigests}}", container])
    repo_digests = []
    if digests["ok"]:
        try:
            repo_digests = json.loads(digests["value"]) or []
        except json.JSONDecodeError:
            observed["repo_digest_parse_error"] = digests["value"][:ERROR_TEXT_LIMIT]
    else:
        observed["repo_digests_status"] = {"status": digests["status"],
                                           "error": digests.get("error")}
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
            "reason": ("the container reports no RepoDigests, so only its config digest is "
                       "known -- a config digest is not a manifest digest and is never "
                       "compared against the pin")}
    return {**observed, "container": container,
            "container_config_image": reference or None,
            "image_id": image_id or None,
            "container_image_id": image_id or None,
            "repo_digests": repo_digests,
            "container_reference": (repo_digests[0] if repo_digests else reference) or None,
            "running_image": {**running, "image_id": image_id or None,
                              "config_digest": config_digest},
            "config_digest": config_digest,
            "digest": config_digest}


def resolve_pinned_ref(run: CommandRunner, ref: str | None) -> dict:
    """Ask the *registry* what the pinned BuildKit reference contains -- a separate layer.

    The pinned digest is a manifest-list, so the only way to check a running child
    against it without trusting a tag is to read the index itself. `imagetools` goes to
    the registry rather than the daemon, which keeps this from becoming "the daemon says
    it pulled what I asked for". Attestation entries (`unknown/unknown`) are kept but
    labelled, because they are descriptors with nothing to do with the runnable images.
    """
    if not ref:
        return {"ok": False, "status": "NOT_APPLICABLE",
                "error": "no pinned BuildKit reference was declared"}
    code, out, err = run(["docker", "buildx", "imagetools", "inspect", ref, "--raw"])
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
            "children": children, "attestation_descriptors": attestations}


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
            environ=None) -> dict:
    """Every producer field the tooling can be asked about, read back."""
    env = environ if environ is not None else os.environ
    buildx = read(run, "buildx_version", ["docker", "buildx", "version"])
    if buildx["ok"]:
        buildx.update(parse_version_line(buildx["value"]))
    engine = read(run, "docker_server", ["docker", "version", "--format", "{{.Server.Version}}"])
    builder = observe_builder(run, builder_name)
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
            "builders": read_all(run, "buildx_ls", ["docker", "buildx", "ls"]),
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
                "declared_env_keys": sorted(declared_env),
                "source": path, "job": job_name}
    if expressions:
        declared["expressions"] = expressions
    if buildkit:
        parts = _ref_parts(buildkit)
        declared.update({f"buildkit_{key}": value
                         for key, value in parts.items() if key != "ref"})
    return {"ok": True, "status": "READ", **declared}


def _pair(left, right) -> dict:
    return {"declared": left, "read_back": right,
            "relation": (None if left is None or right is None
                         else ("equal" if left == right else "different"))}


def _status_of(fields: dict, required: tuple[str, ...]) -> str:
    """CONFORMING only when every required field is present and equal.

    UNKNOWN wins over MISMATCH nowhere: a missing side is reported as UNKNOWN so that an
    unreadable observation can never be read as agreement, and a field that is present and
    unequal is reported even when another field is unreadable -- hiding a known
    disagreement behind an unknown one would be the same mistake in the other direction.
    """
    known = [name for name in required if fields.get(name, {}).get("relation") is not None]
    if not known:
        return UNKNOWN
    if any(fields[name]["relation"] == "different" for name in known):
        return MISMATCH
    return CONFORMING if len(known) == len(required) else UNKNOWN


def compare(configured: dict, observed: dict, pin: dict | None = None) -> dict:
    """The three comparisons M3 asks for, each with its own answer.

    One boolean would let a release agree with its own YAML while disagreeing with the
    machine that built it -- which is precisely what F-44 was. `configured.lock` and
    `configured.workflow` are two authorities that must agree with each other before
    either can be believed against the runner.
    """
    lock = configured.get("lock") or {}
    workflow = configured.get("workflow") or {}
    executing = observed.get("executing_buildx") or {}
    builder = observed.get("builder") or {}
    seen_buildx = executing.get("version") or (observed.get("buildx_version") or {}).get("version")
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
    workflow_vs_observed = {
        "fields": {"buildx_version": _pair(workflow.get("buildx_version"), seen_buildx),
                   "builder_name": _pair(workflow.get("builder_name"), builder.get("builder")),
                   "driver": _pair(workflow.get("driver"), builder.get("driver"))},
        "required": ("buildx_version", "builder_name", "driver"),
    }
    workflow_vs_observed["status"] = _status_of(workflow_vs_observed["fields"],
                                                workflow_vs_observed["required"])
    relation = digest_relation({"digest": lock.get("buildkit_digest")}, running_image,
                              (pin or {}).get("children", []))
    lock_vs_observed = {
        "fields": {"buildx_version": _pair(lock.get("buildx_version"), seen_buildx),
                   "buildkit_digest": {"declared": lock.get("buildkit_digest"),
                                       "read_back": running_image.get("digest"),
                                       "relation": {"CONFORMING": "equal",
                                                    "MISMATCH": "different",
                                                    "UNKNOWN": None}[relation["status"]]},
                   "pinned_index_resolution": {"declared": lock.get("buildkit_image"),
                                               "read_back": (pin or {}).get("status"),
                                               "relation": None if not pin or not pin.get("ok")
                                               else "resolved"}},
        "required": ("buildx_version", "buildkit_digest"),
        "digest_relation": relation,
    }
    lock_vs_observed["status"] = _status_of(lock_vs_observed["fields"],
                                            lock_vs_observed["required"])
    plugins = (observed.get("docker_cli_plugin") or {}).get("candidates") or {}
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
        "buildx_binaries": {
            "executing": executing.get("resolved_path"),
            "identified_by": executing.get("identified_by"),
            "executing_version": seen_buildx,
            "installed_on_disk": {path: (entry.get("version"))
                                  for path, entry in plugins.items() if entry.get("exists")},
            "note": ("both are recorded whenever they differ; the executing one is what built "
                     "the bytes, and the installed one is only what the action placed"),
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
    """The one-sentence answer, built from the three answers rather than replacing them."""
    statuses = {name: (comparison.get(name) or {}).get("status")
                for name in ("lock_vs_workflow", "workflow_vs_observed", "lock_vs_observed")}
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


CONTRACT_REQUIRED = (
    "observed.executing_buildx.resolved_path", "observed.executing_buildx.version",
    "observed.docker_cli_plugin.candidates", "observed.docker_cli_plugin.resolves_to",
    "observed.builder.builder", "observed.builder.driver", "observed.builder.nodes",
    "observed.engine.version", "configured.lock.buildx_version",
    "configured.lock.buildkit_digest", "configured.workflow.buildx_version",
    "comparison.lock_vs_workflow.status", "comparison.workflow_vs_observed.status",
    "comparison.lock_vs_observed.digest_relation.status",
)


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
    for dotted in (CONTRACT_REQUIRED if observe_mode else
                   tuple(k for k in CONTRACT_REQUIRED
                         if not k.startswith(("configured.workflow", "observed.builder")))):
        value = _dig(payload, dotted)
        if value in (None, "", [], {}):
            gaps.append(dotted)
    if (observe_mode
            and payload.get("observed", {}).get("builder", {}).get("status") == "ERROR"):
        gaps.append("observed.builder.error")
    return gaps


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
    parser.add_argument("--self-check", action="store_true",
                        help="exit non-zero when the observation contract is not satisfied")
    args = parser.parse_args(argv)
    runner = run or run_command
    env = environ if environ is not None else os.environ

    with open(args.lock, encoding="utf-8") as handle:
        lock = json.load(handle)
    configured = configured_values(lock, args.buildkit_entry, args.buildx_entry)
    configured["workflow"] = configured_workflow_values(args.workflow, args.job, args.builder)
    observed = observe(runner, docker_cli_build=args.docker_cli_build,
                       builder_name=args.builder if args.mode == "observe" else None,
                       environ=env)
    pin = (resolve_pinned_ref(runner, configured["lock"]["buildkit_image"])
           if args.mode == "observe" else {"ok": False, "status": "NOT_APPLICABLE"})
    comparison = compare(configured, observed, pin)
    f39 = oci_layout_facts(args.oci_tar)
    if f39.get("ok") and args.dockerfile and Path(args.dockerfile).exists():
        for line in Path(args.dockerfile).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("# syntax=") or line.strip().startswith("#syntax="):
                f39["frontend"] = line.strip()
                break
    payload = {
        "mode": args.mode,
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
