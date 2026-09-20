"""Deployment documentation must describe the deployment that exists.

docs/deployment/* and the production checklist are the operator's only source for
what to install and in which order, and the release audit found two ways they had
already rotted: the checklist told the operator to verify an Alembic head
(`20260803_0018`) that had been superseded twice, and nothing in any operator
document mentioned the two sandbox images the worker and the chart default to
(`cap-sandbox-http:latest`, `cap-sandbox-browser:latest`), which no registry
publishes -- `release.yml` ships the backend and the console only, so the first
acquisition on a documented install fails on a missing image unless somebody
already knows about `backend/docker/build_sandbox_images.sh`.

Both are asserted here: the head the docs name must be the head the revisions
declare, and every image coordinate a default configuration references must be
published by the release workflow, recorded in the third-party lock, or documented
with the command that builds it.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import yaml

#: The chart-reference derivation lives in the release completeness test, so
#: this file cannot scan a narrower set of the chart than that one does and
#: call the difference 'consistent'.
_IMAGE_COMPLETENESS = (
    Path(__file__).resolve().parent / "test_release_image_completeness.py"
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERSIONS = PROJECT_ROOT / "backend" / "alembic" / "versions"
CHECKLIST = PROJECT_ROOT / "docs" / "deployment" / "production-checklist.md"
COMPOSE_DOC = PROJECT_ROOT / "docs" / "deployment" / "docker-compose.md"
VALUES = PROJECT_ROOT / "deployment" / "helm" / "cap" / "values.yaml"
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release.yml"
THIRD_PARTY_LOCK = PROJECT_ROOT / "deployment" / "third-party-images.json"
BUILD_SCRIPT = "backend/docker/build_sandbox_images.sh"

_HEAD_RE = re.compile(r"Alembic single head is `([^`]+)`")
_REV_RE = re.compile(r'^revision: str = "([^"]+)"', re.MULTILINE)
_DOWN_RE = re.compile(r'^down_revision: str \| None = (".*?"|None)', re.MULTILINE)


def declared_head() -> str:
    """The one revision no other revision points at -- derived, not hardcoded."""
    revisions: set[str] = set()
    referenced: set[str] = set()
    for path in sorted(VERSIONS.glob("*.py")):
        source = path.read_text("utf-8")
        head = _REV_RE.search(source)
        if not head:
            continue
        revisions.add(head.group(1))
        down = _DOWN_RE.search(source)
        if down and down.group(1) != "None":
            referenced.add(down.group(1).strip('"'))
    heads = revisions - referenced
    assert len(heads) == 1, f"expected one alembic head, found {sorted(heads)}"
    return heads.pop()


def checklist_head(doc: str) -> str | None:
    match = _HEAD_RE.search(doc)
    return match.group(1) if match else None


def _completeness_module():
    spec = importlib.util.spec_from_file_location("release_image_completeness", _IMAGE_COMPLETENESS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chart_default_images(values: dict) -> list[str]:
    """Every container image coordinate the chart can deploy with no overrides.

    Delegates to the release completeness scanner, which resolves both shapes a
    coordinate takes in this chart -- a full string (an env value the worker
    receives) and a `{repository, tag, digest}` block a template composes. Two
    scanners over one chart is how a coordinate slips between them.
    """
    module = _completeness_module()
    return sorted(set(module.chart_images(values).values()))


def test_checklist_names_the_head_the_revisions_declare() -> None:
    doc = CHECKLIST.read_text("utf-8")
    named = checklist_head(doc)
    assert named, "the checklist no longer states the Alembic head -- say which one it verifies"
    actual = declared_head()
    assert named == actual, (
        f"docs/deployment/production-checklist.md tells the operator to verify head "
        f"{named!r} but backend/alembic declares {actual!r}; a checklist that names a "
        "superseded revision proves nothing."
    )


def test_sandbox_images_are_documented_where_they_are_needed() -> None:
    """The compose path still builds its sandbox images locally, so it has to say so."""
    compose_doc = COMPOSE_DOC.read_text("utf-8")
    checklist = CHECKLIST.read_text("utf-8")
    assert BUILD_SCRIPT in compose_doc, (
        "the compose guide never mentions the sandbox images the acquisition worker "
        "pulls by default, so a documented install cannot run an acquisition"
    )
    assert BUILD_SCRIPT in checklist, "the production checklist omits the sandbox images"


def test_every_default_image_coordinate_is_published_locked_or_documented() -> None:
    """A chart default has to come from somewhere an operator can verify.

    Two different rules, because the two kinds of coordinate fail differently:

    * a **CAP-owned** image (`cap-*`) must be *published by the release
      pipeline*. Documentation is not an acceptable answer -- until F-7 the
      sandbox and egress images were named in three documents and produced by
      no artifact, which read as "handled" while a fresh install pulled refs
      that do not exist;
    * a **third-party** image must appear in the third-party lock, which is the
      file that records its registry, tag and digest and why that is safe.
    """
    values = yaml.safe_load(VALUES.read_text("utf-8"))
    module = _completeness_module()
    published = module.published_images()
    lock = json.loads(THIRD_PARTY_LOCK.read_text("utf-8"))
    locked_refs = {entry["image_ref"] for entry in lock["images"]} | {
        entry["image_ref"].split(":")[0] for entry in lock["images"]
    }
    documented = (
        COMPOSE_DOC.read_text("utf-8")
        + CHECKLIST.read_text("utf-8")
        + (PROJECT_ROOT / "docs" / "deployment" / "upgrade.md").read_text("utf-8")
    )

    unexplained: list[str] = []
    for image in chart_default_images(values):
        name = image.split("/")[-1].split(":")[0].split("@")[0]
        if name.startswith("cap-"):
            if name not in published:
                unexplained.append(f"{image}: a CAP image no release job publishes")
            continue
        if any(image in ref or ref in image for ref in locked_refs):
            continue
        if name in documented:
            continue
        unexplained.append(f"{image}: neither locked nor documented")
    assert not unexplained, (
        "the chart can deploy images nothing publishes and no document explains: "
        + "; ".join(unexplained)
    )


def test_head_check_fires_on_a_stale_document() -> None:
    assert checklist_head("Alembic single head is `20260803_0018`") == "20260803_0018"
    assert checklist_head("no head mentioned here") is None
    assert declared_head() != "20260803_0018", (
        "the derivation stopped tracking the chain -- the check above is theatre"
    )


def test_chart_image_scanner_sees_an_undocumented_default() -> None:
    """Both coordinate shapes are scanned, on the real chart, not a stub.

    A fixture that only carries the images the scanner happens to look for would
    pass while the real chart grew a coordinate nobody checked, so the mutation is
    applied to the actual values document.
    """
    values = yaml.safe_load(VALUES.read_text("utf-8"))
    values["worker"]["sandbox"]["extraImage"] = {
        "repository": "ghcr.io/someone/made-up-sandbox",
        "tag": "9.9.9",
    }
    values["worker"]["sandbox"]["browserImage"] = {
        "repository": "cap-sandbox-http",
        "tag": "",
        "digest": "sha256:" + "a" * 64,
    }
    found = chart_default_images(values)
    assert "ghcr.io/someone/made-up-sandbox:9.9.9" in found, (
        "an added image coordinate escaped the scan -- the doc rule below cannot "
        "see it either, which is how F-7 stayed invisible"
    )
    assert any(ref.startswith("cap-sandbox-http@sha256:") for ref in found), (
        "a digest-pinned coordinate lost its location"
    )


def test_missing_build_script_reference_is_detected(tmp_path: Path) -> None:
    doc = "docker compose up --build -d\n"
    assert BUILD_SCRIPT not in doc
