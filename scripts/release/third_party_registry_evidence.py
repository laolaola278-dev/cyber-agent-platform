#!/usr/bin/env python3
# The design this implements is §F.1 of
# docs/quality/cap-post-rc-batch-2-design-freeze-2026-09-22.md. Read that contract before
# changing a failure mode below: each one exists because something here was once claimed
# without it.
"""Resolve every third-party image this repository depends on and write it as tracked evidence.

The artifact separates two things a hand-edited lock file conflated:

* ``observation`` -- metadata about the read: when it was taken, by which generator, by which
  method. It is allowed to change on every run.
* ``images`` -- the digest claims themselves, which describe registry content. Two runs
  against an unchanged registry must produce an identical claims section, and the
  reproducibility check compares the *parsed objects*, not a text substitution of a date.

The target list is derived, never hand-maintained: every tracked Dockerfile's ``FROM`` and
``# syntax=`` lines, every external ``image:`` in the compose file, and every entry of
``deployment/third-party-images.json`` are read and unioned. That turns "the lock says nothing
about them" (F-24) from a sentence into a failure.

Each claim is verified three ways before anything is written: the served
``Docker-Content-Digest`` must equal the digest recomputed from the body; the same content
refetched *by that digest* must be byte-identical to the tag response; and a multi-arch index
must carry exactly one ``linux/amd64`` child. Any doubt stops the run and leaves the previous
artifact where it was -- a partial evidence file is worse than none, because a partial file
reads like a complete one.

Nothing is written to a registry, and no credential is ever serialised: pull tokens come from
each registry's own ``WWW-Authenticate`` challenge, are used in a request header, and the
output is scanned for them before the file is replaced.

Usage::

    python scripts/release/third_party_registry_evidence.py --check    # verify, write nothing
    python scripts/release/third_party_registry_evidence.py            # write the artifact
    python scripts/release/third_party_registry_evidence.py --conflict-check

Exit codes: 0 success; 1 a target was refused or the target set is incomplete; 2 the run could
not start (no git index, bad arguments, unwritable path).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCK_PATH = "deployment/third-party-images.json"
COMPOSE_PATH = "docker-compose.yml"
DEFAULT_OUT = "docs/quality/artifacts/registry-resolution/third-party-registries.json"
SCHEMA = "cap.third-party-registry-evidence/v1"

#: Only an index/manifest-list satisfies this Accept list. A bare request can be served a
#: legacy schema1 *conversion* whose digest differs -- the trap that
#: policy.minio_object_store.upgrade_procedure documents, and the reason a pin taken from a
#: naive GET can name bytes nobody else agrees are the image.
INDEX_ACCEPT = ("application/vnd.oci.image.index.v1+json, "
                "application/vnd.docker.distribution.manifest.list.v2+json")
INDEX_MEDIA_TYPES = frozenset({
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
})
USER_AGENT = "cap-third-party-registry-evidence/1.0"
REGISTRY_HOSTS = {"docker.io": "registry-1.docker.io", "quay.io": "quay.io"}
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DOCKERFILE_RE = re.compile(r"(^|/)Dockerfile(\.[A-Za-z0-9_-]+)?$")
#: Images this repository builds and publishes. They are products, not third-party inputs; one
#: appearing as an input would mean a surface started pulling its own release by tag.
OWNED_PREFIXES = ("ghcr.io/example/", "ghcr.io/laolaola278-dev/")


class Refusal(Exception):
    """Something this tool must not write into evidence."""


@dataclass(frozen=True)
class Target:
    """One logical coordinate that a tracked surface asks a registry for."""

    name: str
    registry: str
    repository: str
    tag: str
    pinned_digest: str | None = None
    #: Every target here is a multi-arch index today. A new single-manifest target has to say
    #: so at its own definition rather than quietly weaken the check for everything else.
    require_index: bool = True
    sources: tuple[str, ...] = field(default=(), compare=False)

    @property
    def reference(self) -> str:
        return self.tag or self.pinned_digest or ""

    @property
    def coordinate(self) -> str:
        if self.tag:
            return f"{self.registry}/{self.repository}:{self.tag}"
        return f"{self.registry}/{self.repository}@{(self.pinned_digest or '')[:19]}!"


# ---------------------------------------------------------------------- target derivation


def tracked_paths(root: str, pattern: re.Pattern) -> list[str]:
    """The files the repository ships, read from the git index rather than a directory walk.

    A walk would treat gitignored scratch as a surface -- this repository keeps probe
    Dockerfiles and stale worktrees under ``_tmp/`` -- and a fresh clone contains none of it.
    The index answers the same list in both places, which is what makes this generator
    runnable from a clone.
    """
    proc = subprocess.run(("git", "-C", root, "ls-files"), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, check=False)
    if proc.returncode != 0:
        raise Refusal(f"git ls-files failed: {proc.stderr.strip()}")
    lines = sorted({line.strip().replace("\\", "/") for line in proc.stdout.splitlines()})
    return [path for path in lines if path and pattern.search(path)]


def split_image_ref(ref: str) -> tuple[str, str, str | None, str | None]:
    """(registry, repository, tag, digest). ``tag`` is None when the surface wrote no tag,
    which is a different fact from writing ``:latest``."""
    base, _, tail = ref.partition("@")
    digest = tail or None
    host = "docker.io"
    head, sep, rest = base.partition("/")
    if sep and ("." in head or ":" in head or head == "localhost"):
        host, base = head, rest
    tag: str | None = None
    if ":" in base:
        name, _, tag = base.rpartition(":")
        base = name
    repository = base if (host != "docker.io" or "/" in base) else f"library/{base}"
    return host, repository, tag, digest


def _lines(root: str, path: str) -> list[str]:
    with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()


def dockerfile_refs(root: str) -> dict[str, list[str]]:
    """Every external reference a tracked Dockerfile pulls, keyed by the raw reference."""
    found: dict[str, list[str]] = {}
    for path in tracked_paths(root, DOCKERFILE_RE):
        if not os.path.exists(os.path.join(root, path)):
            continue
        for lineno, line in enumerate(_lines(root, path), 1):
            text = line.strip()
            syntax = re.match(r"^#\s*syntax=(\S+)", text)
            if syntax:
                found.setdefault(syntax.group(1), []).append(f"{path}:{lineno}#syntax")
                continue
            clause = re.match(r"^FROM\s+(\S+)(\s+AS\s+\S+)?$", text, re.IGNORECASE)
            if not clause:
                continue
            ref = clause.group(1)
            if ref == "scratch" or ref.startswith("${"):
                continue
            found.setdefault(ref, []).append(f"{path}:{lineno}")
    return found


def compose_refs(root: str) -> dict[str, list[str]]:
    """Every third-party ``image:`` the tracked compose file pulls."""
    if not os.path.exists(os.path.join(root, COMPOSE_PATH)):
        raise Refusal(f"{COMPOSE_PATH} is missing, so the compose target set cannot be derived")
    found: dict[str, list[str]] = {}
    for lineno, line in enumerate(_lines(root, COMPOSE_PATH), 1):
        match = re.match(r"^\s+image:\s*(\S+)\s*$", line)
        if not match:
            continue
        ref = match.group(1)
        if any(ref.startswith(prefix) for prefix in OWNED_PREFIXES):
            continue
        found.setdefault(ref, []).append(f"{COMPOSE_PATH}:{lineno}")
    return found


def _load_lock(root: str) -> dict:
    path = os.path.join(root, LOCK_PATH)
    if not os.path.exists(path):
        raise Refusal(f"{LOCK_PATH} is missing, so the lock's own images cannot be resolved")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _name_for(registry: str, repository: str, tag: str, lock: dict) -> str:
    """Reuse the lock's name for a coordinate the lock already knows."""
    for entry in lock["images"]:
        if (entry["registry"], entry["repository"], str(entry["tag"])) == (registry,
                                                                          repository, tag):
            return str(entry["name"])
    return f"{repository.replace('/', '-')}-{tag}"


