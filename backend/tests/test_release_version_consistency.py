"""Release version consistency gate (Release Closure directive Section 17).

The canonical version source is the root ``VERSION`` file. Every other version
carrier in the repository MUST agree with it. This test is version-agnostic: it
reads ``VERSION`` as the source of truth and asserts all downstream carriers
match, so it enforces consistency for ANY release (rc or GA) without edits.

Canonical version map (see outputs/release/cap-v1.0.0-version-map.json):

    VERSION                                        -> canonical (single source)
    backend/pyproject.toml      [project].version  -> must equal VERSION
    sdk/python/pyproject.toml   [project].version  -> must equal VERSION
    frontend/package.json       .version           -> must equal VERSION
    deployment/helm/cap/Chart.yaml  version        -> must equal VERSION
    deployment/helm/cap/Chart.yaml  appVersion     -> must equal VERSION
    deployment/helm/cap/values.yaml image.tag (x3) -> must equal VERSION
    backend/Dockerfile          ARG VERSION        -> must equal VERSION
    frontend/Dockerfile         ARG VERSION        -> must equal VERSION
    backend/app/config/settings.py app_version     -> must equal VERSION
    backend/app/__init__.py     __version__        -> must equal VERSION

A mismatch here is a RELEASE BLOCKER: shipping images/charts/packages whose
embedded version disagrees with the tag breaks upgrade, SBOM attribution, and
the release.yml validate-tag gate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _canonical_version() -> str:
    return (PROJECT_ROOT / "VERSION").read_text("utf-8").strip()


def _read(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text("utf-8")


# -- helpers to extract each carrier's version -------------------------------


def _pyproject_version(text: str) -> str:
    match = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    assert match, "no version field found in pyproject.toml"
    return match.group(1)


def _dockerfile_arg_version(text: str) -> str:
    match = re.search(r'^\s*ARG\s+VERSION\s*=\s*(\S+)', text, re.MULTILINE)
    assert match, "no ARG VERSION found in Dockerfile"
    return match.group(1)


def _settings_app_version(text: str) -> str:
    match = re.search(r'^\s*app_version\s*:\s*str\s*=\s*["\']([^"\']+)["\']',
                      text, re.MULTILINE)
    assert match, "no app_version field found in settings.py"
    return match.group(1)


def _init_dunder_version(text: str) -> str:
    match = re.search(r'^\s*__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    assert match, "no __version__ found in __init__.py"
    return match.group(1)


def _helm_values_image_tags(text: str) -> list[str]:
    return re.findall(r'^\s*tag\s*:\s*["\']?([^"\'\s]+)["\']?', text, re.MULTILINE)


def _uv_lock_root_version(text: str) -> str:
    """Extract the root package version from uv.lock (PEP 440 form)."""
    match = re.search(
        r'\[\[package\]\]\s*\nname = "cyber-agent-platform-backend"\s*\nversion = "([^"]+)"',
        text,
    )
    assert match, "root package entry not found in uv.lock"
    return match.group(1)


def _pep440(canonical: str) -> str:
    """Convert canonical SemVer to PEP 440 (1.0.0-rc3 -> 1.0.0rc3)."""
    return canonical.replace("-", "")


# -- the gate -----------------------------------------------------------------


def test_all_version_sources_match_canonical() -> None:
    expected = _canonical_version()
    assert expected, "VERSION file is empty"

    mismatches: list[str] = []

    def check(label: str, actual: str) -> None:
        if actual != expected:
            mismatches.append(f"{label}: {actual!r} != {expected!r}")

    # Python packaging
    check("backend/pyproject.toml", _pyproject_version(_read("backend/pyproject.toml")))
    check("sdk/python/pyproject.toml",
          _pyproject_version(_read("sdk/python/pyproject.toml")))

    # Frontend packaging
    frontend = json.loads(_read("frontend/package.json"))
    check("frontend/package.json", frontend.get("version", ""))

    # Helm chart
    chart = _read("deployment/helm/cap/Chart.yaml")
    chart_version = re.search(r'^version:\s*(\S+)', chart, re.MULTILINE)
    chart_appversion = re.search(r'^appVersion:\s*["\']?([^"\'\s]+)', chart, re.MULTILINE)
    check("Chart.yaml version", chart_version.group(1) if chart_version else "")
    check("Chart.yaml appVersion", chart_appversion.group(1) if chart_appversion else "")

    # Helm values image tags (backend api, backend worker, frontend)
    for i, tag in enumerate(_helm_values_image_tags(_read("deployment/helm/cap/values.yaml"))):
        check(f"values.yaml image.tag[{i}]", tag)

    # Dockerfiles
    check("backend/Dockerfile ARG VERSION", _dockerfile_arg_version(_read("backend/Dockerfile")))
    check("frontend/Dockerfile ARG VERSION",
          _dockerfile_arg_version(_read("frontend/Dockerfile")))

    # Runtime-reported version (health endpoint / OpenAPI)
    check("backend/app/config/settings.py app_version",
          _settings_app_version(_read("backend/app/config/settings.py")))

    # Python package dunder version
    check("backend/app/__init__.py __version__",
          _init_dunder_version(_read("backend/app/__init__.py")))

    # Lockfiles (Dockerfile runs 'uv sync --frozen' / 'npm ci', so lockfile
    # versions must track the manifests or the image build fails)
    uv_lock_actual = _uv_lock_root_version(_read("backend/uv.lock"))
    if uv_lock_actual != _pep440(expected):
        mismatches.append(
            f"backend/uv.lock root package (PEP 440): {uv_lock_actual!r} "
            f"!= {_pep440(expected)!r}"
        )
    lockfile = json.loads(_read("frontend/package-lock.json"))
    check("frontend/package-lock.json .version", lockfile.get("version", ""))
    check("frontend/package-lock.json packages[''].version",
          lockfile.get("packages", {}).get("", {}).get("version", ""))

    assert not mismatches, (
        "version sources disagree with canonical VERSION "
        f"({expected!r}):\n  " + "\n  ".join(mismatches)
    )


def test_canonical_version_is_valid_semver() -> None:
    version = _canonical_version()
    assert re.fullmatch(
        r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?', version
    ), f"canonical VERSION {version!r} is not valid SemVer"


def test_helm_values_has_exactly_three_image_tags() -> None:
    """Guard: the values.yaml contract is backend-api + backend-worker + frontend."""
    tags = _helm_values_image_tags(_read("deployment/helm/cap/values.yaml"))
    assert len(tags) == 3, f"expected 3 image tags, found {len(tags)}: {tags}"
