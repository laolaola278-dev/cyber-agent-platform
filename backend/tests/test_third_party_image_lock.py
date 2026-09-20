"""``deployment/third-party-images.json`` is the single source of truth for the
containers and binaries the deployment and certification surfaces pull.

Why this gate exists: the MinIO vendor archived its open-source server and
stopped serving community release files, and ``minio/minio`` disappeared from
Docker Hub between two certification runs. Nine reference sites across compose,
four workflows, a setup script and a test fixture named the old image, and the
failure only surfaced as ``pull access denied`` deep inside a certification job
that had otherwise been counted as passing evidence for a release line. A lock
file nobody checks rots the same way, so this module *is* the check: every
reference site must match the lock, the lock must not name an image nothing uses,
and the negative controls at the bottom prove the checker can fail -- in both
directions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = PROJECT_ROOT / "deployment" / "third-party-images.json"
LOCK = json.loads(LOCK_PATH.read_text("utf-8"))

#: ``repository:tag`` for the retired MinIO image, assembled from fragments so
#: this file can name the stale coordinate without tripping the scanner below
#: (``backend/tests`` is one of the surfaces it audits).
STALE_MINIO_REF = "/".join(["minio", "minio"]) + ":" + "RELEASE.2025-04-22T22-12-26Z"

#: The retired coordinate, as it would appear in a YAML/shell surface. Built from
#: ``STALE_MINIO_REF`` rather than written out, because a literal regex for the
#: retired repository-and-colon matches its own source line -- which would make
#: this file, one of the surfaces being audited, the offender it reports.
_STALE_PATTERN = re.compile(
    r"(?<!quay\.io/)(?<![\w./-])" + re.escape(STALE_MINIO_REF.split(":")[0]) + r":"
)

WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"


def _image(name: str) -> dict:
    for entry in LOCK["images"]:
        if entry["name"] == name:
            return entry
    raise AssertionError(f"{name} is not in {LOCK_PATH.name}")


def _drift(entry: dict, text: str, display: str) -> list[str]:
    """Every way one surface's text disagrees with a locked image entry.

    Pure function over text, which is what makes the negative controls below
    able to exercise the same code path the real assertion uses.
    """
    problems: list[str] = []
    if entry["image_ref"] not in text:
        problems.append(f"{display}: does not pull {entry['image_ref']}")
    if entry.get("digest") and _STALE_PATTERN.search(text):
        problems.append(f"{display}: names the retired docker.io MinIO image")
    if entry.get("digest") and re.search(
            re.escape(entry["image_ref"].split("@")[0]) + r":[A-Za-z0-9._-]+", text):
        problems.append(f"{display}: uses a mutable tag where the lock pins a digest")
    return problems


def _surface_files() -> list[Path]:
    """Live deployment and certification surfaces.

    Historical prose under ``outputs/`` keeps its original wording on purpose and
    is deliberately not scanned.
    """
    candidates = [
        PROJECT_ROOT / "docker-compose.yml",
        *sorted(WORKFLOWS.glob("*.yml")),
        *sorted((PROJECT_ROOT / "scripts").rglob("*.sh")),
        *sorted((PROJECT_ROOT / "deployment").rglob("*.yaml")),
        *sorted((PROJECT_ROOT / "backend" / "tests").glob("*.py")),
    ]
    files = [path for path in candidates if path.is_file()]
    assert files, "no surface files resolved -- the image scan would pass vacuously"
    return files


# -- the lock file itself -----------------------------------------------------


def test_lock_file_is_well_formed() -> None:
    assert LOCK["schema"] == "cap.third-party-images/v1"
    names = [entry["name"] for entry in LOCK["images"]]
    assert len(names) == len(set(names)), f"duplicate image entries: {names}"
    for entry in LOCK["images"]:
        referenced = entry.get("referenced_by", [])
        derived = entry.get("derived_by", [])
        assert referenced or derived, f"{entry['name']} is locked but unused"
        assert not (set(referenced) & set(derived)), (
            f"{entry['name']}: a site either hardcodes the coordinate or derives it "
            f"from this lock, never both: {sorted(set(referenced) & set(derived))}"
        )
        assert entry["registry"] and entry["repository"], entry
        # An image either carries a digest or the reason it does not.
        assert entry.get("digest") or entry.get("tag"), entry["name"]
        short_form = (
            entry["registry"] == "docker.io"
            and entry["repository"].startswith("library/")
            # A surface may add the digest to the short name -- that is exactly
            # what a pinned Dockerfile base looks like (`python:3.13-slim@sha256:…`),
            # and refusing it here would push people back to a mutable tag.
            and entry["image_ref"]
            == entry["repository"].split("/", 1)[1] + ":" + entry["tag"]
            + ("@" + entry["digest"] if entry.get("digest") else "")
        )
        assert entry["image_ref"].startswith(f"{entry['registry']}/") or short_form, (
            entry["image_ref"]
        )


def test_derived_sites_read_the_lock_instead_of_copying_it() -> None:
    """``derived_by`` files must resolve the coordinate at runtime.

    A fixture that pastes the digest cannot be distinguished from a stale copy,
    so the claim "this derives it" is only worth checking if it is checked.
    """
    checked = 0
    for entry in LOCK["images"]:
        for rel in entry.get("derived_by", []):
            path = PROJECT_ROOT / rel
            assert path.exists(), f"lock file references a missing site: {rel}"
            text = path.read_text("utf-8")
            assert "third-party-images.json" in text, f"{rel} does not read the lock"
            assert entry["name"] in text, f"{rel} does not select {entry['name']}"
            assert entry["image_ref"] not in text, (
                f"{rel} both hardcodes and derives {entry['name']}: delete the copy"
            )
            checked += 1
    assert checked, "no derived_by sites -- this check would pass vacuously"


def test_digest_pinned_image_uses_an_immutable_reference() -> None:
    entry = _image("minio-object-store")
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", entry["digest"]), entry["digest"]
    assert entry["image_ref"] == (
        f"{entry['registry']}/{entry['repository']}@{entry['digest']}"
    )


def test_locked_digest_is_the_manifest_list_not_a_platform_or_schema1_digest() -> None:
    """A per-architecture digest would break a non-amd64 runner silently.

    Quay's ``Docker-Content-Digest`` for a tag depends on the Accept header: a
    bare request returns a legacy schema1 conversion whose digest differs from
    the manifest list, so recording the wrong one is an easy, invisible mistake.
    """
    entry = _image("minio-object-store")
    assert "manifest-list" in entry["digest_kind"]
    assert entry["digest"] != (
        "sha256:92114c07a60d96f6d8bbdcb6fe770d3fea4be047e4bf0afb922ff529385f736a"
    ), "that is the schema1 conversion digest"
    assert entry["digest"] != (
        "sha256:3f97c5651cb6662b880c787a232b6b34fec8d8922e08d6617b25d241a21164bb"
    ), "that is the linux/amd64 platform manifest"


# -- reference sites agree with the lock -------------------------------------


def test_every_reference_site_matches_the_lock() -> None:
    problems: list[str] = []
    for name in ("minio-object-store", "postgres"):
        entry = _image(name)
        for rel in entry["referenced_by"]:
            path = PROJECT_ROOT / rel
            assert path.exists(), f"lock file references a missing site: {rel}"
            problems.extend(_drift(entry, path.read_text("utf-8"), rel))
    assert not problems, "\n".join(problems)


def test_no_surface_pulls_the_retired_docker_hub_minio() -> None:
    offenders = sorted(
        str(path.relative_to(PROJECT_ROOT))
        for path in _surface_files()
        if _STALE_PATTERN.search(path.read_text("utf-8", errors="replace"))
    )
    assert not offenders, (
        "live surfaces still reference the retired docker.io MinIO image "
        "(pull access denied from certification runners since 2026-09-13): "
        + ", ".join(offenders)
    )


def test_lock_records_the_retirement_with_evidence() -> None:
    """The 'why' is part of the contract, so keep it explicit in the lock.

    Prose here is the only place the retirement timeline survives once the fix
    has aged out of memory; a bare digest tells a future maintainer nothing.
    """
    entry = _image("minio-object-store")
    assert "pull access denied" in entry["previous_ref_status"].lower()
    assert entry["previous_ref"] == "docker.io/" + STALE_MINIO_REF
    assert entry["provenance"]["image_config_labels"]["vendor"] == "MinIO Inc <dev@min.io>"
    assert entry["provenance"]["open_gap"], "provenance must state what was NOT verified"


# -- the binary bootstrap contract -------------------------------------------


def _workflow_text() -> str:
    texts = [path.read_text("utf-8") for path in sorted(WORKFLOWS.glob("*.yml"))]
    assert texts, f"no workflows under {WORKFLOWS}"
    return "\n".join(texts)


def test_mc_bootstrap_is_pinned_verifiable_and_fatal_on_mismatch() -> None:
    mc = next(binary for binary in LOCK["binaries"] if binary["name"] == "mc")
    block = _workflow_text()
    assert re.fullmatch(r"[0-9a-f]{64}", mc["sha256"]), mc["sha256"]
    assert "mc_release=" in block, "mc must be installed from a pinned release tag"
    assert "sha256sum --check" in block, "mc install must verify the published sha256sum"
    assert "curl -fSsL" in block, "curl without -f turns a 410 Gone page into a binary"
    assert "FATAL: mc checksum" in block, "a checksum mismatch must stop the job"
    assert not re.search(r"curl[^|]*dl\.min\.io", block), (
        "dl.min.io serves HTTP 410 for community releases; it cannot back a bootstrap"
    )


def test_kind_bootstrap_fails_loudly() -> None:
    kind = next(binary for binary in LOCK["binaries"] if binary["name"] == "kind")
    assert kind["version"] in _workflow_text()
    checked = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line in path.read_text("utf-8").splitlines():
            if "curl" in line and "kind" in line:
                checked += 1
                assert "-f" in line, f"{path.name}: the kind download must use curl -f"
    assert checked, "no kind bootstrap line found -- this check would pass vacuously"


# -- negative controls --------------------------------------------------------

#: The three ways a maintainer most plausibly edits a MinIO reference.
DRIFTED_SURFACES = [
    "    image: quay.io/minio/minio:RELEASE.2099-01-01T00-00-00Z",
    f"    image: {STALE_MINIO_REF}",
    "    image: quay.io/minio/minio@sha256:" + "0" * 64,
]


@pytest.mark.parametrize("surface", DRIFTED_SURFACES)
def test_drift_checker_reports_a_moved_reference(surface: str) -> None:
    entry = _image("minio-object-store")
    assert _drift(entry, "minio:\n" + surface + "\n", "probe.yml"), (
        f"the checker accepted a surface the lock does not describe: {surface}"
    )


def test_drift_checker_accepts_the_locked_reference() -> None:
    """The other half of a negative control: it must not fail every input.

    A checker that always returns a problem makes the real assertion theatre.
    """
    entry = _image("minio-object-store")
    good = "    image: " + entry["image_ref"]
    assert _drift(entry, "minio:\n" + good + "\n", "probe.yml") == []


def test_stale_reference_pattern_is_precise() -> None:
    assert _STALE_PATTERN.search(f"image: {STALE_MINIO_REF}")
    assert not _STALE_PATTERN.search(
        "image: quay.io/minio/minio@sha256:" + "a" * 64
    ), "the pinned quay.io coordinate must not read as stale"
    assert not _STALE_PATTERN.search("pip install minio/minio-py"), (
        "a hyphenated sibling project is not the server image"
    )


def test_postgres_entry_is_tag_pinned_on_purpose() -> None:
    """Guard the exception: this gate must not quietly demand a digest for Postgres.

    Docker Hub is unreachable from the audit host, so no postgres digest could be
    resolved offline; the entry says so instead of pretending to be pinned.
    """
    entry = _image("postgres")
    assert entry["digest"] is None
    assert entry["image_ref"] == "postgres:" + entry["tag"]
    assert "digest" in json.dumps(LOCK["policy"]["postgres"]).lower()
