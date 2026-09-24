"""Install the one buildx executable a CAP release-equivalent build is allowed to run under.

Batch 3 A2.1 proved this mechanism in a non-publishing job; A2.2 puts the same mechanism under
the path that builds what a release publishes, and does it in **one** implementation, because two
copies of an install recipe is how a release job and its own CI rehearsal start disagreeing.
F-44 is the reason the file exists at all: `docker buildx` dispatches to whichever plugin the
runner image happens to carry, and Batch 3A measured the same job text answering v0.37.0 and
v0.37.1 on different days. A build whose producer is chosen by the machine is not a reproducible
build.

The order is the contract, and each step is here because skipping it would let a specific failure
look like success:

    1. pin        read scripts/release/controlled_buildx.json (one definition of that shape,
                  borrowed from the recorder that scores it later)
    2. path       refuse to install into a docker CLI plugin directory -- before downloading,
                  so a mistaken target cannot even displace the runner's own plugin
    3. download   fetch the pinned asset to a `.part` file; nothing at the target path yet
    4. integrity  size and sha256 against the *repository's* pin, never against a digest
                  served by the host the bytes came from. **A mismatch stops here, before the
                  downloaded file is chmod-ed, executed, or used to create a builder**: a
                  binary that is not the pinned bytes must never get the chance to answer
                  `version` with the right string.
    5. install    0755, moved into place
    6. read-back  `<path> version` -- the first time those bytes run -- and the version and
                  commit have to be what the pin claims, or the pin's own derivation is wrong
                  and nothing downstream can be trusted
    7. declaration the BuildKit reference the caller wants the builder to run has to be the one
                  `deployment/third-party-images.json` locks; a job whose two in-tree
                  declarations disagree is stopped before it builds anything
    8. builder    `<path> create --name … --driver docker-container --driver-opt image=… --use`,
                  then `inspect` that name and read its driver back
    9. plugin     what `docker buildx` *would* have dispatched to, recorded and never fatal

Step 9 is the point of the whole exercise being kept in evidence: A2.1's records are only
meaningful while the runner's own answer is still visible beside them, unscored.

What this file does **not** do is build anything, or decide whether a mismatch blocks a release.
It installs, the build jobs build, the recorder (`record_build_producer.py`) reads the machine
back independently, and `release.yml`'s completeness gate is what refuses publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import record_build_producer as recorder  # the sibling module, hence the sys.path line above

FETCH_TIMEOUT_SECONDS = 900
CHUNK_BYTES = 1 << 20
#: A path may not be a docker CLI plugin directory or the plugin file inside one. The substring
#: check is deliberately blunt: `docker buildx` finds its plugin by name in these directories, so
#: writing there would make the *runner* the producer again while the record claimed otherwise.
REFUSED_MARKERS = ("cli-plugins",)
REFUSED_BASENAMES = ("docker-buildx",)
#: Recorded so an evidence reader can see which rule the target was accepted under.
POLICY_NOTE = ("the target is an absolute path outside every docker CLI plugin directory; the "
               "pinned bytes are hashed before they are executed")


class InstallError(Exception):
    """Why the install stopped, at which step, and everything it had read when it did."""

    def __init__(self, step: str, message: str, facts: dict | None = None) -> None:
        super().__init__(message)
        self.step = step
        self.message = message
        self.facts = dict(facts or {})


def default_fetch(url: str, dest: Path, timeout: int = FETCH_TIMEOUT_SECONDS) -> int:
    """Fetch `url` to `dest` and return the byte count written. Test seam: pass your own."""
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "cap-controlled-buildx"})
    with (urllib.request.urlopen(request, timeout=timeout) as response,
          Path(dest).open("wb") as handle):
        shutil.copyfileobj(response, handle, length=CHUNK_BYTES)
    return Path(dest).stat().st_size


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def plugin_paths() -> list[str]:
    """The concrete CLI plugin files the recorder itself looks for, on this machine."""
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
    return [str(Path(template.format(home=home)) / "docker-buildx")
            for template in recorder.PLUGIN_DIRS]


def refusal_reason(target: str) -> str | None:
    """Why this path may not receive the pinned binary, or None when it may.

    Checked *before* any network access: an install that writes into a plugin directory has
    already changed what the runner would do next, whether or not the bytes are right.
    """
    if not target:
        return "no install path was given"
    # Absolute either for the platform this runs on, or in POSIX form: the mechanism runs on Linux
    # runners and is tested on a Windows machine, where `os.path.isabs("/tmp/x")` is False.
    if not (os.path.isabs(str(target)) or PurePosixPath(str(target)).is_absolute()):
        return f"{target!r} is not an absolute path; a relative target is whatever cwd says"
    normalised = str(target).replace("\\", "/")
    if any(marker in normalised for marker in REFUSED_MARKERS):
        return f"{target!r} is inside a docker CLI plugin directory"
    if Path(normalised).name in REFUSED_BASENAMES:
        return f"{target!r} would replace the docker CLI plugin itself"
    for plugin in plugin_paths():
        if normalised == plugin.replace("\\", "/"):
            return f"{target!r} is the runner's own buildx plugin"
    return None


def load_pin(pin_path: str, facts: dict) -> dict:
    """The pin, through the recorder's own reader, so both tools agree on its shape."""
    pin = recorder.load_controlled_pin(pin_path)
    if not pin.get("ok"):
        raise InstallError("pin",
                           pin.get("error") or f"the controlled buildx pin at {pin_path!r} is "
                           "unreadable, so nothing here declares what the bytes should be",
                           facts)
    for field in ("download_url", "expected_sha256", "version", "expected_git_commit"):
        if not pin.get(field):
            raise InstallError("pin", f"{pin_path} declares no {field}", facts)
    if not str(pin["download_url"]).startswith("https://"):
        raise InstallError("pin",
                           f"the pinned asset is not fetched over https ({pin['download_url']!r})",
                           facts)
    if not str(pin["expected_sha256"]).startswith("sha256:"):
        raise InstallError("pin", f"unexpected integrity algorithm {pin['expected_sha256']!r}",
                           facts)
    return pin


