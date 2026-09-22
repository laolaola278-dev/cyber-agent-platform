"""Record the *producer* of a release build, separating what was configured from what was read.

B2 of the post-rc hardening plan. Pinning a builder is only half of a claim: the evidence a
release leaves behind has to say which builder actually ran, or the pin is a wish expressed in
a YAML file. So every field here is read back out of the tool that owns it, and the declared
values are kept in a *separate* object -- two agreeing copies of one string the script itself
wrote would prove nothing, which is the failure mode this file exists to avoid.

Nothing is invented. A read that fails is reported as failed (`{"ok": false, "error": ...}`)
rather than filled in from a default, because a blank field a reader cannot distinguish from an
unrecorded one is how F-33's gate ended up reading a verdict nobody wrote.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from collections.abc import Callable

#: One command read: exit code, stdout and stderr, with the argv that answered them.
CommandRunner = Callable[[list[str]], tuple[int, str, str]]

#: How much of a complaint to carry into the evidence. Enough to identify a usage
#: error, short enough that a stack trace cannot bury the record it explains.
ERROR_TEXT_LIMIT = 300


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
        return {"ok": False, "command": command, "exit": code,
                "error": complaint or (out[:ERROR_TEXT_LIMIT] or f"exit {code}")}
    return {"ok": True, "command": command, "exit": code, "value": first}


def _inspect_sections(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """The builder block and the first node block of `docker buildx inspect`.

    The command's layout carries the facts: a builder header (`Name:`, `Driver:`), then a
    `Nodes:` section with per-node keys (`Endpoint:`, `Status:`, `Buildkit:`, `Image:`).
    Labels are parsed rather than positions, so a buildx release that adds or reorders a
    line cannot silently shift a value into the wrong field.
    """
    head: dict[str, str] = {}
    node: dict[str, str] = {}
    in_nodes = False
    for line in text.splitlines():
        if line.strip() == "Nodes:":
            in_nodes = True
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            continue
        target = node if in_nodes else head
        target.setdefault(key.strip().lower(), value.strip())
        if in_nodes and target.get("image"):
            break  # the first node is the one this builder runs
    return head, node


def buildx_builder(run: CommandRunner) -> dict:
    """The builder that actually ran: driver, name, and the BuildKit identity behind them.

    The image reference and the image ID are different facts -- the first says what the
    builder was asked to run, the second says what the daemon resolved -- and the digest
    the release cares about is only in the second one when the reference was pulled by
    digest. `--format` is not available here: the buildx CLI rejects that flag on both
    `version` and `inspect`, and the first release evidence proved it by failing with
    nothing but an exit code.
    """
    argv = ["docker", "buildx", "inspect"]
    command = " ".join(shlex.quote(part) for part in argv)
    code, out, err = run(argv)
    if code != 0 or not out.strip():
        complaint = (err.splitlines() or [""])[0].strip()[:ERROR_TEXT_LIMIT]
        return {"ok": False, "command": command, "exit": code,
                "error": complaint or f"exit {code}",
                "reason": "no builder could be inspected"}
    head, node = _inspect_sections(out)
    driver, builder = head.get("driver", ""), head.get("name", "")
    observed: dict = {"ok": True, "command": command, "exit": code,
                      "driver": driver or None, "builder": builder or None,
                      "buildkit_version": node.get("buildkit") or None,
                      "node_image": node.get("image") or None}
    container = f"buildx_buildkit_{builder}" if builder else ""
    if not container:
        return {**observed, "ok": False, "error": "buildx named no builder"}
    image = read(run, "image", ["docker", "inspect", "--format",
                                "{{.Config.Image}}	{{.Image}}", container])
    if not image["ok"]:
        # Only the docker driver has an excuse for no container to inspect: it runs the
        # engine's embedded BuildKit. With a docker-container builder that container *is*
        # the builder, so a failed read is a lost observation, and the two must not be
        # recorded in the same shape.
        if driver == "docker":
            return {**observed, "error": image["error"],
                    "reason": ("the docker driver runs the engine's embedded BuildKit, so no "
                               "BuildKit image digest exists to record")}
        return {**observed, "ok": False,
                "error": f"{container} is this builder's BuildKit container and could not "
                         f"be read: {image['error']}"}
    reference, _, image_id = image["value"].partition("	")
    return {**observed, "container": container, "container_reference": reference or None,
            "image_id": image_id or None,
            "digest": (image_id.split(":", 1)[1] if image_id.startswith("sha256:") else None)}


def buildx_version(run: CommandRunner) -> dict:
    """The buildx binary's own version line, parsed rather than templated.

    `docker buildx version` prints `<install path> <version> <commit>` on one line and
    takes no `--format`, so parsing is the only way to ask -- and the raw line stays in
    the record so a reader can check the parse against what the tool actually said.
    """
    record = read(run, "buildx_version", ["docker", "buildx", "version"])
    if record["ok"]:
        parts = record["value"].split()
        record["version"] = parts[1] if len(parts) > 1 else None
        record["commit"] = parts[2] if len(parts) > 2 else None
    return record


def observe(run: CommandRunner, docker_cli_build: bool = False) -> dict:
    """Every producer field the tooling can be asked about, read back.

    `docker_cli_build` is not a cosmetic flag. `docker build` with BuildKit on runs the
    *daemon's embedded* BuildKit; a buildx builder created earlier in the job is then not
    the producer of these bytes at all. CI's evidence at `d8472d0` showed exactly that
    misreading: the record named a `docker-container` builder whose container "could not
    be read", because the image had never been built through that container. Calling a
    wrong question unanswered is not the same as recording an absence, so the caller says
    which path built the image and this function answers for that path -- keeping the
    failed `inspect` in the record as `inspected_builder` rather than dropping it.
    """
    buildx = buildx_version(run)
    engine = read(run, "docker_server", ["docker", "version", "--format",
                                         "{{.Server.Version}}"])
    builder = buildx_builder(run)
    if docker_cli_build and not builder.get("ok"):
        builder = {
            "ok": True,
            "build_path": "docker CLI (`docker build`)",
            "driver": "engine-embedded BuildKit",
            "engine_version": engine.get("value"),
            "buildx_cli": buildx.get("version"),
            "note": ("`docker build` runs the daemon's embedded BuildKit, so no BuildKit "
                     "container or image digest belongs to this build; the builder that "
                     "`buildx inspect` names did not produce these bytes"),
            "inspected_builder": builder,
        }
    return {"buildx_version": buildx, "builder": builder, "docker_engine": engine}


def _images_match(pinned: str | None, seen: str | None) -> bool | None:
    """Do the pinned BuildKit image and the one that ran name the same bytes?

    By digest when both carry one, because a tag on either side is a pointer the vendor
    can move; by whole reference otherwise. Neither side being readable is `None`, not
    `False` -- the evidence must not turn a missing observation into an accusation.
    """
    if not pinned or not seen:
        return None
    pinned_digest = pinned.rsplit("@", 1)[-1] if "@" in pinned else None
    seen_digest = seen.rsplit("@", 1)[-1] if "@" in seen else None
    if pinned_digest and seen_digest:
        return pinned_digest == seen_digest
    return pinned == seen


def compare(configured: dict, observed: dict) -> dict:
    """Pin against production, as a field -- not as an editorial judgement.

    The release declares a builder and then records the one that ran; until now a reader
    had to diff the two objects by eye, which is how a `v0.37.1` pin next to an observed
    `v0.37.0` could pass unnoticed through three CI artifacts. `matches` is None when
    either side is unreadable: "unknown" must not be recorded as disagreement, and a
    comparison nobody has to look at is a comparison nobody honours.
    """
    seen_buildx = observed["buildx_version"].get("version")
    seen_image = observed["builder"].get("container_reference")
    pinned_buildx = configured.get("buildx_version")
    pinned_image = configured.get("buildkit_image")
    return {
        "buildx_version": {"pinned": pinned_buildx, "observed": seen_buildx,
                           "matches": (seen_buildx == pinned_buildx
                                       if seen_buildx and pinned_buildx else None)},
        "buildkit_image": {"pinned": pinned_image, "observed": seen_image,
                           "matches": _images_match(pinned_image, seen_image)},
    }


def configured_values(lock: dict, buildkit_entry: str, buildx_entry: str = "buildx") -> dict:
    """What this repository declares, kept apart from what ran.

    Both halves come from `deployment/third-party-images.json` rather than from an
    environment variable, so the release cannot record a pin it did not ship: that
    file is the same one the deployment gates already read, and a workflow that
    disagrees with it is a test failure (`test_build_producer_contract.py`).
    """
    image = next((item for item in lock["images"] if item["name"] == buildkit_entry), None)
    binary = next((item for item in lock.get("binaries", []) if item["name"] == buildx_entry),
                  None)
    return {
        "buildx_version": binary.get("version") if binary else None,
        "buildkit_image": image["image_ref"] if image else None,
        "lock_entries": {"buildkit": buildkit_entry if image else None,
                         "buildx": buildx_entry if binary else None},
        "source": "deployment/third-party-images.json",
    }


def runner_identity(environ) -> dict:
    """What the runner says about itself, absent where it is not a hosted runner."""
    keys = ("RUNNER_ENVIRONMENT", "RUNNER_NAME", "ImageOS", "ImageVersion", "RUNNER_ARCH")
    return {key.lower(): environ.get(key) for key in keys if environ.get(key)}


def main(argv: list[str] | None = None, run: CommandRunner | None = None,
         environ=None) -> int:
    parser = argparse.ArgumentParser(description="record the producer of a build")
    parser.add_argument("--out", required=True)
    parser.add_argument("--buildkit-entry", default="buildkit-buildkit")
    parser.add_argument("--docker-cli-build", action="store_true",
                        help="the build ran through `docker build`, whose BuildKit is the daemon")
    parser.add_argument("--lock", default="deployment/third-party-images.json")
    args = parser.parse_args(argv)
    runner = run or run_command
    env = environ if environ is not None else __import__("os").environ

    with open(args.lock, encoding="utf-8") as handle:
        lock = json.load(handle)
    configured = configured_values(lock, args.buildkit_entry)
    observed = observe(runner, docker_cli_build=args.docker_cli_build)
    payload = {
        "configured": configured,
        "observed": observed,
        "comparison": compare(configured, observed),
        "runner": runner_identity(env),
    }
    missing = [key for key, record in payload["observed"].items()
               if isinstance(record, dict) and not record.get("ok")
               and record.get("reason") is None]
    if missing:
        payload["incomplete"] = missing
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    # An unreadable producer is reported on stdout and still exits 0: the build already
    # happened, and failing here would hide the build evidence behind a recorder's own
    # complaint. `incomplete` is the field the release gate reads.
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
