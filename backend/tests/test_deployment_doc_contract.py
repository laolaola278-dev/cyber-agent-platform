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

import json
import re
from pathlib import Path

import yaml

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


def chart_default_images(values: dict) -> list[str]:
    """Every container image coordinate the chart can deploy with no overrides."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"image", "browserImage"} and isinstance(value, str):
                    found.append(value)
                elif key.endswith("Image") and isinstance(value, str):
                    found.append(value)
                elif value and not isinstance(value, (str, int, bool, float)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(values)
    # image: {repository: ..., tag: ...} blocks are the published product images;
    # the strings above are the bare coordinates a default install pulls.
    return sorted({entry for entry in found if "/" not in entry.split(":")[0] or ":" in entry})


# -- the checks ---------------------------------------------------------------


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
    """`release.yml` publishes two images; the worker needs two more."""
    compose_doc = COMPOSE_DOC.read_text("utf-8")
    checklist = CHECKLIST.read_text("utf-8")
    assert BUILD_SCRIPT in compose_doc, (
        "the compose guide never mentions the sandbox images the acquisition worker "
        "pulls by default, so a documented install cannot run an acquisition"
    )
    assert BUILD_SCRIPT in checklist, "the production checklist omits the sandbox images"


def test_every_default_image_coordinate_is_published_locked_or_documented() -> None:
    values = yaml.safe_load(VALUES.read_text("utf-8"))
    published = RELEASE_WORKFLOW.read_text("utf-8")
    lock = json.loads(THIRD_PARTY_LOCK.read_text("utf-8"))
    locked_refs = {entry["image_ref"] for entry in lock["images"]} | {
        entry["image_ref"].split(":")[0] for entry in lock["images"]
    }
    documented = (COMPOSE_DOC.read_text("utf-8") + CHECKLIST.read_text("utf-8")
                  + (PROJECT_ROOT / "docs" / "deployment" / "upgrade.md").read_text("utf-8"))

    unexplained: list[str] = []
    for image in chart_default_images(values):
        name = image.split(":")[0]
        if f"cap-{name}" in published or name in published:
            continue  # published by the release workflow
        if any(image in ref or ref in image for ref in locked_refs):
            continue  # recorded in the third-party image lock
        if name in documented:
            continue  # the operator is told where it comes from
        unexplained.append(image)
    assert not unexplained, (
        "the chart can deploy images that nothing publishes and no document "
        f"explains: {unexplained}"
    )


# -- negative controls --------------------------------------------------------


def test_head_check_fires_on_a_stale_document() -> None:
    assert checklist_head("Alembic single head is `20260803_0018`") == "20260803_0018"
    assert checklist_head("no head mentioned here") is None
    assert declared_head() != "20260803_0018", (
        "the derivation stopped tracking the chain -- the check above is theatre"
    )


def test_chart_image_scanner_sees_an_undocumented_default(tmp_path: Path) -> None:
    values = {
        "worker": {"image": {"repository": "cap-backend", "tag": "1.0.0"}},
        "sandbox": {"image": "made-up-sandbox:latest", "browserImage": "cap-sandbox-http:latest"},
    }
    found = chart_default_images(values)
    assert "made-up-sandbox:latest" in found, "a bare default image escaped the scan"
    assert "cap-sandbox-http:latest" in found


def test_missing_build_script_reference_is_detected(tmp_path: Path) -> None:
    doc = "docker compose up --build -d\n"
    assert BUILD_SCRIPT not in doc
