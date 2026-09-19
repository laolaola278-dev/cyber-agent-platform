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
        ("scripts/quality/scan_secrets.py", "repo_tooling"),
        ("VERSION", "release_metadata"),
    ],
)
def test_non_runtime_categories(path: str, expected: str) -> None:
    assert classify_path(path) == expected


def test_unknown_file_fails_closed() -> None:
    # an unrecognized path must NOT be silently treated as docs
    assert classify_path("mystery/blob.bin") in RUNTIME_CATEGORIES


# -- the criterion behind `repo_tooling` --------------------------------------

REPO_ROOT = _HERE
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"


def _scripts_reaches_a_container(
    root: Path,
    builds: list[str],
    contexts: dict[str, list[str]],
    dockerfiles: dict[str, str],
    compose: str,
    manifests: dict[str, str],
) -> list[str]:
    """Every way ``scripts/`` could end up inside a running container.

    ``builds`` are the docker build command lines CI actually runs, ``contexts``
    maps each build context to its top-level entries, ``dockerfiles``/``compose``
    /``manifests`` are file contents keyed for reporting.
    """
    problems: list[str] = []
    root_scripts = (root / "scripts").resolve()
    for command in builds:
        context = command.split()[-1].rstrip("\\").rstrip("/") or "."
        for entry in contexts.get(context, ()):
            # identity, not name: backend/scripts is its own tree and belongs
            # inside that image context
            if (root / context / entry).resolve() == root_scripts:
                problems.append(f"build context {context!r} contains the root scripts/")
    for name, text in dockerfiles.items():
        for line in text.splitlines():
            parts = line.split()
            if parts and parts[0] in ("COPY", "ADD") and any(
                token.removeprefix("./").startswith("scripts") for token in parts[1:]
            ):
                problems.append(f"{name}: {line.strip()}")
    for line in compose.splitlines():
        if "scripts" in line and not line.lstrip().startswith("#"):
            problems.append(f"docker-compose: {line.strip()}")
    for name, text in manifests.items():
        for line in text.splitlines():
            if "scripts" in line and not line.lstrip().startswith("#"):
                problems.append(f"{name}: {line.strip()}")
    return problems


def test_repo_tooling_reaches_no_shipped_artifact() -> None:
    """Why a scripts/quality change may inherit certification.

    ``scripts/quality/*`` is repository-side tooling: the secret scanner and the
    coverage-matrix asserter run in CI and on a developer machine, and no
    deployment ever executes them. That is the whole justification for the
    ``repo_tooling`` category -- and a category justified by a comment is a
    category that goes stale, so the claim is checked here against the files that
    would actually carry it into a container. The moment this reports anything,
    ``scripts/quality/`` must go back to the classifier's fail-closed default:
    a tooling change that runs inside the product is a product change.
    """
    builds = [
        line.strip()
        for path in sorted(WORKFLOW_DIR.glob("*.yml"))
        for line in path.read_text("utf-8").splitlines()
        if line.strip().startswith("docker build")
    ]
    assert builds, "no docker build commands found -- scanner is reading nothing"
    contexts: dict[str, list[str]] = {}
    for command in builds:
        context = command.split()[-1].rstrip("/")
        directory = REPO_ROOT / context
        assert directory.is_dir(), f"build context {context} does not exist"
        contexts[context] = [entry.name for entry in directory.iterdir()]
    dockerfiles = {
        "backend/Dockerfile": (REPO_ROOT / "backend" / "Dockerfile").read_text("utf-8"),
        "backend/docker/egress-proxy/Dockerfile": (
            REPO_ROOT / "backend" / "docker" / "egress-proxy" / "Dockerfile"
        ).read_text("utf-8"),
    }
    compose = (REPO_ROOT / "docker-compose.yml").read_text("utf-8")
    manifests = {
        str(path.relative_to(REPO_ROOT)): path.read_text("utf-8")
        for path in (REPO_ROOT / "deployment" / "helm" / "cap").rglob("*.yaml")
    }
    assert manifests, "no chart manifests found -- scanner is reading nothing"

    problems = _scripts_reaches_a_container(
        REPO_ROOT, builds, contexts, dockerfiles, compose, manifests
    )
    assert not problems, f"scripts/ reaches a container, repo_tooling must fail closed: {problems}"