def declared_buildkit(lock_path: str | None, buildkit: str | None, facts: dict) -> None:
    """A job that names a BuildKit the lock does not is stopped before it builds.

    This is not a second opinion on the measurement -- the recorder still compares the same two
    files and reports the verdict in `lock_vs_workflow`. It is a refusal to *start* a build whose
    own declarations already disagree, which is the difference between an unwelcome measurement
    (evidence, keep it) and a job that cannot be right (a defect, stop now).
    """
    if not lock_path or not buildkit:
        return
    path = Path(lock_path)
    if not path.exists():
        raise InstallError("declaration", f"no lock file at {lock_path!r}", facts)
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise InstallError("declaration", f"{lock_path}: {type(error).__name__}: {error}",
                           facts) from error
    entries = [item for item in lock.get("images", []) if item.get("name") == "buildkit-buildkit"]
    if not entries:
        raise InstallError("declaration", f"{lock_path} names no buildkit-buildkit entry", facts)
    locked = entries[0].get("image_ref")
    if locked != buildkit:
        raise InstallError(
            "declaration",
            f"the builder would run {buildkit!r} while {lock_path} locks {locked!r}; the two "
            "declarations in this repository disagree, and no build should decide which wins",
            facts)
    facts["declaration"] = {"lock": str(lock_path), "buildkit_image": locked,
                            "relation": "equal"}


def create_builder(run: Callable[[list[str]], tuple[int, str, str]], path: str, builder: str,
                   driver: str, buildkit: str, facts: dict) -> dict:
    """Create the named builder with the pinned executable, then read the name back."""
    argv = [path, "create", "--name", builder, "--driver", driver,
            "--driver-opt", f"image={buildkit}", "--use"]
    code, out, err = run(argv)
    if code != 0:
        raise InstallError("builder", f"`{' '.join(argv)}` exited {code}: {(err or out).strip()}",
                           facts)
    code, out, err = run([path, "inspect", "--bootstrap", builder])
    if code != 0:
        raise InstallError("builder",
                           f"the builder {builder!r} was created but cannot be inspected "
                           f"(exit {code}): {(err or out).strip()}", facts)
    seen_driver = None
    seen_name = None
    for line in (out or "").splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        name, text = key.strip().lower(), value.strip()
        if name == "driver" and seen_driver is None:
            seen_driver = text
        elif name == "name" and seen_name is None:
            seen_name = text
    answer = {"builder": builder, "driver": driver, "buildkit_image": buildkit,
              "inspected_name": seen_name, "inspected_driver": seen_driver}
    facts["builder"] = answer
    if seen_driver != driver:
        raise InstallError("builder",
                           f"builder {builder!r} reports driver {seen_driver!r}, not {driver!r}",
                           facts)
    if seen_name and seen_name != builder:
        raise InstallError("builder", f"asked for builder {builder!r} and got {seen_name!r}", facts)
    return answer


