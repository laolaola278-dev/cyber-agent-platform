"""F-20: every external Dockerfile base is pinned by digest and recorded in the lock.

A release commit must rebuild to the same bytes, and a base image named by tag is
a pointer somebody else moves. `deployment/third-party-images.json` already held
the *runtime* pulls (MinIO, PostgreSQL) to that standard while the *build* bases --
`python:3.13-slim`, `node:22-alpine`, `nginx:1.30.4-alpine`,
`python:3.13.12-slim-bookworm`, `ghcr.io/astral-sh/uv:0.8.3` -- stayed mutable, so
a rebuilt 1.0.x image could carry different OpenSSL libraries than the certified
one with nothing in the lock to say so.

ARTIFACT-GATE 3 (external FROMs digest-pinned) and GATE 4 (the lock covers every
one of them) are this file. The internal base -- the browser image layering on the
HTTP sandbox image -- is deliberately *not* resolved by a digest here: it is a
build input the caller names, and `test_release_image_completeness.py` holds that
contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = PROJECT_ROOT / "deployment" / "third-party-images.json"
LOCK = json.loads(LOCK_PATH.read_text("utf-8"))

#: Build bases the lock must not be allowed to drift from.
BASE_ROLE = "Dockerfile base image"


def _dockerfiles() -> list[Path]:
    found = []
    for candidate in sorted(PROJECT_ROOT.rglob("Dockerfile*")):
        parts = set(candidate.relative_to(PROJECT_ROOT).parts)
        if parts & {"_tmp", "outputs", "node_modules", ".venv", ".git"}:
            continue
        if candidate.is_file():
            found.append(candidate)
    assert found, "no Dockerfiles found -- this scan would pass vacuously"
    return found


_FROM = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", re.MULTILINE)
_ARG_DEFAULT = re.compile(r"^ARG\s+(\w+)=(\S+)", re.MULTILINE)


def dockerfile_froms() -> dict[str, list[str]]:
    """Every FROM reference each Dockerfile can build from.

    ``FROM ${NAME}`` resolves to the ARG's default when it has one; with no
    default the caller must name it, so nothing is recorded here and no
    mutable coordinate can hide in that slot either.
    """
    found: dict[str, list[str]] = {}
    for dockerfile in _dockerfiles():
        rel = dockerfile.relative_to(PROJECT_ROOT).as_posix()
        text = dockerfile.read_text(encoding="utf-8")
        defaults = dict(_ARG_DEFAULT.findall(text))
        refs: list[str] = []
        for ref in _FROM.findall(text):
            if ref == "scratch":
                continue
            if ref.startswith("${") and ref.endswith("}"):
                name = ref[2:-1]
                if name in defaults:
                    refs.append(defaults[name])
                continue
            refs.append(ref)
        found[rel] = refs
    return found


def locked_refs() -> set[str]:
    return {entry["image_ref"] for entry in LOCK["images"]}


def base_image_offenders(froms: dict[str, list[str]], refs: set[str]) -> list[str]:
    """Every way a build base can escape the lock.

    Pure over text so the negative controls below exercise the same code path the
    real assertion uses -- a check that only ever sees the current files proves
    nothing about the failures it exists to catch.
    """
    offenders: list[str] = []
    for dockerfile, images in froms.items():
        for image in images:
            if "cap-" in image.split("/")[-1].split(":")[0]:
                continue  # internal CAP base: named per build, not per digest
            if "@sha256:" not in image:
                offenders.append(f"{dockerfile}: {image} is pinned by tag, not digest")
                continue
            if image not in refs:
                offenders.append(f"{dockerfile}: {image} is not in {LOCK_PATH.name}")
    return offenders


# -- GATE 3 / GATE 4 ----------------------------------------------------------


def test_every_external_dockerfile_base_is_locked_and_digest_pinned() -> None:
    offenders = base_image_offenders(dockerfile_froms(), locked_refs())
    assert not offenders, "; ".join(offenders)


def test_the_scan_sees_the_real_bases_not_an_empty_set() -> None:
    """A lock check over zero files passes forever, so pin the coverage."""
    froms = dockerfile_froms()
    assert {"backend/Dockerfile", "frontend/Dockerfile"} <= set(froms), sorted(froms)
    external = {
        image
        for images in froms.values()
        for image in images
        if "cap-" not in image.split("/")[-1]
    }
    assert external, f"no external base in {sorted(froms)} -- the scanner is broken"
    assert any("python:3.13-slim@sha256:" in image for image in external)
    assert any("nginx:1.30.4-alpine@sha256:" in image for image in external)


def test_no_dockerfile_layers_on_a_cap_image_by_mutable_name() -> None:
    """An internal base is a build argument, never a literal `cap-*:latest`."""
    for dockerfile in _dockerfiles():
        text = dockerfile.read_text(encoding="utf-8")
        for ref in _FROM.findall(text):
            last = ref.split("/")[-1]
            assert not last.startswith("cap-"), (
                f"{dockerfile.name}: hard-coded internal base {ref!r} is back; it "
                "must arrive as --build-arg so the release can name the exact image"
            )
            if ref.startswith("${"):
                name = ref[2:-1]
                assert not re.search(
                    r"^ARG\s+" + re.escape(name) + r"=", text, re.MULTILINE
                ), (
                    f"{dockerfile.name}: ARG {name} has a default, which is a "
                    ":latest with extra steps"
                )


def test_lock_base_entries_are_used_and_their_sites_agree() -> None:
    """A locked base nothing builds from is a stale record, not a safety net."""
    froms = dockerfile_froms()
    used = {image for images in froms.values() for image in images}
    bases = [e for e in LOCK["images"] if BASE_ROLE in e.get("role", "")]
    assert len(bases) >= 5, f"only {len(bases)} bases locked; F-20 expects every FROM"
    for entry in bases:
        assert entry["image_ref"] in used, (
            f"{entry['name']}: locked but no Dockerfile FROMs it"
        )
        # `referenced_by` lists every surface that names the coordinate, and a
        # certification recorder legitimately does that: build_images.sh writes the
        # base digest of the image it inspected. The claim this test guards is not
        # "a Dockerfile is the only file allowed to mention a base" -- it is that
        # something actually builds on it, and that every listed site agrees.
        builds_on_it = [site for site in entry.get("referenced_by", []) if site in froms]
        assert builds_on_it, (
            f"{entry['name']}: every site that names it stopped FROMing it"
        )
        for site in entry.get("referenced_by", []):
            assert entry["image_ref"] in (PROJECT_ROOT / site).read_text("utf-8"), (
                f"{site} and the lock disagree about {entry['name']}"
            )


def test_locked_base_digests_carry_their_provenance() -> None:
    """A digest with no source is a magic number nobody can re-derive."""
    for entry in LOCK["images"]:
        if BASE_ROLE not in entry.get("role", ""):
            continue
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", entry["digest"]), entry["name"]
        assert entry.get("manifest_media_type"), entry["name"]
        assert "linux/amd64" in entry.get("platforms", []), (
            f"{entry['name']}: the certification runners' platform is absent"
        )
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", entry.get("platform_digest_linux_amd64", "")), (
            f"{entry['name']}: no linux/amd64 child digest recorded"
        )
        provenance = entry.get("provenance") or {}
        assert provenance.get("method") and provenance.get("resolved_on"), entry["name"]
        assert provenance.get("evidence"), (
            f"{entry['name']}: provenance must point at the verification artifact"
        )
        expected = (
            f"{entry['repository'].split('/', 1)[1]}:{entry['tag']}@{entry['digest']}"
            if entry["registry"] == "docker.io"
            else (
                f"{entry['registry']}/{entry['repository']}"
                f":{entry['tag']}@{entry['digest']}"
            )
        )
        assert entry["image_ref"] == expected, (
            f"{entry['name']}: image_ref does not follow from its own fields"
        )


# -- negative controls --------------------------------------------------------


def test_the_check_rejects_a_tag_only_base() -> None:
    """F-20's exact defect, seen through the function that has to catch it."""
    assert base_image_offenders({"x/Dockerfile": ["python:3.13-slim"]}, locked_refs()) == [
        "x/Dockerfile: python:3.13-slim is pinned by tag, not digest"
    ]


def test_the_check_rejects_a_digest_that_is_not_locked() -> None:
    assert base_image_offenders(
        {"x/Dockerfile": ["example.invalid/base:1.0@sha256:" + "0" * 64]}, locked_refs()
    ), "an unlocked digest slipped past"


def test_the_check_accepts_the_locked_coordinate() -> None:
    """Otherwise every control above passes by being impossible to satisfy."""
    pinned = next(
        ref for ref in locked_refs() if ref.startswith("python:3.13-slim@sha256:")
    )
    assert base_image_offenders({"backend/Dockerfile": [pinned]}, locked_refs()) == []


def test_the_check_leaves_the_internal_base_to_the_release_graph() -> None:
    assert base_image_offenders({"x/Dockerfile": ["cap-sandbox-http:1.0"]}, locked_refs()) == []