def collect_targets(root: str = REPO_ROOT) -> list[Target]:
    """The union of every third-party coordinate a tracked surface asks for."""
    lock = _load_lock(root)
    per_ref: dict[str, list[str]] = {}
    for source in (dockerfile_refs(root), compose_refs(root)):
        for ref, sites in source.items():
            per_ref.setdefault(ref, []).extend(sites)

    by_digest = {(e["registry"], e["repository"], e.get("digest")): e for e in lock["images"]}
    targets: dict[tuple[str, str, str], Target] = {}

    for entry in lock["images"]:
        coordinate = (entry["registry"], entry["repository"], str(entry["tag"]))
        targets[coordinate] = Target(name=entry["name"], registry=entry["registry"],
                                     repository=entry["repository"], tag=coordinate[2],
                                     pinned_digest=entry.get("digest"),
                                     sources=tuple(entry.get("referenced_by") or ()))

    for ref, sites in sorted(per_ref.items()):
        registry, repository, tag, digest = split_image_ref(ref)
        if tag is None and digest is None:
            raise Refusal(f"{sites[0]} writes {ref!r} with neither a tag nor a digest, which "
                          "means :latest -- a certified build must not name a moving default")
        if tag is None:
            entry = by_digest.get((registry, repository, digest))
            tag = str(entry["tag"]) if entry else ""
            name = str(entry["name"]) if entry else f"{repository.rsplit('/', 1)[-1]}-digest"
        else:
            name = _name_for(registry, repository, tag, lock)
        coordinate = (registry, repository, tag)
        existing = targets.get(coordinate)
        if existing is None:
            targets[coordinate] = Target(name=name, registry=registry, repository=repository,
                                         tag=tag, pinned_digest=digest, sources=tuple(sites))
            continue
        merged = tuple(dict.fromkeys(existing.sources + tuple(sites)))
        targets[coordinate] = Target(name=existing.name, registry=existing.registry,
                                     repository=existing.repository, tag=existing.tag,
                                     pinned_digest=existing.pinned_digest or digest,
                                     require_index=existing.require_index, sources=merged)
    return sorted(targets.values(), key=lambda target: target.name)