def plugin_diagnostic(run: Callable[[list[str]], tuple[int, str, str]], facts: dict) -> dict:
    """What `docker buildx` would have dispatched to. Read, recorded, never fatal."""
    answer: dict = {"command": "docker buildx version"}
    try:
        code, out, err = run(["docker", "buildx", "version"])
    except OSError as error:  # a runner with no docker at all is still a fact, not a crash
        answer.update({"status": "ERROR", "error": f"{type(error).__name__}: {error}"})
        facts["runner_plugin"] = answer
        return answer
    answer["exit"] = code
    if code == 0:
        parsed = recorder.parse_version_line((out or "").strip())
        answer.update({"status": "READ", "version": parsed.get("version"),
                       "commit": parsed.get("commit"), "path": parsed.get("path"),
                       "value": (out or "").strip()})
    else:
        answer.update({"status": "ERROR", "error": ((err or out) or "").strip()})
    answer["note"] = ("diagnostic only: the build runs under the pinned executable installed "
                      "above, and this answer is what F-44 was filed over")
    facts["runner_plugin"] = answer
    return answer


def install(pin_path: str, target: str, *, builder: str | None = None, driver: str | None = None,
            buildkit: str | None = None, lock_path: str | None = None,
            fetch: Callable[[str, Path], int] = default_fetch,
            run: Callable[[list[str]], tuple[int, str, str]] = recorder.run_command,
            hash_file: Callable[[Path], str] = sha256_of) -> dict:
    """Run the ordered install and return the facts. Raises `InstallError` at the failed step."""
    facts: dict = {
        "tool": "scripts/release/install_controlled_buildx.py",
        "status": "STARTED",
        "target": target,
        "steps_completed": [],
        "policy": {"refused_markers": list(REFUSED_MARKERS),
                   "refused_basenames": list(REFUSED_BASENAMES),
                   "note": POLICY_NOTE},
    }

    def done(step: str) -> None:
        facts["steps_completed"].append(step)

    pin = load_pin(pin_path, facts)
    facts["pin"] = {"source": pin["source"], "version": pin["version"],
                    "expected_git_commit": pin["expected_git_commit"],
                    "expected_sha256": pin["expected_sha256"],
                    "download_url": pin["download_url"], "declared_path": pin.get("declared_path"),
                    "platform": pin.get("platform")}
    if pin.get("declared_path") and pin["declared_path"] != target:
        # Both files name the producer's path. The pin's is not authoritative over the job's --
        # `configured.workflow` is the authority the recorder compares -- but an installer given
        # one path while the repository declares another would install a file nothing observes.
        raise InstallError(
            "pin", f"asked to install to {target!r} while {pin['source']} declares "
            f"{pin['declared_path']!r}; install one path or change the declaration, not both",
            facts)
    done("pin")

    reason = refusal_reason(target)
    if reason:
        raise InstallError("path", reason, facts)
    done("path")

    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    size = fetch(pin["download_url"], part)
    facts["download"] = {"url": pin["download_url"], "bytes": size, "partial": str(part)}
    done("download")

    expected_bytes = pin.get("asset_size_bytes")
    if expected_bytes is not None and size != expected_bytes:
        raise InstallError("integrity",
                           f"{part} is {size} bytes; {pin['source']} pins the asset at "
                           f"{expected_bytes}, so the file is truncated or not the asset pinned",
                           facts)
    computed = hash_file(part)
    facts["integrity"] = {
        "expected": pin["expected_sha256"], "computed": computed, "algorithm": "sha256",
        "of": str(part),
        "relation": "equal" if computed == pin["expected_sha256"] else "different",
    }
    if computed != pin["expected_sha256"]:
        # Nothing has been executed and nothing is at the target path. This is the hard stop the
        # approval asks for, and it is placed here deliberately: a checksum-verified file is the
        # earliest point at which the bytes can be trusted to answer a question about themselves.
        raise InstallError("integrity",
                           f"{part} hashes to {computed}, not the pinned {pin['expected_sha256']}. "
                           "The file is left in place for inspection and is never executed.", facts)
    done("integrity")

    os.chmod(part, 0o755)
    os.replace(part, destination)
    facts["install"] = {"path": str(destination), "mode": "0755", "replaced": str(part)}
    done("install")

    code, out, err = run([str(destination), "version"])
    if code != 0:
        raise InstallError("read-back", f"`{destination} version` exited {code}: "
                                        f"{(err or out).strip()}", facts)
    read_back = recorder.parse_version_line((out or "").strip())
    facts["read_back"] = {"command": f"{destination} version", "value": (out or "").strip(),
                          "version": read_back.get("version"),
                          "commit": read_back.get("commit"),
                          "path_reported_by_binary": read_back.get("path")}
    for field in ("version", "commit"):
        expected = pin["version"] if field == "version" else pin["expected_git_commit"]
        if read_back.get(field) != expected:
            raise InstallError(
                "read-back",
                f"the pinned bytes answer {field}={read_back.get(field)!r}, not {expected!r}. The "
                "asset's digest matches the pin, so the pin's own claim about what those bytes "
                "are is what is wrong -- and no build run with them should be certified.", facts)
    done("read-back")

    if builder and (not driver or not buildkit):
        raise InstallError("builder",
                           f"--builder {builder!r} needs --driver and --buildkit; a builder "
                           "created with an implied driver is the runner's choice again", facts)
    if builder and not lock_path:
        # B2 pinned a BuildKit image and F-44 found the pin unverified. A builder started from a
        # reference nothing compared with the shipped lock would reproduce exactly that state, so
        # a job that asks for a builder also has to say which lock it agrees with.
        raise InstallError("declaration",
                           f"--builder {builder!r} without --lock leaves {buildkit!r} unchecked "
                           "against deployment/third-party-images.json", facts)
    declared_buildkit(lock_path, buildkit, facts)
    if buildkit and lock_path:
        done("declaration")

    if builder:
        create_builder(run, str(destination), builder, driver, buildkit, facts)
        done("builder")

    plugin_diagnostic(run, facts)
    done("plugin-diagnostic")
    facts["status"] = "INSTALLED"
    return facts


