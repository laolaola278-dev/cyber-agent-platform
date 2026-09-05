"""Bump all CAP version carriers from 1.0.3 -> 1.0.4-rc1.

Idempotent: re-running is safe (a carrier already at 1.0.4-rc1 is skipped).

Why a new RC rather than inheriting the v1.0.3 GA certification: the console
completion refactor (9dbe60f, extracted the remaining 7 inline views into
pages/ so all 16 views are components; App.tsx 672 -> 157 lines) touches
``frontend/src/**``, which ``scripts/release/classify_diff.py`` classifies as
``production_runtime`` (fail-closed by design):

    python scripts/release/classify_diff.py 6a79c1e 9dbe60f
    -> runtime_affecting=true, RECERTIFICATION_REQUIRED

The v1.0.3 GA certification therefore CANNOT be inherited. The console
completion must earn its own certification at a fresh rc anchor, after which a
pure version-metadata commit promotes rc1 -> 1.0.4 (INHERITED).

Version-agnostic consistency test (test_release_version_consistency.py) reads
the root VERSION file as canonical and asserts every downstream carrier agrees,
so all carriers must move together. This script makes targeted, surgical edits:

  * package-lock.json: ONLY the root .version and packages[''].version are
    bumped. The delayed-stream dependency at ~line 2405 (also "1.0.0") must NOT
    be touched.
  * uv.lock: root package version uses PEP 440 form (1.0.4rc1, no hyphen).
  * Chart.yaml: also flips artifacthub prerelease annotation false -> true
    (the 1.0.3 promote commit set it back to "false").
  * test_phase_23_release_candidate.py: RC_VERSION constant tracks the release,
    and the release-notes list gains docs/releases/v1.0.4-rc1.md. That test
    asserts the notes FILE EXISTS, so docs/releases/v1.0.4-rc1.md must be
    committed alongside this bump.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root (script lives in scripts/release/)
OLD = "1.0.3"
NEW = "1.0.4-rc1"          # SemVer / canonical form
NEW_PEP440 = "1.0.4rc1"     # PEP 440 form (uv.lock)

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
    path.write_text(text.replace(old, new), "utf-8")
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
    root_pat = re.compile(r'^  "version": "1\.0\.3",$', re.MULTILINE)
    assert len(root_pat.findall(lock_text)) == 1, "root .version not uniquely found"
    lock_text = root_pat.sub(f'  "version": "{NEW}",', lock_text)
    pkg_pat = re.compile(
        r'("": \{\n      "name": "cyber-agent-platform-frontend",\n      "version": ")1\.0\.3(",)'
    )
    assert len(pkg_pat.findall(lock_text)) == 1, "packages[''].version not uniquely found"
    lock_text = pkg_pat.sub(lambda m: f"{m.group(1)}{NEW}{m.group(2)}", lock_text)
    lock.write_text(lock_text, "utf-8")
    changed.append(
        "frontend/package-lock.json: root + packages[''] -> 1.0.4-rc1 (delayed-stream untouched)"
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

# 7. Helm values.yaml: 3 image tags
edit("deployment/helm/cap/values.yaml", f'tag: "{OLD}"', f'tag: "{NEW}"', count=3)

# 8-9. Dockerfiles ARG VERSION
edit("backend/Dockerfile", f"ARG VERSION={OLD}", f"ARG VERSION={NEW}", count=1)
edit("frontend/Dockerfile", f"ARG VERSION={OLD}", f"ARG VERSION={NEW}", count=1)

# 10. settings.py app_version
edit("backend/app/config/settings.py", f'app_version: str = "{OLD}"', f'app_version: str = "{NEW}"', count=1)

# 11. __init__.py __version__
edit("backend/app/__init__.py", f'__version__ = "{OLD}"', f'__version__ = "{NEW}"', count=1)

# 12. uv.lock root package (PEP 440). Anchor on the package name block so we do
#     not touch any dependency that happens to be version 1.0.3.
uv = ROOT / "backend/uv.lock"
uv_text = uv.read_text("utf-8")
if NEW_PEP440 not in uv_text:
    uv_pat = re.compile(
        r'(\[\[package\]\]\nname = "cyber-agent-platform-backend"\nversion = ")1\.0\.3(")'
    )
    assert len(uv_pat.findall(uv_text)) == 1, "uv.lock root package not uniquely found"
    uv_text = uv_pat.sub(lambda m: f"{m.group(1)}{NEW_PEP440}{m.group(2)}", uv_text)
    uv.write_text(uv_text, "utf-8")
    changed.append(f"backend/uv.lock: root package -> {NEW_PEP440} (PEP 440)")
else:
    skipped.append("backend/uv.lock: already bumped")

# 13. test_phase_23 RC_VERSION constant + release-notes list
edit(
    "backend/tests/test_phase_23_release_candidate.py",
    f'RC_VERSION = "{OLD}"',
    f'RC_VERSION = "{NEW}"',
    count=1,
)
# GUARDED: every historical entry stays in the release-notes list forever, so
# testing for the presence of OLD would re-append the rc1 path on every re-run.
# Guard on the NEW path instead.
notes_rel = "backend/tests/test_phase_23_release_candidate.py"
if '        "docs/releases/v1.0.4-rc1.md",\n' not in (ROOT / notes_rel).read_text("utf-8"):
    edit(
        notes_rel,
        '        "docs/releases/v1.0.3.md",\n',
        '        "docs/releases/v1.0.3.md",\n        "docs/releases/v1.0.4-rc1.md",\n',
        count=1,
    )
else:
    skipped.append(f"{notes_rel}: rc1 notes path already listed")

print("Bumped all version carriers to", NEW)
for line in changed:
    print("  -", line)
for line in skipped:
    print("  ~", line)