def target_set_problems(targets: list[Target], root: str = REPO_ROOT) -> list[str]:
    """The derived set has to cover every surface that pulls third-party bytes."""
    problems: list[str] = []
    names = {target.name for target in targets}
    covered = {(t.registry, t.repository, t.tag) for t in targets}
    pinned = {(t.registry, t.repository, t.pinned_digest) for t in targets}

    for entry in _load_lock(root)["images"]:
        if entry["name"] not in names:
            problems.append(f"the lock names {entry['name']}, which no target resolves")

    def covers(ref: str) -> bool:
        registry, repository, tag, digest = split_image_ref(ref)
        return ((registry, repository, tag or "") in covered
                or (registry, repository, digest) in pinned)

    for source in (compose_refs(root), dockerfile_refs(root)):
        for ref, sites in sorted(source.items()):
            if sites[0].endswith("#syntax"):
                continue
            if not covers(ref):
                problems.append(f"{sites[0]} asks for {ref}, which no target resolves")
    return problems


# ------------------------------------------------------------------------- http plumbing


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class UrllibTransport:
    """The real client, injected so every check below is drivable without a network."""

    def get(self, url: str, headers: dict[str, str] | None = None) -> Response:
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=60) as answer:
                sent = {key.lower(): value for key, value in dict(answer.headers).items()}
                return Response(answer.status, sent, answer.read())
        except urllib.error.HTTPError as error:
            sent = {key.lower(): value for key, value in dict(error.headers).items()}
            return Response(error.code, sent, error.read())


