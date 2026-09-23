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

    # Deployment env template. APP_VERSION overrides settings.py's app_version at
    # runtime, so a stale value makes a whole deployment self-report the wrong
    # version through /health -- which is what the console now displays.
    env_match = re.search(r"^APP_VERSION=(\S+)", _read(".env.example"), re.MULTILINE)
    check(".env.example APP_VERSION", env_match.group(1) if env_match else "")

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


def test_helm_values_tag_every_image_coordinate_they_declare() -> None:
    """Guard: one explicit tag per `{repository, tag, digest}` block in the chart.

    This used to assert "exactly three tags" (api, worker, frontend). F-7 made the
    two sandbox images and the egress proxy release coordinates as well, so a
    hardcoded count would be edited by whoever adds the next one. The count is
    derived from the chart's own `repository:` lines instead, and every block must
    state a tag: the carrier assertion above only sees version *literals*, so an
    image coordinate whose tag went missing would not be checked at all.
    """
    text = _read("deployment/helm/cap/values.yaml")
    repositories = re.findall(r"^\s*repository:\s*\S+", text, re.MULTILINE)
    tags = _helm_values_image_tags(text)
    assert len(repositories) >= 6, (
        f"the chart declares {len(repositories)} image coordinates; since F-7 it must "
        "declare at least backend, worker, frontend, sandbox-http, sandbox-browser "
        "and egress-proxy"
    )
    assert len(tags) == len(repositories), (
        f"{len(repositories)} image coordinates but {len(tags)} tags: one coordinate "
        "carries no version for the carrier check to compare"
    )


# -- guard: the build/packaging layer must not carry its own version ----------

#: The 16 carriers above are declared explicitly, so they are checked for
#: *equality*. This guard covers the opposite failure mode: a version literal
#: in a layer that is supposed to resolve the version at build time. `.github/
#: workflows/ci.yml` shipped `build-args: VERSION=1.0.0-rc1` and survived five
#: releases because no carrier check looked at CI, mislabelling every CI-built
#: image and the provenance/SBOM attribution derived from it.
#: Any line whose variable name ends in VERSION and which resolves to a bare
#: semantic-version literal -- assigned directly (`build-args: VERSION=1.0.0-rc1`)
#: or as a Compose interpolation default (`APP_VERSION: ${APP_VERSION:-1.0.0-rc1}`,
#: optionally closed by `}`). The Compose form matters because APP_VERSION
#: overrides `settings.app_version` at runtime, so a stale default makes a
#: deployment self-report the wrong version. Substitutions without a literal
#: (`${APP_VERSION}`, `${APP_VERSION:?required}`) are not matched.
#:
#: There is deliberately no end-of-line anchor here. The rule used to carry `\s*$`,
#: which made a literal invisible to the guard as soon as the line continued -- and
#: `.github/workflows/ci.yml` carried exactly such a line (`--build-arg
#: VERSION=0.0.0-…` followed by ` \`) through five observation runs while the guard
#: that exists to stop CI stating a version reported nothing. A rule a backslash can
#: step around is not the rule that was described.
_VERSION_LITERAL = re.compile(
    r"""VERSION\S*\s*[:=]\s*(?:\$\{[A-Z_]+:-)?(?P<quote>['\"]?)"""
    r"""(?P<value>\d+\.\d+\.\d+[0-9A-Za-z.+-]*)(?P=quote)\}?"""
)


def version_literal_on(line: str) -> bool:
    """Whether one line states a version the build layer should have derived.

    Comment-only lines are excluded: a comment mentioning a version reports history, and what
    this guard exists to catch is a value that reaches a builder. A trailing comment on a
    command line is still scanned, because the command part is what runs.
    """
    if line.lstrip().startswith("#"):
        return False
    return bool(_VERSION_LITERAL.search(line))


def _deriving_sources() -> list[Path]:
    """Build/packaging files that must derive the version rather than state it.

    Resolved from PROJECT_ROOT (never the CWD) so the scan cannot silently
    collect an empty set when pytest runs from `backend/`.
    """
    workflows = sorted((PROJECT_ROOT / ".github" / "workflows").glob("*.yml"))
    compose = PROJECT_ROOT / "docker-compose.yml"
    return [*workflows, *([compose] if compose.exists() else [])]


def test_no_hardcoded_version_literal_in_build_or_packaging() -> None:
    """Build/packaging layers resolve VERSION from the canonical file, never a literal.

    A `VERSION=<literal>` build-arg is a latent provenance defect: it is correct
    only on the day it is written. Acceptable forms are a context reference
    (`VERSION=` with no value, resolved from an env/step output) or an explicit
    `${{ ... }}` / `${VAR}` substitution.
    """
    sources = _deriving_sources()
    assert sources, (
        f"no build/packaging files found under {PROJECT_ROOT / '.github' / 'workflows'} "
        "-- the version-literal guard would pass vacuously"
    )

    offenders: list[str] = []
    for path in sources:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for lineno, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if version_literal_on(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "hardcoded VERSION literal(s) in a layer that must derive the canonical "
        "version (read it from the VERSION file / a step output instead):\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_reads_a_continued_line_as_well_as_a_terminated_one() -> None:
    r"""The control that keeps the anchor's removal from being a claim.

    The old pattern ended in `\s*$`, so `--build-arg VERSION=0.0.0-x \` -- a literal,
    continued -- was not an offender, and the guard passed over the line it was written for.
    Asserting the two forms side by side is what makes "we removed the anchor" mean something:
    both are caught now, and a derived substitution is still not.
    """
    caught = [
        "          build-args: VERSION=1.0.0-rc1",                     # the original defect
        '          APP_VERSION: ${APP_VERSION:-1.0.0-rc1}',            # the Compose default
        "                  --build-arg VERSION=0.0.0-producer-observation \\",  # continued
        '              args+=(--build-arg "VERSION=2.5.1" --push)',    # mid-line
    ]
    for line in caught:
        assert version_literal_on(line), f"the guard still misses: {line}"
    derived = [
        '                  --build-arg "VERSION=${OBSERVE_VERSION}"',
        "        version: ${{ needs.validate-tag.outputs.version }}",
        "          APP_VERSION: ${APP_VERSION:?required}",
        "          # a CI VERSION=1.0.0-rc1 once mislabelled every CI-built image",
    ]
    for line in derived:
        assert not version_literal_on(line), f"the guard flags something that derives it: {line}"