def write_facts(facts: dict, facts_path: str | None) -> None:
    if not facts_path:
        return
    destination = Path(facts_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, fetch: Callable | None = None,
         run: Callable | None = None, hash_file: Callable | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pin", required=True,
                        help="the JSON pinning the executable (controlled_buildx.json)")
    parser.add_argument("--path", required=True,
                        help="the absolute path to install it to -- never a CLI plugin path")
    parser.add_argument("--builder", default=None, help="create this named builder after installing")
    parser.add_argument("--driver", default=None, help="the builder's driver (docker-container)")
    parser.add_argument("--buildkit", default=None,
                        help="the BuildKit image reference the builder runs, digest-pinned")
    parser.add_argument("--lock", default=None,
                        help="deployment/third-party-images.json, to check --buildkit against it")
    parser.add_argument("--facts", default=None, help="where to write the install facts JSON")
    args = parser.parse_args(argv)

    seams = {"fetch": fetch, "run": run, "hash_file": hash_file}
    injected = {name: value for name, value in seams.items() if value is not None}
    try:
        facts = install(args.pin, args.path, builder=args.builder, driver=args.driver,
                        buildkit=args.buildkit, lock_path=args.lock, **injected)
    except InstallError as error:
        facts = dict(error.facts)
        facts["status"] = "ERROR"
        facts["failed_step"] = error.step
        facts["error"] = error.message
        write_facts(facts, args.facts)
        print(json.dumps(facts, indent=2, sort_keys=True))
        print(f"controlled buildx install failed at {error.step}: {error.message}",
              file=sys.stderr)
        return 1
    write_facts(facts, args.facts)
    print(json.dumps(facts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