def header_value(headers: dict[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


def parse_challenge(headers: dict[str, str]) -> dict[str, str]:
    raw = header_value(headers, "WWW-Authenticate")
    if not raw.lower().startswith("bearer"):
        raise Refusal(f"the authorisation response is not a Bearer challenge: {raw[:60]!r}")
    parts: dict[str, str] = {}
    for pair in raw[len("bearer"):].replace(",", "\x00").split("\x00"):
        key, _, value = pair.strip().partition("=")
        if key:
            parts[key.strip().lower()] = value.strip().strip('"')
    if not parts.get("realm"):
        raise Refusal("the Bearer challenge names no realm")
    return parts


class Registry:
    """Anonymous first, then whatever bearer authority the registry itself points at."""

    def __init__(self, transport: UrllibTransport | None = None) -> None:
        self.transport = transport or UrllibTransport()
        self.secrets: set[str] = set()

    @staticmethod
    def host(registry: str) -> str:
        return REGISTRY_HOSTS.get(registry, registry)

    def _get(self, url: str, token: str | None = None) -> Response:
        headers = {"User-Agent": USER_AGENT, "Accept": INDEX_ACCEPT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.transport.get(url, headers)

    def token(self, registry: str, repository: str) -> str | None:
        probe = self._get(f"https://{self.host(registry)}/v2/")
        if "www-authenticate" not in probe.headers:
            return None
        challenge = parse_challenge(probe.headers)
        # The scope comes from the repository being pulled, never from the challenge: ghcr
        # advertises scope="repository:user/image:pull" on its /v2/ probe, which is a
        # placeholder, and a token minted for it is answered 403. Realm and service are the
        # registry's to name; the resource is ours to name.
        scope = f"repository:{repository}:pull"
        url = (f"{challenge['realm']}?service={urllib.parse.quote(challenge.get('service', ''))}"
               f"&scope={urllib.parse.quote(scope)}")
        answer = self._get(url)
        if answer.status != 200:
            raise Refusal(f"{challenge['realm']} answered HTTP {answer.status} for a pull token")
        try:
            payload = json.loads(answer.body or b"{}")
        except json.JSONDecodeError as error:
            raise Refusal(f"{challenge['realm']} answered a token request with non-JSON: "
                          f"{error}") from error
        if not isinstance(payload, dict):
            raise Refusal(f"{challenge['realm']} answered a token request with a "
                          f"{type(payload).__name__}")
        token = payload.get("token") or payload.get("access_token")
        if not token:
            raise Refusal(f"{challenge['realm']} answered without a token field")
        self.secrets.add(str(token))
        return token

    def manifest(self, target: Target, reference: str, token: str | None) -> Response:
        url = (f"https://{self.host(target.registry)}/v2/{target.repository}"
               f"/manifests/{urllib.parse.quote(reference, safe=':@')}")
        answer = self._get(url, token)
        if answer.status == 401 and token is None:
            answer = self._get(url, self.token(target.registry, target.repository))
        if answer.status != 200:
            raise Refusal(f"{target.coordinate} as {reference[:24]} answered HTTP {answer.status}")
        if not answer.body:
            raise Refusal(f"{target.coordinate} answered 200 with an empty body")
        return answer


# ---------------------------------------------------------------------------- resolution


def resolve(target: Target, registry: Registry) -> dict:
    """One verified claim about one coordinate, or a Refusal naming the doubt."""
    token = registry.token(target.registry, target.repository)
    tag_response = registry.manifest(target, target.reference, token)

    served = header_value(tag_response.headers, "Docker-Content-Digest")
    if not served:
        raise Refusal(f"{target.coordinate}: the registry served no Docker-Content-Digest, so "
                      "nothing in the response names what was returned")
    if not DIGEST_RE.match(served):
        raise Refusal(f"{target.coordinate}: Docker-Content-Digest is not a sha256 digest "
                      f"({served[:24]}!)")
    recomputed = "sha256:" + hashlib.sha256(tag_response.body).hexdigest()
    if served != recomputed:
        raise Refusal(f"{target.coordinate}: the served digest {served[:20]}! is not the digest "
                      f"of the bytes served ({recomputed[:20]}!) -- a conversion or an "
                      "intermediary rewrote them")

    try:
        document = json.loads(tag_response.body)
    except json.JSONDecodeError as error:
        raise Refusal(f"{target.coordinate}: the manifest is not JSON ({error})") from error
    if not isinstance(document, dict):
        raise Refusal(f"{target.coordinate}: the manifest is a {type(document).__name__}")

    children = document.get("manifests")
    media_type = (document.get("mediaType")
                  or header_value(tag_response.headers, "Content-Type") or "")
    if children is None:
        if target.require_index:
            raise Refusal(f"{target.coordinate}: answered a single manifest ({media_type}) "
                          "where a multi-arch index is required -- pinning that digest would "
                          "freeze one architecture, not the image")
        platforms, amd64 = [], []
    else:
        if media_type and media_type not in INDEX_MEDIA_TYPES:
            raise Refusal(f"{target.coordinate}: an index body served as {media_type}")
        platforms = sorted({
            f"{(child.get('platform') or {}).get('os')}/"
            f"{(child.get('platform') or {}).get('architecture')}"
            for child in children
            if (child.get("platform") or {}).get("os") != "unknown"})
        amd64 = [child for child in children
                 if (child.get("platform") or {}).get("os") == "linux"
                 and (child.get("platform") or {}).get("architecture") == "amd64"]
        if len(amd64) != 1:
            raise Refusal(f"{target.coordinate}: {len(amd64)} linux/amd64 children, expected "
                          "exactly one -- which bytes the certification matrix runs on is "
                          "ambiguous, so no platform digest can be recorded")

    index_digest = served
    refetch = registry.manifest(target, index_digest, token)
    if refetch.body != tag_response.body:
        raise Refusal(f"{target.coordinate}: refetching by digest {index_digest[:20]}! returned "
                      "bytes that differ from the tag response -- the digest does not name what "
                      "the tag serves")

    pinned_still_resolves: bool | None = None
    if target.pinned_digest and target.pinned_digest != index_digest:
        # The surface pins bytes that the tag no longer names. Those bytes still have to be
        # there, or the deployment this evidence describes cannot pull -- which is a refusal,
        # not a footnote.
        pin_probe = registry.manifest(target, target.pinned_digest, token)
        pinned_still_resolves = pin_probe.status == 200 and bool(pin_probe.body)

    child = amd64[0] if amd64 else {}
    ref = (f"{target.registry}/{target.repository}:{target.tag}" if target.tag
           else f"{target.registry}/{target.repository}@{target.reference}")
    return {
        "name": target.name,
        "registry": target.registry,
        "repository": target.repository,
        "tag": target.tag,
        "ref": ref,
        "index_digest": index_digest,
        "digest_kind": ("manifest-list (multi-arch index)" if children is not None
                        else "single manifest"),
        "media_type": media_type,
        "platforms": platforms,
        "child_count": len(children) if children is not None else 1,
        "linux_amd64_child": {"digest": child.get("digest", ""),
                              "size": int(child.get("size") or 0),
                              "variant": (child.get("platform") or {}).get("variant")},
        "served_digest_verified_against_body": True,
        "refetch_by_digest_byte_identical": True,
        "pinned_by_surfaces": target.pinned_digest or "",
        "pinned_digest_still_resolves": pinned_still_resolves,
        # Drift is reported, not refused: the pinned digest is still valid bytes; what moved is
        # the tag that once pointed at it, and a reviewer decides what to do about that.
        "tag_currently_serves_pin": (None if not target.pinned_digest
                                     else target.pinned_digest == index_digest),
        "sources": sorted(set(target.sources)),
    }


def claim_set_problems(claims: list[dict], root: str = REPO_ROOT) -> list[str]:
    """Every coordinate some surface depends on has to appear in the written claims.

    `target_set_problems` checks the plan against the repository before any request; this
    checks the delivered artifact against the repository afterwards, which is what catches a
    claim that was dropped, deduplicated away, or resolved from a stale target list.
    """
    problems: list[str] = []
    resolved = {(claim["registry"], claim["repository"], claim["tag"]) for claim in claims}
    names = [claim["name"] for claim in claims]
    if len(names) != len(set(names)):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        problems.append(f"two claims share a name: {duplicates}")

    for entry in _load_lock(root)["images"]:
        coordinate = (entry["registry"], entry["repository"], str(entry["tag"]))
        if coordinate not in resolved:
            problems.append(f"the lock's {entry['name']} ({coordinate[2]}) is missing from "
                            "the claims, so the artifact is incomplete")

    sources = {}
    sources.update(compose_refs(root))
    sources.update({ref: sites for ref, sites in dockerfile_refs(root).items()
                    if not sites[0].endswith("#syntax")})
    for ref in sorted(sources):
        registry, repository, tag, digest = split_image_ref(ref)
        if tag is None:
            entry = {(e["registry"], e["repository"], e.get("digest")): e
                     for e in _load_lock(root)["images"]}.get((registry, repository, digest))
            tag = str(entry["tag"]) if entry else ""
        if (registry, repository, tag) not in resolved:
            problems.append(f"{ref} is pulled by a tracked surface and has no claim")
    return problems


def claims_are_reproducible(targets: list[Target], registry: Registry) -> list[str]:
    """Resolve each coordinate twice: one coordinate with two answers is a conflict.

    Two *different* coordinates sharing one digest is legitimate and expected -- the same
    content can answer to two tags -- so nothing here compares across names.
    """
    problems: list[str] = []
    for target in targets:
        first = resolve(target, registry)["index_digest"]
        second = resolve(target, registry)["index_digest"]
        if first != second:
            problems.append(f"{target.coordinate}: resolved {first[:20]}! then {second[:20]}! "
                            "-- the tag moved underneath this run")
    return problems


# ------------------------------------------------------------------------------- document


def build_document(claims: list[dict], verified_on: str) -> dict:
    return {
        "schema": SCHEMA,
        "generator": "scripts/release/third_party_registry_evidence.py",
        "observation": {
            "verified_on": verified_on,
            "target_count": len(claims),
            "method": (
                "Accept lists the OCI index and the Docker manifest list only; the served "
                "Docker-Content-Digest must equal sha256(body); the same content refetched by "
                "that digest must be byte-identical; an index must carry exactly one "
                "linux/amd64 child; unknown/unknown attestation entries are excluded from the "
                "platform list"),
        },
        "images": sorted(claims, key=lambda claim: claim["name"]),
    }


def digest_claims(document: dict) -> dict:
    """The claims section alone -- the half a reproducibility comparison may use."""
    return {"schema": document["schema"], "images": document["images"]}


def serialise(document: dict) -> str:
    # Sorted keys, LF newlines, two-space indent: a machine record, identical on every
    # platform. The working tree here is CRLF with core.autocrlf=true.
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def assert_no_credentials(text: str, secrets: set[str]) -> None:
    lowered = text.lower()
    for needle in ("authorization", "www-authenticate", "bearer ", "x-api-key", "password"):
        if needle in lowered:
            raise Refusal(f"the evidence would contain {needle!r}; refusing to write it")
    for secret in secrets:
        if secret and secret in text:
            raise Refusal("the evidence would contain a pull token; refusing to write it")


def write_atomic(path: str, text: str) -> None:
    """Replace the artifact only once the whole payload exists on disk."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".partial"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


#: A registry that resets mid-run is a transport fact, not a judgement about the image. It has
#: to be reported the same way a refusal is, because the caller's promise -- never overwrite a
#: complete artifact with a partial one -- only holds if the failure arrives as a Refusal.
TRANSPORT_ERRORS = (OSError, http.client.HTTPException, UnicodeDecodeError, ValueError)


def resolve_all(targets: list[Target], registry: Registry) -> list[dict]:
    claims: list[dict] = []
    failures: list[str] = []
    for target in targets:
        try:
            claims.append(resolve(target, registry))
        except Refusal as refusal:
            failures.append(str(refusal))
        except TRANSPORT_ERRORS as error:
            failures.append(f"{target.coordinate}: the registry connection failed "
                            f"({type(error).__name__}: {str(error)[:120]}) -- no claim is "
                            "written for a read that did not complete")
    if failures:
        raise Refusal("\n".join(failures))
    return claims


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="resolve third-party registry evidence")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true",
                        help="resolve and verify, but do not write the artifact")
    parser.add_argument("--conflict-check", action="store_true",
                        help="resolve every target twice and require one answer each")
    parser.add_argument("--verified-on", default=None,
                        help="observation date, YYYY-MM-DD (default: today, UTC). Never part "
                             "of the digest claims.")
    args = parser.parse_args(argv)

    if args.verified_on:
        try:
            dt.date.fromisoformat(args.verified_on)
        except ValueError:
            print("--verified-on must be YYYY-MM-DD", file=sys.stderr)
            return 2
        verified_on = args.verified_on
    else:
        verified_on = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")

    try:
        targets = collect_targets()
    except Refusal as refusal:
        print(f"cannot derive the target set: {refusal}", file=sys.stderr)
        return 2
    if not targets:
        print("the derived target set is empty; this run would prove nothing", file=sys.stderr)
        return 2
    problems = target_set_problems(targets)
    if problems:
        print("incomplete target set:", file=sys.stderr)
        for problem in problems:
            print(f"  * {problem}", file=sys.stderr)
        return 1

    registry = Registry()
    try:
        claims = resolve_all(targets, registry)
        if args.conflict_check:
            conflicts = claims_are_reproducible(targets, registry)
            if conflicts:
                print("conflicting resolutions:", file=sys.stderr)
                for conflict in conflicts:
                    print(f"  * {conflict}", file=sys.stderr)
                return 1
    except Refusal as refusal:
        for line in str(refusal).splitlines():
            print(f"refused: {line}", file=sys.stderr)
        print("the previous evidence file, if any, is left untouched", file=sys.stderr)
        return 1

    gaps = claim_set_problems(claims)
    if gaps:
        print("the claims are incomplete:", file=sys.stderr)
        for gap in gaps:
            print(f"  * {gap}", file=sys.stderr)
        return 1

    document = build_document(claims, verified_on)
    text = serialise(document)
    try:
        assert_no_credentials(text, registry.secrets)
    except Refusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 1

    out_path = args.out if os.path.isabs(args.out) else os.path.join(REPO_ROOT, args.out)
    if args.check:
        print(f"verified {len(claims)} targets; --check wrote nothing to {args.out}")
    else:
        write_atomic(out_path, text)
        print(f"wrote {args.out}: {len(claims)} targets, observed {verified_on}")
    for claim in document["images"]:
        drift = "" if claim["tag_currently_serves_pin"] in (None, True) \
            else "   <-- the tag no longer serves the pinned digest"
        print(f"  {claim['name']:36} {claim['index_digest']}{drift}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
