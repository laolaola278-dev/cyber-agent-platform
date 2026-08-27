"""Tests for scripts/release/classify_diff.py -- certification inheritance gate.

Contract (Release Closure directive Section 6):
  * a production runtime file change  -> runtime_affecting = True
  * a docs / report-only change       -> runtime_affecting = False
  * unknown files fail CLOSED (runtime_affecting = True)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "classify_diff", _HERE / "scripts" / "release" / "classify_diff.py"
)
_mod = importlib.util.module_from_spec(_SPEC)
# register BEFORE exec so @dataclass can resolve the module via sys.modules
sys.modules["classify_diff"] = _mod
_SPEC.loader.exec_module(_mod)

classify_path = _mod.classify_path
FileClassification = _mod.FileClassification
DiffReport = _mod.DiffReport
RUNTIME_CATEGORIES = _mod.RUNTIME_CATEGORIES
INHERITABLE_CATEGORIES = _mod.INHERITABLE_CATEGORIES
is_version_only_change = _mod.is_version_only_change
is_release_metadata_only_change = _mod.is_release_metadata_only_change
_is_version_field = _mod._is_version_field
_is_release_metadata_field = _mod._is_release_metadata_field


# -- path classification -----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "backend/app/acquisition/worker_path.py",
        "backend/app/worker/runtime.py",
        "backend/app/acquisition/claim.py",
        "frontend/src/App.tsx",
        "backend/app/models/db.py",
    ],
)
def test_production_runtime_is_runtime_affecting(path: str) -> None:
    category = classify_path(path)
    assert category in RUNTIME_CATEGORIES, f"{path} -> {category}"


@pytest.mark.parametrize(
    "path",
    [
        "backend/alembic/versions/20260812_0021_fencing.py",
        "deployment/helm/cap/templates/worker.yaml",
        "backend/Dockerfile",
        "backend/pyproject.toml",
    ],
)
def test_db_deployment_dependency_are_runtime_affecting(path: str) -> None:
    category = classify_path(path)
    assert category in RUNTIME_CATEGORIES, f"{path} -> {category}"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("docs/GA_CERTIFICATION_TASK_REPORT.md", "docs"),
        ("CHANGELOG.md", "docs"),
        ("backend/tests/test_phase_28_7_ga_certification.py", "test_harness"),
        (".github/workflows/cap-ga-certification.yml", "ci_workflow"),
        ("scripts/certification/generate_report_28_7.py", "certification_generator"),
        ("VERSION", "release_metadata"),
    ],
)
def test_non_runtime_categories(path: str, expected: str) -> None:
    assert classify_path(path) == expected


def test_unknown_file_fails_closed() -> None:
    # an unrecognized path must NOT be silently treated as docs
    assert classify_path("mystery/blob.bin") in RUNTIME_CATEGORIES


# -- report aggregation ------------------------------------------------------


def _report(paths: list[str]) -> DiffReport:
    report = DiffReport(certified_sha="a" * 40, candidate_sha="b" * 40)
    for path in paths:
        category = classify_path(path)
        report.files.append(
            FileClassification(
                path=path,
                category=category,
                runtime_affecting=category in RUNTIME_CATEGORIES,
            )
        )
    return report


def test_docs_only_diff_is_inheritable() -> None:
    report = _report(
        [
            "docs/GA_CERTIFICATION_TASK_REPORT.md",
            "backend/tests/test_phase_28_7_ga_certification.py",
            ".github/workflows/cap-ga-certification.yml",
            "scripts/certification/generate_report_28_7.py",
        ]
    )
    assert report.runtime_affecting is False
    assert report.release_metadata_only is True
    assert report.to_dict()["inheritance"] == "INHERITED"


def test_runtime_diff_blocks_inheritance() -> None:
    report = _report(
        [
            "docs/notes.md",
            "backend/app/acquisition/worker_path.py",
        ]
    )
    assert report.runtime_affecting is True
    assert report.release_metadata_only is False
    assert report.to_dict()["inheritance"] == "RECERTIFICATION_REQUIRED"


def test_empty_diff_is_inheritable() -> None:
    report = _report([])
    assert report.runtime_affecting is False
    assert report.release_metadata_only is True


# -- content-aware version-bump detection ------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'version = "1.0.0"',
        "version = '1.0.0-rc3'",
        '"version": "1.0.0",',
        "version: 1.0.0",
        'appVersion: "1.0.0"',
        '    tag: "1.0.0"',
        "ARG VERSION=1.0.0",
        '    app_version: str = "1.0.0"',
        '__version__ = "1.0.0"',
    ],
)
def test_version_field_lines_are_recognized(line: str) -> None:
    assert _is_version_field(line), line


@pytest.mark.parametrize(
    "line",
    [
        'dependencies = ["fastapi>=0.115"]',
        "    replicaCount: 3",
        "FROM python:3.13-slim",
        "def run_claimed(run_id):",
        "    pullPolicy: IfNotPresent",
        "",
    ],
)
def test_non_version_lines_are_rejected(line: str) -> None:
    assert not _is_version_field(line), line


def test_pure_version_bump_is_detected() -> None:
    removed = ['version = "1.0.0-rc3"']
    added = ['version = "1.0.0"']
    assert is_version_only_change(removed, added) is True


def test_pep440_version_bump_is_detected() -> None:
    # uv.lock uses PEP 440 (no hyphen): 1.0.0rc3 -> 1.0.0
    removed = ['version = "1.0.0rc3"']
    added = ['version = "1.0.0"']
    assert is_release_metadata_only_change(removed, added) is True


def test_multi_line_version_bump_is_detected() -> None:
    removed = [
        'version = "1.0.0-rc3"',
        'appVersion: "1.0.0-rc3"',
        '    tag: "1.0.0-rc3"',
    ]
    added = [
        'version = "1.0.0"',
        'appVersion: "1.0.0"',
        '    tag: "1.0.0"',
    ]
    assert is_version_only_change(removed, added) is True


def test_dependency_change_is_not_a_version_bump() -> None:
    removed = ['version = "1.0.0-rc3"', 'dependencies = ["fastapi>=0.115"]']
    added = ['version = "1.0.0"', 'dependencies = ["fastapi>=0.116"]']
    assert is_version_only_change(removed, added) is False


def test_logic_change_is_not_a_version_bump() -> None:
    removed = ['    replicaCount: 3']
    added = ['    replicaCount: 5']
    assert is_version_only_change(removed, added) is False


def test_reordered_only_lines_without_version_token_fail_closed() -> None:
    # identical normalized lines but no version token -> not a bump
    removed = ['version = "x"']
    added = ['version = "x"']
    assert is_version_only_change(removed, added) is False


def test_empty_change_is_not_a_version_bump() -> None:
    assert is_version_only_change([], []) is False


def test_version_bump_report_is_inheritable() -> None:
    report = DiffReport(certified_sha="a" * 40, candidate_sha="b" * 40)
    report.files.append(
        FileClassification(path="backend/pyproject.toml",
                           category="version_bump", runtime_affecting=False)
    )
    report.files.append(
        FileClassification(path="VERSION",
                           category="release_metadata", runtime_affecting=False)
    )
    assert report.runtime_affecting is False
    assert report.release_metadata_only is True
    assert report.to_dict()["inheritance"] == "INHERITED"
    assert "version_bump" in INHERITABLE_CATEGORIES


# -- release-metadata-only detection (prerelease annotation) -----------------


def test_prerelease_annotation_is_release_metadata_field() -> None:
    assert _is_release_metadata_field('  artifacthub.io/prerelease: "true"')
    assert _is_release_metadata_field('  artifacthub.io/prerelease: "false"')
    assert not _is_release_metadata_field("  replicaCount: 3")


def test_prerelease_flip_is_release_metadata_only() -> None:
    removed = ['  artifacthub.io/prerelease: "true"']
    added = ['  artifacthub.io/prerelease: "false"']
    assert is_release_metadata_only_change(removed, added) is True


def test_prerelease_noop_is_not_a_change() -> None:
    removed = ['  artifacthub.io/prerelease: "true"']
    added = ['  artifacthub.io/prerelease: "true"']
    assert is_release_metadata_only_change(removed, added) is False


def test_mixed_version_and_prerelease_is_release_metadata_only() -> None:
    removed = [
        'version: 1.0.0-rc3',
        'appVersion: "1.0.0-rc3"',
        '  artifacthub.io/prerelease: "true"',
    ]
    added = [
        'version: 1.0.0',
        'appVersion: "1.0.0"',
        '  artifacthub.io/prerelease: "false"',
    ]
    assert is_release_metadata_only_change(removed, added) is True


def test_prerelease_plus_logic_change_fails_closed() -> None:
    removed = [
        '  artifacthub.io/prerelease: "true"',
        "    replicaCount: 3",
    ]
    added = [
        '  artifacthub.io/prerelease: "false"',
        "    replicaCount: 5",
    ]
    assert is_release_metadata_only_change(removed, added) is False


def test_alias_is_version_only_change_matches() -> None:
    # backwards-compatible alias must behave identically
    assert is_version_only_change is is_release_metadata_only_change