def test_the_container_reach_scan_is_not_vacuous() -> None:
    """Planted evidence: each route into a container must be reported."""
    builds = ["docker build -t x -f backend/Dockerfile backend/"]
    base_kwargs = {
        "root": REPO_ROOT,
        "builds": builds,
        "contexts": {"backend": ["app", "Dockerfile"]},
        "dockerfiles": {"backend/Dockerfile": "COPY app ./app\n"},
        "compose": "  volumes:\n    - ./data:/data\n",
        "manifests": {"worker.yaml": "  - name: data\n"},
    }
    assert not _scripts_reaches_a_container(**base_kwargs), "clean repo must report nothing"

    by_context = _scripts_reaches_a_container(
        **{
            **base_kwargs,
            "builds": ["docker build -t x -f Dockerfile ."],
            "contexts": {".": ["app", "scripts"]},
        }
    )
    assert by_context and "build context" in by_context[0]
    # a context's OWN scripts tree is a different directory and stays clean
    assert not _scripts_reaches_a_container(
        **{**base_kwargs, "contexts": {"backend": ["app", "scripts"]}}
    )

    by_copy = _scripts_reaches_a_container(
        **{**base_kwargs, "dockerfiles": {"backend/Dockerfile": "COPY scripts /s\n"}}
    )
    assert by_copy and "backend/Dockerfile" in by_copy[0]

    by_compose = _scripts_reaches_a_container(
        **{**base_kwargs, "compose": "    - ./scripts:/opt/scripts\n"}
    )
    assert by_compose and "docker-compose" in by_compose[0]

    by_manifest = _scripts_reaches_a_container(
        **{**base_kwargs, "manifests": {"worker.yaml": "    path: /repo/scripts\n"}}
    )
    assert by_manifest and "worker.yaml" in by_manifest[0]


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


# -- git decoding (Windows platform-encoding regression) ----------------------


class _Completed:
    def __init__(self, stdout: bytes) -> None:
        self.stdout = stdout
        self.stderr = b""
        self.returncode = 0


def test_git_output_is_decoded_as_utf8_not_the_platform_encoding(monkeypatch) -> None:
    """A diff with a non-ASCII byte must classify, not crash, on any platform.

    The classifier used ``text=True``, so on a GBK-default Windows console the
    reader thread raised UnicodeDecodeError, ``stdout`` came back as None and
    ``build_report`` died with an AttributeError -- hit for real on 2026-09-19,
    the first time this tool ran against a Windows working tree.
    """
    seen: dict = {}
    payload = (
        b"--- a/backend/app/config/settings.py\n"
        b"+++ b/backend/app/config/settings.py\n"
        b'-    app_version: str = "1.0.5"\n'
        b'+    app_version: str = "1.0.6-rc1"  # \xe2\x80\x94 anchor\n'
    )

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return _Completed(payload)

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)
    out = _mod._git(["git", "diff", "a..b", "--", "backend/app/config/settings.py"])

    assert "\u2014" in out, "UTF-8 bytes must decode regardless of the console codepage"
    assert "text" not in seen, "text=True decodes with the platform encoding"
    assert "encoding" not in seen, "decoding must stay explicit and in-thread"


def test_undecodable_git_output_fails_loudly(monkeypatch) -> None:
    """Never degrade to "no changed lines" -- that would inherit a certification."""
    monkeypatch.setattr(
        _mod.subprocess, "run", lambda args, **kwargs: _Completed(b"\xff\xfenot utf-8")
    )
    with pytest.raises(SystemExit) as raised:
        _mod._git(["git", "diff", "--name-only", "a..b"])
    assert "UTF-8" in str(raised.value)


def test_diff_helpers_route_through_the_decoder(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(_mod, "_git", lambda args: calls.append(args) or "a.py\n")
    assert _mod.git_changed_files("a", "b") == ["a.py"]
    assert _mod.git_file_diff("a", "b", "a.py") == "a.py\n"
    assert calls == [
        ["git", "diff", "--name-only", "a..b"],
        ["git", "diff", "a..b", "--", "a.py"],
    ]


# -- env-template version carrier (.env.example APP_VERSION) ------------------


def test_env_template_version_line_is_a_version_field() -> None:
    """APP_VERSION=1.0.6-rc1 is the same kind of carrier as ARG VERSION=...

    Since 1a55df0 the compose file requires APP_VERSION and it overrides
    settings.app_version at runtime, so the consistency gate demands
    APP_VERSION == VERSION. The classifier has to recognise the line or every
    RC bump reports RECERTIFICATION_REQUIRED for a commit with no behaviour in
    it -- which is what happened to the 1.0.6-rc1 anchor.
    """
    removed = ["APP_VERSION=1.0.5"]
    added = ["APP_VERSION=1.0.6-rc1"]
    assert _is_version_field(added[0])
    assert is_release_metadata_only_change(removed, added)


def test_env_template_change_beyond_the_literal_still_blocks_inheritance() -> None:
    """Adding the pattern must not let a real config edit through."""
    removed = ["APP_VERSION=1.0.5", "REDIS_URL=redis://localhost:6379/0"]
    added = [
        "APP_VERSION=1.0.6-rc1",
        "REDIS_URL=redis://attacker.example:6379/0",
    ]
    assert not is_release_metadata_only_change(removed, added)


def test_env_template_indirection_is_not_mistaken_for_a_bump() -> None:
    """A substituted value is not a version literal, so it stays runtime work."""
    assert not _is_version_field("APP_VERSION=$(git describe)")
    assert not _is_version_field("APP_VERSION=")
