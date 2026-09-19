"""Bump all CAP version carriers from 1.0.5 -> 1.0.6-rc1.

Idempotent: re-running is safe (a carrier already at 1.0.6-rc1 is skipped).

Why a new RC rather than inheriting the v1.0.5 GA certification: v1.0.5 is
already published (tag `v1.0.5` -> `32ec298`, GitHub Release "Cyber Agent
Platform v1.0.5", 2026-09-07) and published release contents are immutable, so
the 15 commits of post-release work on this line cannot be shipped as 1.0.5.
``scripts/release/classify_diff.py`` -- fail-closed by design -- classifies the
delta as requiring re-certification:

    python scripts/release/classify_diff.py 32ec298 a5ce0c4
    -> 64 files, runtime_affecting=true, RECERTIFICATION_REQUIRED
       production_runtime 48 | test_harness 6 | docs 5 | dependency 2
       | ci_workflow 1 | database 1 | deployment 1

Runtime-affecting changes on this line (see docs/releases/v1.0.6-rc1.md):
  * ``backend/app/repositories/asset.py`` -- asset search crashed with a 500 on
    PostgreSQL (``SELECT DISTINCT`` over json columns has no equality operator).
  * ``backend/app/main.py`` / ``config/settings.py`` -- ``CAP_ZAP_API_KEY`` was
    documented but never consumed, so a fresh deployment could not create an
    incident at all (SECRET_NOT_FOUND through AssessmentService).
  * ``backend/app/workflow/{nodes,service}.py`` + ``api/routes/workflow.py`` --
    the APPROVAL node stops being a Phase 3 placeholder that parks forever:
    a reviewer decision now opens or terminates the run.
  * ``backend/app/middleware/authorization.py`` -- the decision route is mapped
    to ``approval.decide`` instead of falling through to ``platform.manage``.
  * ``backend/app/api/errors.py``, ``api/routes/assets.py`` -- audit rows carry
    the authenticated principal instead of "api-user".
  * ``frontend/src/**`` -- Approval Center / Plugin inventory completed, the
    platform's own error text surfaced, unnamed action columns and a no-op
    assignment submission fixed.
  * ``docker-compose.yml`` + ``.env.example`` -- APP_VERSION is now a required
    carrier instead of a stale literal, so a deployment cannot self-report an
    five-releases-old version.
  * ``scripts/release/classify_diff.py`` -- decoded git output with the platform
    encoding, which crashes the certification gate on a GBK Windows console.

No ``backend/alembic/versions`` file differs from v1.0.5, so the *schema* is
unchanged; the runtime, RBAC and console behaviour are not, and this anchor
re-certifies from scratch.

Version-agnostic consistency test (test_release_version_consistency.py) reads
the root VERSION file as canonical and asserts every downstream carrier agrees,
so all carriers must move together. This script makes targeted edits:

  * package-lock.json: ONLY the root .version and packages[''].version.
  * uv.lock: root package version uses PEP 440 form (1.0.6rc1, no hyphen).
  * Chart.yaml: also flips the artifacthub prerelease annotation false -> true.
  * .env.example: APP_VERSION is a carrier since 1a55df0 (it overrides
    settings.app_version at runtime), so a stale value mislabels a deployment.
  * test_phase_23_release_candidate.py: RC_VERSION tracks the release and the
    release-notes list gains docs/releases/v1.0.6-rc1.md -- that test asserts
    the file exists, so it must be committed alongside this bump.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root (script lives in scripts/release/)
OLD = "1.0.5"
NEW = "1.0.6-rc1"          # SemVer / canonical form
NEW_PEP440 = "1.0.6rc1"    # PEP 440 form (uv.lock)

changed: list[str] = []
skipped: list[str] = []


def edit(rel: str, old: str, new: str, *, count: int | None = None) -> None:
    path = ROOT / rel
    text = path.read_text("utf-8")
    occurrences = text.count(old)
    if occurrences == 0 and new in text:
        skipped.append(f"{rel}: already at {new}")
        return
    if count is not None and occurrences != count:
        raise SystemExit(
            f"ABORT: {rel}: expected {count} occurrence(s) of {old!r}, found {occurrences}"
        )
    if occurrences == 0:
        raise SystemExit(f"ABORT: {rel}: {old!r} not found (and {new!r} not present)")
    # newline="\n": write_text would otherwise translate to os.linesep and
    # convert a whole LF file to CRLF on Windows, burying the bump in churn.
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    changed.append(f"{rel}: {old!r} -> {new!r} ({occurrences}x)")


# 1. canonical VERSION
edit("VERSION", OLD, NEW, count=1)

# 2-3. Python packaging manifests
edit("backend/pyproject.toml", f'version = "{OLD}"', f'version = "{NEW}"', count=1)
edit("sdk/python/pyproject.toml", f'version = "{OLD}"', f'version = "{NEW}"', count=1)

# 4. frontend package.json (root version only; deps use ranges, not exact)
edit("frontend/package.json", f'"version": "{OLD}"', f'"version": "{NEW}"', count=1)

# 5. frontend package-lock.json -- root .version and packages[''].version ONLY.
lock = ROOT / "frontend/package-lock.json"
lock_text = lock.read_text("utf-8")
if NEW not in lock_text:
    root_pat = re.compile(r'^  "version": "1\.0\.5",$', re.MULTILINE)
    assert len(root_pat.findall(lock_text)) == 1, "root .version not uniquely found"
    lock_text = root_pat.sub(f'  "version": "{NEW}",', lock_text)
    pkg_pat = re.compile(
        r'("": \{\n      "name": "cyber-agent-platform-frontend",\n      "version": ")1\.0\.5(",)'
    )
    assert len(pkg_pat.findall(lock_text)) == 1, "packages[''].version not uniquely found"
    lock_text = pkg_pat.sub(lambda m: f"{m.group(1)}{NEW}{m.group(2)}", lock_text)
    lock.write_text(lock_text, encoding="utf-8", newline="\n")
    changed.append(
        f"frontend/package-lock.json: root + packages[''] -> {NEW} (dependencies untouched)"
    )
else:
    skipped.append("frontend/package-lock.json: already bumped")

# 6. Helm Chart.yaml: version, appVersion, prerelease annotation
edit("deployment/helm/cap/Chart.yaml", f"version: {OLD}", f"version: {NEW}", count=1)
edit("deployment/helm/cap/Chart.yaml", f'appVersion: "{OLD}"', f'appVersion: "{NEW}"', count=1)
edit(
    "deployment/helm/cap/Chart.yaml",
    'artifacthub.io/prerelease: "false"',
    'artifacthub.io/prerelease: "true"',
    count=1,
)

# 7. Helm values.yaml: 3 image tags (backend api, backend worker, frontend)
edit("deployment/helm/cap/values.yaml", f'tag: "{OLD}"', f'tag: "{NEW}"', count=3)

# 8-9. Dockerfiles ARG VERSION
edit("backend/Dockerfile", f"ARG VERSION={OLD}", f"ARG VERSION={NEW}", count=1)
edit("frontend/Dockerfile", f"ARG VERSION={OLD}", f"ARG VERSION={NEW}", count=1)

# 10-11. Runtime-reported version (health endpoint / OpenAPI) and package dunder
edit(
    "backend/app/config/settings.py",
    f'app_version: str = "{OLD}"',
    f'app_version: str = "{NEW}"',
    count=1,
)
edit("backend/app/__init__.py", f'__version__ = "{OLD}"', f'__version__ = "{NEW}"', count=1)

# 12. .env.example APP_VERSION -- a carrier since 1a55df0: compose passes it into
#     the containers, where it overrides settings.app_version at runtime.
edit(".env.example", f"APP_VERSION={OLD}", f"APP_VERSION={NEW}", count=1)

# 13. uv.lock root package (PEP 440). Anchored on the package name block so no
#     dependency that happens to be version 1.0.5 is touched.
uv = ROOT / "backend/uv.lock"
uv_text = uv.read_text("utf-8")
if NEW_PEP440 not in uv_text:
    uv_pat = re.compile(
        r'(\[\[package\]\]\nname = "cyber-agent-platform-backend"\nversion = ")1\.0\.5(")'
    )
    assert len(uv_pat.findall(uv_text)) == 1, "uv.lock root package not uniquely found"
    uv_text = uv_pat.sub(lambda m: f"{m.group(1)}{NEW_PEP440}{m.group(2)}", uv_text)
    uv.write_text(uv_text, encoding="utf-8", newline="\n")
    changed.append(f"backend/uv.lock: root package -> {NEW_PEP440} (PEP 440)")
else:
    skipped.append("backend/uv.lock: already bumped")

# 14. test_phase_23 RC_VERSION constant + release-notes list
edit(
    "backend/tests/test_phase_23_release_candidate.py",
    f'RC_VERSION = "{OLD}"',
    f'RC_VERSION = "{NEW}"',
    count=1,
)
# GUARDED on the NEW path: every historical entry stays in the list forever, so
# testing for OLD would re-append this path on every re-run.
notes_rel = "backend/tests/test_phase_23_release_candidate.py"
notes_path = f'        "docs/releases/v{NEW}.md",\n'
if notes_path not in (ROOT / notes_rel).read_text("utf-8"):
    edit(
        notes_rel,
        f'        "docs/releases/v{OLD}.md",\n',
        f'        "docs/releases/v{OLD}.md",\n{notes_path}',
        count=1,
    )
else:
    skipped.append(f"{notes_rel}: rc notes path already listed")

print("Bumped all version carriers to", NEW)
for line in changed:
    print("  -", line)
for line in skipped:
    print("  ~", line)
