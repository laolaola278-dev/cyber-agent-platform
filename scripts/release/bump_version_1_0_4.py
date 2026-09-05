"""Promote all CAP version carriers from 1.0.4-rc1 -> 1.0.4 (GA).

This is the pure version-metadata commit that follows the v1.0.4-rc1
certification anchor (87d2409). It carries NO code change, so
``scripts/release/classify_diff.py`` must report:

    release_metadata_only = true
    INHERITED            (the rc1 certification carries over)

Idempotent: re-running is safe (a carrier already at 1.0.4 is skipped).

Differences vs the rc1 bump:
  * Chart.yaml flips the artifacthub prerelease annotation back to "false"
    (rc1 set it to "true").
  * test_phase_23 gains docs/releases/v1.0.4.md in the release-notes list.
  * uv.lock root package goes from the PEP 440 form 1.0.4rc1 -> 1.0.4.

GUARDS MUST BE ANCHORED, NOT SUBSTRING TESTS. Promoting to a bare x.y.z is
riskier than bumping to an rc: real dependencies legitimately carry versions
like "1.0.4" at 6-space indentation, so a substring guard such as
``if NEW not in lock_text`` reports "already bumped" and SILENTLY SKIPS the
real root bump. Every guard below therefore anchors on the exact root carrier
(root .version at exactly two spaces; the uv.lock package block).

See bump_version_1_0_3.py / bump_version_1_0_3_rc1.py for why
package-lock.json and uv.lock need targeted edits (delayed-stream /
dependency false positives).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root (script lives in scripts/release/)
OLD = "1.0.4-rc1"
NEW = "1.0.4"              # SemVer / canonical form
OLD_PEP440 = "1.0.4rc1"     # PEP 440 form currently in uv.lock
NEW_PEP440 = "1.0.4"

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
# ANCHORED guard: exactly-two-space indent + end of line matches only the root
# entry, never a dependency that happens to be version 1.0.4.
if len(re.findall(rf'^  "version": "{re.escape(NEW)}",$', lock_text, re.MULTILINE)) == 0:
    root_pat = re.compile(rf'^  "version": "{re.escape(OLD)}",$', re.MULTILINE)
    assert len(root_pat.findall(lock_text)) == 1, "root .version not uniquely found"
    lock_text = root_pat.sub(f'  "version": "{NEW}",', lock_text)
    pkg_pat = re.compile(
        r'("": \{\n      "name": "cyber-agent-platform-frontend",\n      "version": ")'
        + re.escape(OLD) + r'(",)'
    )
    assert len(pkg_pat.findall(lock_text)) == 1, "packages[''].version not uniquely found"
    lock_text = pkg_pat.sub(lambda m: f"{m.group(1)}{NEW}{m.group(2)}", lock_text)
    lock.write_text(lock_text, "utf-8")
    changed.append(
        f"frontend/package-lock.json: root + packages[''] -> {NEW} (delayed-stream untouched)"
    )
else:
    skipped.append("frontend/package-lock.json: already bumped")

# 6. Helm Chart.yaml: version, appVersion, prerelease annotation back to false
edit("deployment/helm/cap/Chart.yaml", f"version: {OLD}", f"version: {NEW}", count=1)
edit("deployment/helm/cap/Chart.yaml", f'appVersion: "{OLD}"', f'appVersion: "{NEW}"', count=1)
edit(
    "deployment/helm/cap/Chart.yaml",
    'artifacthub.io/prerelease: "true"',
    'artifacthub.io/prerelease: "false"',
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
#     not touch any dependency that happens to share the version string.
uv = ROOT / "backend/uv.lock"
uv_text = uv.read_text("utf-8")
if len(re.findall(
    r'\[\[package\]\]\nname = "cyber-agent-platform-backend"\nversion = "'
    + re.escape(NEW_PEP440) + r'"',
    uv_text,
)) == 0:
    uv_pat = re.compile(
        r'(\[\[package\]\]\nname = "cyber-agent-platform-backend"\nversion = ")'
        + re.escape(OLD_PEP440) + r'(")'
    )
    assert len(uv_pat.findall(uv_text)) == 1, "uv.lock root package not uniquely found"
    uv_text = uv_pat.sub(lambda m: f"{m.group(1)}{NEW_PEP440}{m.group(2)}", uv_text)
    uv.write_text(uv_text, "utf-8")
    changed.append(f"backend/uv.lock: root package -> {NEW_PEP440} (PEP 440)")
else:
    skipped.append("backend/uv.lock: already bumped")

# 13. test_phase_23 RC_VERSION constant + GA release notes path
edit(
    "backend/tests/test_phase_23_release_candidate.py",
    f'RC_VERSION = "{OLD}"',
    f'RC_VERSION = "{NEW}"',
    count=1,
)
# GUARDED: the rc1 entry stays in the release-notes list forever, so testing
# for the presence of OLD would re-append the GA path on every re-run. Guard on
# the NEW path instead.
notes_rel = "backend/tests/test_phase_23_release_candidate.py"
if '        "docs/releases/v1.0.4.md",\n' not in (ROOT / notes_rel).read_text("utf-8"):
    edit(
        notes_rel,
        '        "docs/releases/v1.0.4-rc1.md",\n',
        '        "docs/releases/v1.0.4-rc1.md",\n        "docs/releases/v1.0.4.md",\n',
        count=1,
    )
else:
    skipped.append(f"{notes_rel}: GA notes path already listed")

print(f"Bumped all version carriers to {NEW} (GA)")
for line in changed:
    print("  -", line)
for line in skipped:
    print("  ~", line)
