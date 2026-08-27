#!/usr/bin/env python3
"""Release diff classifier -- certification inheritance gate.

Compares a runtime-certified SHA against a release-candidate SHA and
classifies every changed file. The core question it answers:

    Does the release candidate change any PRODUCTION RUNTIME code that was
    NOT part of the certified commit?

If every changed file is docs / test / CI / report-generator / release
metadata / pure version bump / artifact staging, runtime certification is
INHERITED from the certified SHA. If any production runtime logic, deployment
topology, database schema, or dependency-runtime file changed, RECERTIFICATION
is required.

CONTENT-AWARE VERSION-BUMP DETECTION
------------------------------------
A file that lives in a normally runtime-affecting location (pyproject.toml,
package.json, Chart.yaml, values.yaml, Dockerfile, backend/app/config/
settings.py, backend/app/__init__.py) is reclassified as a non-blocking
``version_bump`` IFF every changed line is a recognized version field and the
only textual delta between the removed and added lines is the version literal
itself. Anything else -- a new dependency, a changed probe, an altered ARG, an
added env var -- keeps the fail-closed path category and blocks inheritance.

Usage:
    python scripts/release/classify_diff.py <certified_sha> <candidate_sha>

Exit codes:
    0  runtime_affecting=false  (certification inheritable)
    2  runtime_affecting=true   (recertification required)
    3  usage / git error
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Classification rules. Order matters: first match wins. Each rule is a
# (category, list-of-path-prefixes-or-substrings). A file is RUNTIME-AFFECTING
# only if it lands in one of the RUNTIME_CATEGORIES.
# ---------------------------------------------------------------------------

RULES: list[tuple[str, tuple[str, ...]]] = [
    # -- docs / reports -----------------------------------------------------
    ("docs", ("docs/", "README", "CHANGELOG", "LICENSE", ".md")),
    # -- certification report generator + release scripts -------------------
    ("certification_generator", ("scripts/certification/", "scripts/release/")),
    # -- CI workflows -------------------------------------------------------
    ("ci_workflow", (".github/workflows/", ".github/")),
    # -- test harness -------------------------------------------------------
    ("test_harness", ("tests/", "test_", "_test.py", "conftest.py", "pytest.ini", "tox.ini")),
    # -- release metadata (version files, changelog, release notes) ---------
    ("release_metadata", ("release-notes", "release_notes")),
    # -- dependency manifests (lockfiles / manifests -- reviewed separately)-
    ("dependency", ("requirements", "pyproject.toml", "package.json", "package-lock.json",
                    "poetry.lock", "Pipfile", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock")),
    # -- deployment (Helm / k8s manifests / Dockerfiles / compose) ----------
    ("deployment", ("deployment/", "helm/", "k8s/", "kubernetes/", "Dockerfile",
                    "docker-compose", "compose.yml", "compose.yaml", ".dockerignore")),
    # -- database / migrations / schema -------------------------------------
    ("database", ("migrations/", "alembic/", "schema", ".sql")),
    # -- production runtime (backend app, frontend src, sandbox, worker) ----
    ("production_runtime", ("backend/app/", "frontend/src/", "app/", "src/",
                            "worker", "sandbox", "acquisition")),
]

# Categories that BLOCK certification inheritance.
RUNTIME_CATEGORIES = {"production_runtime", "database", "deployment", "dependency"}

# Categories that are always safe for inheritance (never runtime-affecting).
INHERITABLE_CATEGORIES = {
    "docs", "certification_generator", "ci_workflow", "test_harness",
    "release_metadata", "version_bump",
}

# ---------------------------------------------------------------------------
# Release-metadata line recognition. Two families:
#
#   VERSION_FIELD_PATTERNS -- a version carrier. The only permitted delta is
#     the version literal itself (checked via normalization + token presence).
#
#   RELEASE_METADATA_FIELD_PATTERNS -- a pure release-metadata field that may
#     legitimately change value without touching runtime behavior (e.g. the
#     artifacthub.io/prerelease annotation flipped "true" -> "false" at GA).
#     These are exempt from the version-token requirement.
#
# Both families are deliberately narrow: we would rather fail closed (block
# inheritance) than misread a logic change as a cosmetic release edit.
# ---------------------------------------------------------------------------

VERSION_FIELD_PATTERNS: tuple[re.Pattern[str], ...] = (
    # pyproject.toml:  version = "1.0.0"
    re.compile(r'^\s*version\s*=\s*["\']'),
    # package.json / package-lock.json:  "version": "1.0.0"
    re.compile(r'^\s*"version"\s*:\s*["\']'),
    # Helm Chart.yaml:  version: 1.0.0  /  appVersion: "1.0.0"
    re.compile(r'^\s*(app)?version\s*:\s*', re.IGNORECASE),
    # Helm values.yaml image tag:  tag: "1.0.0"
    re.compile(r'^\s*tag\s*:\s*'),
    # Dockerfile:  ARG VERSION=1.0.0
    re.compile(r'^\s*ARG\s+VERSION\s*='),
    # backend/app/config/settings.py:  app_version: str = "1.0.0"
    re.compile(r'^\s*app_version\s*(:\s*str\s*)?=\s*'),
    # backend/app/__init__.py:  __version__ = "1.0.0"
    re.compile(r'^\s*__version__\s*=\s*'),
)

RELEASE_METADATA_FIELD_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Helm Chart.yaml annotation:  artifacthub.io/prerelease: "false"
    # (pure release metadata -- GA flips this from "true" to "false")
    re.compile(r'^\s*artifacthub\.io/prerelease\s*:\s*'),
)

# A SemVer / PEP 440 version token: 1.0.0, 1.0.0-rc3, 1.0.0rc3, 1.0.0a1,
# 1.0.0b2, 1.0.0.post1, 1.0.0.dev0, 1.0.0+build.5
_VERSION_TOKEN = re.compile(
    r'\b\d+\.\d+\.\d+'
    r'(?:[-+][0-9A-Za-z.-]+|\.(?:post|dev)\d+|(?:rc|a|b|alpha|beta)\d*)?'
    r'\b'
)
_VERSION_PLACEHOLDER = "<VER>"


def _is_version_field(line: str) -> bool:
    """True if a changed line is a recognized version-field assignment."""
    if not line.strip():
        return False
    return any(pattern.match(line) for pattern in VERSION_FIELD_PATTERNS)


def _is_release_metadata_field(line: str) -> bool:
    """True if a changed line is a pure release-metadata field (no version)."""
    if not line.strip():
        return False
    return any(pattern.match(line) for pattern in RELEASE_METADATA_FIELD_PATTERNS)


def _normalize_version(line: str) -> str:
    """Replace every version literal with a placeholder for comparison."""
    return _VERSION_TOKEN.sub(_VERSION_PLACEHOLDER, line)


def is_release_metadata_only_change(removed: list[str], added: list[str]) -> bool:
    """True iff the ONLY delta is release metadata (version bumps and/or
    pure release-metadata fields such as the prerelease annotation).

    Fail-closed. Returns False unless EVERY changed line is a recognized
    version field or release-metadata field. Version fields must differ only
    in the version literal (normalized sets identical + a version token is
    actually present, guarding against pure reorderings). Release-metadata
    fields are exempt from the token requirement but must represent a real
    change (removed != added).
    """
    if not removed and not added:
        return False
    all_lines = removed + added
    if not all(
        _is_version_field(line) or _is_release_metadata_field(line)
        for line in all_lines
    ):
        return False

    ver_removed = [l for l in removed if _is_version_field(l)]
    ver_added = [l for l in added if _is_version_field(l)]

    if ver_removed or ver_added:
        # Version carriers: only the version literal may differ.
        if sorted(_normalize_version(l) for l in ver_removed) != sorted(
            _normalize_version(l) for l in ver_added
        ):
            return False
        return any(
            _VERSION_TOKEN.search(l) for l in ver_removed + ver_added
        )

    # Only release-metadata fields changed -- require an actual change.
    return sorted(removed) != sorted(added)


# Backwards-compatible alias (tests + older callers).
is_version_only_change = is_release_metadata_only_change


def _changed_lines(diff_text: str) -> tuple[list[str], list[str]]:
    """Extract (removed, added) content lines from a unified diff."""
    removed: list[str] = []
    added: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    return removed, added


@dataclass
class FileClassification:
    path: str
    category: str
    runtime_affecting: bool


@dataclass
class DiffReport:
    certified_sha: str
    candidate_sha: str
    files: list[FileClassification] = field(default_factory=list)

    @property
    def runtime_affecting(self) -> bool:
        return any(f.runtime_affecting for f in self.files)

    @property
    def release_metadata_only(self) -> bool:
        return all(f.category in INHERITABLE_CATEGORIES for f in self.files)

    def to_dict(self) -> dict:
        return {
            "certified_sha": self.certified_sha,
            "candidate_sha": self.candidate_sha,
            "runtime_affecting": self.runtime_affecting,
            "release_metadata_only": self.release_metadata_only,
            "inheritance": (
                "INHERITED" if not self.runtime_affecting else "RECERTIFICATION_REQUIRED"
            ),
            "files": [
                {"path": f.path, "category": f.category,
                 "runtime_affecting": f.runtime_affecting}
                for f in self.files
            ],
        }


def classify_path(path: str) -> str:
    """Return the path-based category for a single changed file."""
    # Exact-basename release metadata: the canonical VERSION file. Matched by
    # exact basename (not substring) so paths like alembic/versions/*.py are
    # NOT misclassified as release metadata.
    basename = path.rsplit("/", 1)[-1]
    if basename == "VERSION":
        return "release_metadata"
    lowered = path.lower()
    for category, needles in RULES:
        for needle in needles:
            if needle.lower() in lowered:
                return category
    # Default: unknown files are treated as runtime-affecting (fail-closed).
    return "production_runtime"


def git_changed_files(certified: str, candidate: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{certified}..{candidate}"],
        capture_output=True, text=True, check=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def git_file_diff(certified: str, candidate: str, path: str) -> str:
    out = subprocess.run(
        ["git", "diff", f"{certified}..{candidate}", "--", path],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def classify_file(certified: str, candidate: str, path: str) -> FileClassification:
    """Classify one changed file, applying content-aware release-metadata detection."""
    path_category = classify_path(path)
    if path_category in RUNTIME_CATEGORIES:
        # Possibly a pure release-metadata edit (version bump / prerelease
        # annotation) -- inspect the actual diff content.
        diff_text = git_file_diff(certified, candidate, path)
        removed, added = _changed_lines(diff_text)
        if is_release_metadata_only_change(removed, added):
            return FileClassification(path, "version_bump", runtime_affecting=False)
    return FileClassification(
        path, path_category, runtime_affecting=path_category in RUNTIME_CATEGORIES
    )


def build_report(certified: str, candidate: str) -> DiffReport:
    report = DiffReport(certified_sha=certified, candidate_sha=candidate)
    for path in git_changed_files(certified, candidate):
        report.files.append(classify_file(certified, candidate, path))
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 3
    certified, candidate = argv[1], argv[2]
    try:
        report = build_report(certified, candidate)
    except subprocess.CalledProcessError as error:
        print(f"git error: {error.stderr}", file=sys.stderr)
        return 3

    print(json.dumps(report.to_dict(), indent=2))
    if report.runtime_affecting:
        print("\nRESULT: RECERTIFICATION REQUIRED (runtime-affecting change found)",
              file=sys.stderr)
        return 2
    print("\nRESULT: runtime certification INHERITED "
          f"(release_metadata_only={report.release_metadata_only})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
