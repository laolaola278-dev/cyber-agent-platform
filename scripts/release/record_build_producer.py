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

#: One command read, with the argv that answers it and the field it fills.
CommandRunner = Callable[[list[str]], tuple[int, str]]


def run_command(argv: list[str]) -> tuple[int, str]:
    """The real runner: argv, no shell, exit code and stdout handed back."""
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, f"{type(error).__name__}: {error}"
    return completed.returncode, (completed.stdout or "").strip()


def read(run: CommandRunner, name: str, argv: list[str]) -> dict:
    """One command, recorded honestly: what it answered, or that it did not answer."""
    code, out = run(argv)
    text = out.splitlines()[0].strip() if out else ""
    if code != 0 or not text:
        return {"ok": False, "command": " ".join(shlex.quote(part) for part in argv),
                "error": text or f"exit {code}"}
    return {"ok": True, "command": " ".join(shlex.quote(part) for part in argv), "value": text}


def buildx_builder_container(run: CommandRunner) -> dict:
    """The BuildKit container a `docker-container` driver builder is running, if any.

    The image reference and the image ID are different facts -- the first says what the
    builder was asked to run, the second says what the daemon resolved -- and the digest the
    release cares about is only in the second one when the reference was pulled by digest.
    """
    listed = read(run, "builders", ["docker", "buildx", "inspect", "--format",
                                    "{{.Driver}} {{.Name}}"])
    if not listed["ok"]:
        return {"ok": False, "error": listed["error"], "reason": "no builder could be inspected"}
    driver, _, builder = listed["value"].partition(" ")
    container = f"buildx_buildkit_{builder}" if builder else ""
    if not container:
        return {"ok": False, "error": "buildx named no builder", "driver": driver}
    image = read(run, "image", ["docker", "inspect", "--format",
                                "{{.Config.Image}}\t{{.Image}}", container])
    if not image["ok"]:
        # Only the docker driver has an excuse for no container to inspect: it runs the
        # engine's embedded BuildKit. With a docker-container builder that container *is*
        # the builder, so a failed read is a lost observation, and the two must not be
        # recorded in the same shape.
        if driver == "docker":
            return {"ok": False, "driver": driver, "error": image["error"],
                    "reason": ("the docker driver runs the engine's embedded BuildKit, so no "
                               "BuildKit image digest exists to record")}
        return {"ok": False, "driver": driver,
                "error": f"{container} is this builder's BuildKit container and could not be "
                         f"read: {image['error']}"}
    reference, _, image_id = image["value"].partition("\t")
    return {"ok": True, "driver": driver, "builder": builder, "container": container,
            "reference": reference or None, "image_id": image_id or None,
            "digest": (image_id.split(":", 1)[1] if image_id.startswith("sha256:") else None)}


def observe(run: CommandRunner) -> dict:
    """Every producer field the tooling can be asked about, read back."""
    buildx = read(run, "buildx_version", ["docker", "buildx", "version", "--format",
                                          "{{.Version}}"])
    observed = {
        "buildx_version": buildx,
        "builder": buildx_builder_container(run),
        "docker_engine": read(run, "docker_server", ["docker", "version", "--format",
                                                     "{{.Server.Version}}"]),
    }
    return observed


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
    parser.add_argument("--lock", default="deployment/third-party-images.json")
    args = parser.parse_args(argv)
    runner = run or run_command
    env = environ if environ is not None else __import__("os").environ

    with open(args.lock, encoding="utf-8") as handle:
        lock = json.load(handle)
    payload = {
        "configured": configured_values(lock, args.buildkit_entry),
        "observed": observe(runner),
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
