"""``deployment/third-party-images.json`` is the single source of truth for the
containers and binaries the deployment and certification surfaces pull.

Why this gate exists: the MinIO vendor archived its open-source server and
stopped serving community release files, and ``minio/minio`` disappeared from
Docker Hub between two certification runs. Nine reference sites across compose,
five workflows, a setup script and a test fixture named the old image, and the
failure only surfaced as ``pull access denied`` deep inside a certification job
that had otherwise been counted as passing evidence for a release line. A lock
file nobody checks rots the same way, so this module *is* the check, in both
directions: a site that disagrees with the lock fails, a site the lock does not
name fails, an entry the lock names that nothing pulls fails, and a compose
service that pulls a mutable tag fails (F-24 -- a tag is a pointer somebody
else moves). Every locked digest is generated rather than transcribed, and the
file it was generated into is cited by name, tracked, and compared
digest-for-digest (F-37). The negative controls at the bottom prove each of
those can fail -- a checker nobody has seen report is theatre.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = PROJECT_ROOT / "deployment" / "third-party-images.json"
LOCK = json.loads(LOCK_PATH.read_text("utf-8"))
COMPOSE_PATH = PROJECT_ROOT / "docker-compose.yml"

#: The tracked artifact `scripts/release/third_party_registry_evidence.py`
#: writes, and the only tree a cited evidence path may live in. Evidence under
#: ``outputs/`` is generated paperwork that a fresh clone does not contain, so a
#: pointer there vouches for nothing; Batch 2 closed F-37 by replacing those
#: pointers with this one and deleting the twin-matching allowance that let the
#: untracked form pass.
EVIDENCE_ARTIFACT = "docs/quality/artifacts/registry-resolution/third-party-registries.json"
EVIDENCE_ROOTS = ("docs/quality/artifacts/",)

#: A locked digest is an assertion about an index, not about one architecture.
INDEX_KIND_TOKENS = ("manifest-list", "index")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")

#: ``repository:tag`` for the retired MinIO image, assembled from fragments so
#: this file can name the stale coordinate without tripping the scanner below
#: (``backend/tests`` is one of the surfaces it audits).
STALE_MINIO_REF = "/".join(["minio", "minio"]) + ":" + "RELEASE.2025-04-22T22-12-26Z"

#: The retired coordinate, as it would appear in a YAML/shell surface. Built from
#: ``STALE_MINIO_REF`` rather than written out, because a literal regex for the
#: retired repository-and-colon matches its own source line -- which would make
#: this file, one of the surfaces being audited, the offender it reports.
_STALE_PATTERN = re.compile(
    r"(?<!quay\.io/)(?<![\w./-])" + re.escape(STALE_MINIO_REF.split(":")[0]) + r":"
)

WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"


def _image(name: str) -> dict:
    for entry in LOCK["images"]:
        if entry["name"] == name:
            return entry
    raise AssertionError(f"{name} is not in {LOCK_PATH.name}")


def _coordinate(entry: dict) -> tuple[str, str, str]:
    return (entry["registry"], entry["repository"], entry["tag"])


#: Where an image reference can actually sit: a YAML `image:` value, a
#: Dockerfile `FROM`, or a buildkit `# syntax=` directive. A substring search is
#: not precise enough -- `redis://redis:6379/0` and `…@postgres:5432/cap` are
#: URLs whose host happens to be the name of a pinned image, and reading them as
#: mutable tags would have the checker cry foul on correct files.
_REFERENCE_POSITION = re.compile(
    r"(?:^\s*(?:-\s+)?image:\s*|^\s*FROM\s+(?:--\S+\s+)*|^\s*#\s*syntax\s*=\s*)"
    r"(?P<value>[^#\n]+)",
    re.M,
)


def _image_token(value: str) -> str:
    """The image inside a value position, or "" when the value has none.

    `FROM --platform=$BUILDPLATFORM python:3.13 AS build` names its image in the
    first non-flag word, and `image: postgres:${{ matrix.postgres }}` spreads one
    reference across four words because Actions templates contain spaces -- so
    the template is rejoined before the split, and stays attached to the name it
    qualifies rather than becoming a bare `postgres:`.
    """
    joined = re.sub(r"\s+", " ", value)
    joined = re.sub(r"\$\{\{ ", "${{", joined)
    joined = re.sub(r" \}\}", "}}", joined)
    for token in joined.split():
        if token.startswith("--") or token.upper() == "AS":
            continue
        return token
    return ""


def _reference_values(text: str) -> list[str]:
    return [token for token in map(_image_token, _REFERENCE_POSITION.findall(text)) if token]


#: The axes a workflow declares for its matrix jobs. ``postgres-version-matrix``
#: exists to run the migration chain across several PostgreSQL versions, so its
#: `image: postgres:${{ matrix.postgres }}` is a set on purpose; naming the axis
#: is what makes that reviewable instead of a mutable tag wearing a template.
_MATRIX_AXIS = re.compile(r"^\s+(\w+):\s*\[[^\]]*\]", re.M)
_MATRIX_INTERPOLATION = re.compile(r"\$\{\{\s*matrix\.(\w+)\s*\}\}")


def _matrix_axes(text: str) -> set[str]:
    return set(_MATRIX_AXIS.findall(text))


def _drift(entry: dict, text: str, display: str) -> list[str]:
    """Every way one surface's text disagrees with a locked image entry.

    Pure function over text, which is what makes the negative controls below
    able to exercise the same code path the real assertion uses.
    """
    problems: list[str] = []
    if entry["image_ref"] not in text:
        problems.append(f"{display}: does not pull {entry['image_ref']}")
    if entry.get("digest") and _STALE_PATTERN.search(text):
        problems.append(f"{display}: names the retired docker.io MinIO image")
    if entry.get("digest"):
        names = _reference_names(entry)
        axes = _matrix_axes(text)
        for ref in _reference_values(text):
            head = ref.split("@", 1)[0]
            if not any(head == name or head.startswith(f"{name}:") for name in names):
                continue
            if "@sha256:" in ref:
                continue  # digest-carrying, whether or not a tag sits beside it
            axis = _MATRIX_INTERPOLATION.search(ref)
            if axis and axis.group(1) in axes:
                continue  # a declared version matrix: the range is the contract
            problems.append(
                f"{display}: pulls {ref}, a mutable reference where the lock pins "
                f"{entry['digest']}"
                + (f" (axis {axis.group(1)!r} is not declared in this file)" if axis else "")
            )
    return problems


def _surface_files() -> list[Path]:
    """Live deployment and certification surfaces.

    Historical prose under ``outputs/`` keeps its original wording on purpose and
    is deliberately not scanned.
    """
    candidates = [
        COMPOSE_PATH,
        *sorted(WORKFLOWS.glob("*.yml")),
        *sorted((PROJECT_ROOT / "scripts").rglob("*.sh")),
        *sorted((PROJECT_ROOT / "deployment").rglob("*.yaml")),
        *sorted((PROJECT_ROOT / "backend" / "tests").glob("*.py")),
        # A Dockerfile is a pull surface like any other: the bases it FROMs are
        # locked, and omitting them here would make "the lock lists a site that no
        # longer pulls it" fire on every base entry.
        *[p for p in sorted(PROJECT_ROOT.rglob("Dockerfile")) if "_tmp" not in p.parts],
    ]
    files = [path for path in candidates if path.is_file()]
    assert files, "no surface files resolved -- the image scan would pass vacuously"
    return files


def _admissible_refs(entry: dict) -> set[str]:
    """Every spelling a surface may legitimately carry for this coordinate.

    The registry host is optional exactly where Docker makes it optional (the
    Hub), and ``library/`` is optional on top of that. A tag beside a digest is
    admissible here because Dockerfile bases have always carried one so a reader
    can see the version line; the compose contract separately forbids it.
    """
    registry, repository = entry["registry"], entry["repository"]
    tag, digest = entry.get("tag"), entry.get("digest")
    names = {repository} if registry == "docker.io" else {f"{registry}/{repository}"}
    if registry == "docker.io" and repository.startswith("library/"):
        names.add(repository.split("/", 1)[1])
    refs: set[str] = set()
    for name in names:
        if tag:
            refs.add(f"{name}:{tag}")
        if digest:
            refs.add(f"{name}@{digest}")
            if tag:
                refs.add(f"{name}:{tag}@{digest}")
    return refs


def _reference_names(entry: dict) -> set[str]:
    """The repository strings a surface could use for this coordinate."""
    registry, repository = entry["registry"], entry["repository"]
    names = {repository} if registry == "docker.io" else {f"{registry}/{repository}"}
    if registry == "docker.io" and repository.startswith("library/"):
        names.add(repository.split("/", 1)[1])
    return names


def _compose_services() -> dict[str, dict]:
    services = yaml.safe_load(COMPOSE_PATH.read_text("utf-8"))["services"]
    assert services, "docker-compose.yml declares no services; every compose check is vacuous"
    return services


def _external_compose_refs() -> dict[str, str]:
    """service -> the third-party image it pulls (locally built images excluded)."""
    refs = {}
    for name, service in _compose_services().items():
        image = service.get("image")
        if not image or service.get("build"):
            continue
        refs[name] = str(image)
    assert refs, "no compose service pulls a third-party image; the F-24 check is vacuous"
    return refs


# -- the lock file itself -----------------------------------------------------


def test_lock_file_is_well_formed() -> None:
    assert LOCK["schema"] == "cap.third-party-images/v1"
    names = [entry["name"] for entry in LOCK["images"]]
    assert len(names) == len(set(names)), f"duplicate image entries: {names}"
    for entry in LOCK["images"]:
        referenced = entry.get("referenced_by", [])
        derived = entry.get("derived_by", [])
        assert referenced or derived, f"{entry['name']} is locked but unused"
        assert not (set(referenced) & set(derived)), (
            f"{entry['name']}: a site either hardcodes the coordinate or derives it "
            f"from this lock, never both: {sorted(set(referenced) & set(derived))}"
        )
        assert entry["registry"] and entry["repository"] and entry["tag"], entry
        assert entry["image_ref"] in _admissible_refs(entry), (
            f"{entry['name']}: {entry['image_ref']!r} is not a spelling of "
            f"{entry['registry']}/{entry['repository']}:{entry['tag']}"
        )


def test_every_locked_digest_says_which_digest_it_is() -> None:
    """F-24 / D.4 #5: an index digest and a platform digest are not interchangeable.

    A per-architecture pin breaks a non-amd64 host silently, and pasting the
    child digest from `docker inspect` is the plausible way an operator "fixes"
    a pin, so the file has to state the kind and the checker has to believe it
    only when the recorded child digest differs from it.
    """
    digested = [entry for entry in LOCK["images"] if entry.get("digest")]
    assert len(digested) == len(LOCK["images"]), (
        f"entries with no digest at all: "
        f"{sorted(e['name'] for e in LOCK['images'] if not e.get('digest'))}"
    )
    for entry in digested:
        assert DIGEST_RE.fullmatch(entry["digest"]), f"{entry['name']}: {entry['digest']}"
        assert any(token in entry.get("digest_kind", "").lower()
                   for token in INDEX_KIND_TOKENS), (
            f"{entry['name']}: digest_kind {entry.get('digest_kind')!r} does not say this is a "
            "manifest-list/index digest"
        )
        assert entry["image_ref"].endswith("@" + entry["digest"]), (
            f"{entry['name']}: the lock records a digest its reference does not carry"
        )
        child = entry.get("platform_digest_linux_amd64")
        assert child and child != entry["digest"], (
            f"{entry['name']}: recording the platform child as the pin is the mistake "
            "the digest_kind field exists to prevent"
        )


def test_derived_sites_read_the_lock_instead_of_copying_it() -> None:
    """``derived_by`` files must resolve the coordinate at runtime.

    A fixture that pastes the digest cannot be distinguished from a stale copy,
    so the claim "this derives it" is only worth checking if it is checked.
    """
    checked = 0
    for entry in LOCK["images"]:
        for rel in entry.get("derived_by", []):
            path = PROJECT_ROOT / rel
            assert path.exists(), f"lock file references a missing site: {rel}"
            text = path.read_text("utf-8")
            assert "third-party-images.json" in text, f"{rel} does not read the lock"
            assert entry["name"] in text, f"{rel} does not select {entry['name']}"
            assert entry["image_ref"] not in text, (
                f"{rel} both hardcodes and derives {entry['name']}: delete the copy"
            )
            checked += 1
    assert checked, "no derived_by sites -- this check would pass vacuously"


def test_digest_pinned_image_uses_an_immutable_reference() -> None:
    entry = _image("minio-object-store")
    assert DIGEST_RE.fullmatch(entry["digest"]), entry["digest"]
    assert entry["image_ref"] == (
        f"{entry['registry']}/{entry['repository']}@{entry['digest']}"
    )


def test_locked_digest_is_the_manifest_list_not_a_platform_or_schema1_digest() -> None:
    """A per-architecture digest would break a non-amd64 runner silently.

    Quay's ``Docker-Content-Digest`` for a tag depends on the Accept header: a
    bare request returns a legacy schema1 conversion whose digest differs from
    the manifest list, so recording the wrong one is an easy, invisible mistake.
    """
    entry = _image("minio-object-store")
    assert "manifest-list" in entry["digest_kind"]
    assert entry["digest"] != (
        "sha256:92114c07a60d96f6d8bbdcb6fe770d3fea4be047e4bf0afb922ff529385f736a"
    ), "that is the schema1 conversion digest"
    assert entry["digest"] != entry["platform_digest_linux_amd64"], (
        "that is the linux/amd64 platform manifest"
    )


# -- reference sites agree with the lock -------------------------------------


def test_every_reference_site_matches_the_lock() -> None:
    """Every surface the lock names must carry exactly the locked reference."""
    problems: list[str] = []
    for entry in LOCK["images"]:
        for rel in entry.get("referenced_by", []):
            path = PROJECT_ROOT / rel
            assert path.exists(), f"lock file references a missing site: {rel}"
            problems.extend(_drift(entry, path.read_text("utf-8"), rel))
    assert not problems, "\n".join(problems)


def _pull_sites(entry: dict, surfaces: dict[str, str]) -> set[str]:
    """Which surfaces name this coordinate -- pinned, unpinned, or pinned wrongly.

    Exact substrings, not patterns: `postgresql://…` must not read as a pull of
    ``postgres:<tag>``, and a surface carrying some *other* digest for the same
    repository is precisely a divergence worth reporting.
    """
    names = _reference_names(entry)
    tag = entry.get("tag")
    markers = {f"{name}:{tag}" for name in names if tag} | {f"{name}@sha256:" for name in names}
    return {
        rel
        for rel, text in surfaces.items()
        if entry["image_ref"] in text or markers & set(_found(text, markers))
    }


def _found(text: str, markers: set[str]) -> list[str]:
    return [marker for marker in sorted(markers) if marker in text]


def _surfaces() -> dict[str, str]:
    return {
        str(path.relative_to(PROJECT_ROOT).as_posix()): path.read_text("utf-8", errors="replace")
        for path in _surface_files()
    }


def test_the_lock_names_every_site_that_pulls_a_locked_image() -> None:
    """D.4 #4, the direction the previous contract could not see.

    A site that edits its image without the lock, or exists at all without being
    listed, used to be invisible: the only check iterated ``referenced_by``, so
    ``ci.yml`` could pull a coordinate the lock pinned differently and nothing
    would notice. This derives the pull sites from the surfaces themselves.

    The two directions are judged over different sets on purpose. An unlisted
    site is looked for only among the surfaces that *pull* (compose, workflows,
    shell, cluster YAML), because a test file may name a coordinate as parsing
    input without pulling it; a listed site is checked against every surface,
    because "the lock says this file pulls it" is false the moment it does not.
    """
    surfaces = _surfaces()
    pull_universe = {k: v for k, v in surfaces.items() if k not in _test_paths()}
    problems: list[str] = []
    for entry in LOCK["images"]:
        listed = set(entry.get("referenced_by", []))
        for rel in sorted(_pull_sites(entry, pull_universe) - listed):
            problems.append(
                f"{entry['name']}: {rel} names this coordinate but is not in referenced_by, "
                "so the lock does not govern it"
            )
        for rel in sorted(listed - _pull_sites(entry, surfaces)):
            problems.append(
                f"{entry['name']}: referenced_by lists {rel}, which no longer pulls "
                f"{entry['image_ref']}"
            )
    assert not problems, "\n".join(problems)


def _test_paths() -> set[str]:
    return {
        str(path.relative_to(PROJECT_ROOT).as_posix())
        for path in (PROJECT_ROOT / "backend" / "tests").glob("*.py")
    }


def test_no_surface_pulls_the_retired_docker_hub_minio() -> None:
    offenders = sorted(
        str(path.relative_to(PROJECT_ROOT))
        for path in _surface_files()
        if _STALE_PATTERN.search(path.read_text("utf-8", errors="replace"))
    )
    assert not offenders, (
        "live surfaces still reference the retired docker.io MinIO image "
        "(pull access denied from certification runners since 2026-09-13): "
        + ", ".join(offenders)
    )


def test_lock_records_the_retirement_with_evidence() -> None:
    """The 'why' is part of the contract, so keep it explicit in the lock.

    Prose here is the only place the retirement timeline survives once the fix
    has aged out of memory; a bare digest tells a future maintainer nothing.
    """
    entry = _image("minio-object-store")
    assert "pull access denied" in entry["previous_ref_status"].lower()
    assert entry["previous_ref"] == "docker.io/" + STALE_MINIO_REF
    assert entry["provenance"]["image_config_labels"]["vendor"] == "MinIO Inc <dev@min.io>"
    assert entry["provenance"]["open_gap"], "provenance must state what was NOT verified"


# -- the compose contract (F-24) ---------------------------------------------


def test_compose_pulls_every_external_image_by_digest_only() -> None:
    """D.4 #1: a deployment surface carries ``repository@sha256:<64 hex>``.

    A tag beside a digest on a compose line is not a decoration: it invites the
    next reader to change the tag and believe they changed what runs.
    """
    pinned = re.compile(r"(?:[a-z0-9.-]+\.[a-z]{2,}/)?[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}")
    tagged = {
        name: ref
        for name, ref in _external_compose_refs().items()
        if not pinned.fullmatch(ref)
    }
    assert not tagged, (
        "these compose services pull a reference that is not a bare digest: "
        + ", ".join(f"{name}={ref}" for name, ref in sorted(tagged.items()))
    )


def test_every_external_compose_image_is_in_the_lock_and_vice_versa() -> None:
    """D.4 #2 and #3 together: neither set may carry an extra member."""
    compose_refs = set(_external_compose_refs().values())
    locked_compose_entries = {
        entry["image_ref"]
        for entry in LOCK["images"]
        if "docker-compose.yml" in entry.get("referenced_by", [])
    }
    assert compose_refs == locked_compose_entries, (
        f"pulled but not locked: {sorted(compose_refs - locked_compose_entries)}; "
        f"locked but not pulled: {sorted(locked_compose_entries - compose_refs)}"
    )


def test_a_locked_compose_digest_is_the_one_the_evidence_measures() -> None:
    """D.4 #5 applied to compose: the line, the lock and the evidence agree."""
    claims = _evidence_claims()
    problems = []
    for name, ref in sorted(_external_compose_refs().items()):
        entry = next(
            (e for e in LOCK["images"] if e["image_ref"] == ref),
            None,
        )
        assert entry, f"{name}: compose pulls {ref}, which no lock entry describes"
        claim = claims.get(_coordinate(entry))
        assert claim, f"{entry['name']}: the tracked evidence measures no such coordinate"
        if claim["index_digest"] != entry["digest"] or f"@{claim['index_digest']}" not in ref:
            problems.append(
                f"{name}: compose/lock pin {entry['digest']} but the evidence measures "
                f"{claim['index_digest']} for {entry['repository']}:{entry['tag']}"
            )
        if claim["pinned_digest_still_resolves"] is False:
            problems.append(f"{name}: the pinned digest no longer resolves in the registry")
    assert not problems, "\n".join(problems)


def test_postgres_is_pinned_by_digest_like_every_other_deployed_image() -> None:
    """The reversal of the old tag exception, guarded rather than deleted.

    The entry was tag-pinned because Docker Hub could not be reached from the
    audit host -- a fact about one machine's egress, since replaced by
    `scripts/release/third_party_registry_evidence.py`. Restoring a mutable tag
    here is now a test failure with a name that says so, which is the point: the
    exception must not be able to reopen quietly.
    """
    entry = _image("postgres")
    assert DIGEST_RE.fullmatch(entry["digest"]), entry["digest"]
    assert entry["image_ref"] == "postgres@" + entry["digest"]
    assert entry["tag"] == "16-alpine", "the tag stays as metadata for the upgrade reviewer"
    assert "16-alpine" in entry["tag_note"]
    policy = LOCK["policy"]["postgres"]
    assert policy["form"] == "repository@digest"
    assert "tag" not in json.dumps(entry["image_ref"])
    for step in ("third_party_registry_evidence.py", "version matrix", "referenced_by"):
        assert step in policy["upgrade_procedure"], f"{step} is missing from the procedure"
    assert policy["known_cost"], "the price of pinning has to be stated where it is paid"


#: The whole extent of the exemption `_drift` grants to an interpolated image
#: value, stated here so it cannot grow by accident: one job, one axis, three
#: versions, all of them named.
DECLARED_MATRIX_PULLS = {
    ".github/workflows/cap-linux-certification.yml": {
        "axis": "postgres",
        "versions": ["15-alpine", "16-alpine", "17-alpine"],
    },
}


def test_the_version_matrix_is_the_only_surface_that_pulls_a_locked_image_by_tag() -> None:
    """A `${{ }}` in an image line is not a pin, so its reach gets its own bound.

    The exemption `_drift` needs for `postgres-version-matrix` is the same shape
    of hole F-24 is closing. Naming every permitted interpolation here turns it
    into an inventory: a second templated pull, a new axis, or a version dropped
    from the declared list all fail this test rather than passing quietly.
    """
    found: dict[str, list[str]] = {}
    for rel, text in _surfaces().items():
        for ref in _reference_values(text):
            if "${{" in ref and "@sha256:" not in ref:
                found.setdefault(rel, []).append(ref.strip())
    assert sorted(found) == sorted(DECLARED_MATRIX_PULLS), (
        f"interpolated pulls are no longer the one declared matrix: {found}"
    )
    for rel, declared in DECLARED_MATRIX_PULLS.items():
        text = (PROJECT_ROOT / rel).read_text("utf-8")
        assert declared["axis"] in _matrix_axes(text), f"{rel} declares no such axis"
        assert f"matrix.{declared['axis']}" in text, f"{rel} no longer pulls the axis it declares"
        for version in declared["versions"]:
            assert f'"{version}"' in text, f"{rel} dropped {version} from the matrix"


def test_an_interpolation_with_no_declared_axis_is_still_drift() -> None:
    """The boundary of the exemption, from both sides."""
    entry = _image("postgres")
    undeclared = _drift(entry, "    image: postgres:${{ inputs.version }}\n", "probe.yml")
    assert any("mutable reference" in problem for problem in undeclared), (
        f"a templated tag passed the checker: {undeclared}"
    )
    declared = (
        "      strategy:\n        matrix:\n          postgres: [\"16-alpine\"]\n"
        "    image: postgres:${{ matrix.postgres }}\n"
    )
    passed = [p for p in _drift(entry, declared, "probe.yml") if "mutable reference" in p]
    assert not passed, f"a declared matrix axis was reported as drift: {passed}"


# -- evidence pointers: the tracked artifact, and nothing else (F-37 closed) --

#: **The contract.** Each entry's `provenance.evidence` names a file; that file
#: exists in this checkout, `git ls-files` returns it, it lives under
#: `EVIDENCE_ROOTS`, and the digests it asserts for this entry's exact coordinate
#: are the digests the entry asserts. The transitional form of this rule let the
#: cited path be an untracked file under `outputs/` as long as a *same-named*
#: copy was tracked somewhere; that allowance is gone, because basename matching
#: cannot distinguish one measurement from a look-alike and the purpose of a
#: pointer is that a reader can open the thing named.
EVIDENCE_ABSENT_JUSTIFICATION: dict[str, tuple[str, ...]] = {}


def _tracked_files() -> set[str]:
    proc = subprocess.run(  # noqa: S603 -- a known command name, no shell
        ("git", "-C", str(PROJECT_ROOT), "ls-files"),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, f"git ls-files failed: {proc.stderr.strip()}"
    files = {
        line.strip().replace("\\", "/")
        for line in proc.stdout.splitlines()
        if line.strip()
    }
    assert files, "git ls-files answered with nothing -- this check would pass vacuously"
    return files


def _digest_claims(payload: object) -> set[str]:
    """Every `sha256:...` a document asserts, anywhere in it.

    Positions, ordering and dates are layout; the digests are the claim. Two
    copies of one measurement taken on different days therefore compare equal,
    which is the distinction the transitional contract needed and the final one
    keeps.
    """
    if isinstance(payload, dict):
        parts = [_digest_claims(value) for value in payload.values()]
    elif isinstance(payload, list):
        parts = [_digest_claims(value) for value in payload]
    else:
        return {payload} if isinstance(payload, str) and DIGEST_RE.fullmatch(payload) else set()
    return set().union(*parts) if parts else set()


def _evidence_document() -> dict:
    path = PROJECT_ROOT / EVIDENCE_ARTIFACT
    assert path.is_file(), (
        f"{EVIDENCE_ARTIFACT} is cited by every lock entry and is not in this checkout"
    )
    document = json.loads(path.read_text("utf-8"))
    assert document["schema"] == "cap.third-party-registry-evidence/v1", document["schema"]
    assert document["generator"] == "scripts/release/third_party_registry_evidence.py"
    return document


def _evidence_claims() -> dict[tuple[str, str, str], dict]:
    return {_coordinate(claim): claim for claim in _evidence_document()["images"]}


def _pointer_problems(
    entries: list[dict], tracked: set[str], claims: dict[tuple[str, str, str], dict]
) -> list[str]:
    """Each problem is a citation a fresh clone cannot verify, in one line."""
    problems: list[str] = []
    for entry in entries:
        name = entry["name"]
        cited = str((entry.get("provenance") or {}).get("evidence") or "")
        if not cited:
            problems.append(
                f"{name}: asserts {entry.get('digest')} and cites no evidence file at all"
            )
            continue
        if not cited.startswith(EVIDENCE_ROOTS):
            problems.append(
                f"{name}: cites {cited}, outside {' or '.join(EVIDENCE_ROOTS)}, where a "
                "fresh clone cannot find generated evidence"
            )
            continue
        if cited not in tracked:
            problems.append(
                f"{name}: {cited} is not git-tracked, so a fresh clone cannot open the "
                "proof it cites (a same-named copy elsewhere does not count)"
            )
            continue
        if not (PROJECT_ROOT / cited).is_file():
            problems.append(
                f"{name}: {cited} is tracked but absent from this checkout"
            )
            continue
        claim = claims.get(_coordinate(entry))
        if claim is None:
            problems.append(
                f"{name}: {cited} measures no "
                f"{entry['registry']}/{entry['repository']}:{entry['tag']}, so it cannot "
                "vouch for this digest"
            )
            continue
        asserted = _digest_claims(claim)
        wanted = {entry["digest"], entry.get("platform_digest_linux_amd64")} - {None}
        if asserted != wanted:
            problems.append(
                f"{name}: {cited} asserts {sorted(asserted)} for "
                f"{entry['repository']}:{entry['tag']}, the lock asserts {sorted(wanted)}"
            )
    return problems


def test_cited_evidence_is_tracked_and_asserts_exactly_the_locked_digests() -> None:
    problems = _pointer_problems(LOCK["images"], _tracked_files(), _evidence_claims())
    assert not problems, "\n".join(problems)


def test_an_entry_with_no_evidence_pointer_is_refused() -> None:
    """Absence has to be a stated rule, not a gap that grows.

    The transitional table named the entries that may cite nothing. F-24 gave
    every locked image a generator-measured digest, so the table is empty by
    construction and stays that way only while nobody adds an exception --
    which is exactly what the comparison below makes visible.
    """
    without = sorted(
        entry["name"]
        for entry in LOCK["images"]
        if not (entry.get("provenance") or {}).get("evidence")
    )
    assert without == sorted(EVIDENCE_ABSENT_JUSTIFICATION), (
        f"entries citing no evidence: {without}; the empty table means each one has to "
        "be justified by name before it may cite nothing"
    )


# -- the binary bootstrap contract -------------------------------------------


def _workflow_text() -> str:
    texts = [path.read_text("utf-8") for path in sorted(WORKFLOWS.glob("*.yml"))]
    assert texts, f"no workflows under {WORKFLOWS}"
    return "\n".join(texts)


def test_mc_bootstrap_is_pinned_verifiable_and_fatal_on_mismatch() -> None:
    mc = next(binary for binary in LOCK["binaries"] if binary["name"] == "mc")
    block = _workflow_text()
    assert re.fullmatch(r"[0-9a-f]{64}", mc["sha256"]), mc["sha256"]
    assert "mc_release=" in block, "mc must be installed from a pinned release tag"
    assert "sha256sum --check" in block, "mc install must verify the published sha256sum"
    assert "curl -fSsL" in block, "curl without -f turns a 410 Gone page into a binary"
    assert "FATAL: mc checksum" in block, "a checksum mismatch must stop the job"
    assert not re.search(r"curl[^|]*dl\.min\.io", block), (
        "dl.min.io serves HTTP 410 for community releases; it cannot back a bootstrap"
    )


def test_kind_bootstrap_fails_loudly() -> None:
    kind = next(binary for binary in LOCK["binaries"] if binary["name"] == "kind")
    assert kind["version"] in _workflow_text()
    checked = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line in path.read_text("utf-8").splitlines():
            if "curl" in line and "kind" in line:
                checked += 1
                assert "-f" in line, f"{path.name}: the kind download must use curl -f"
    assert checked, "no kind bootstrap line found -- this check would pass vacuously"


# -- negative controls --------------------------------------------------------

#: The three ways a maintainer most plausibly edits a MinIO reference.
DRIFTED_SURFACES = [
    "    image: quay.io/minio/minio:RELEASE.2099-01-01T00-00-00Z",
    f"    image: {STALE_MINIO_REF}",
    "    image: quay.io/minio/minio@sha256:" + "0" * 64,
]


@pytest.mark.parametrize("surface", DRIFTED_SURFACES)
def test_drift_checker_reports_a_moved_reference(surface: str) -> None:
    entry = _image("minio-object-store")
    assert _drift(entry, "minio:\n" + surface + "\n", "probe.yml"), (
        f"the checker accepted a surface the lock does not describe: {surface}"
    )


def test_drift_checker_accepts_the_locked_reference() -> None:
    """The other half of a negative control: it must not fail every input.

    A checker that always returns a problem makes the real assertion theatre.
    """
    entry = _image("minio-object-store")
    good = "    image: " + entry["image_ref"]
    assert _drift(entry, "minio:\n" + good + "\n", "probe.yml") == []


def test_drift_checker_reports_a_tag_where_the_lock_pins_a_digest() -> None:
    """The same control for a service that was newly pinned, not for MinIO."""
    for name in ("postgres", "redis", "prometheus", "grafana", "pgadmin4"):
        entry = _image(name)
        surface = f"    image: {entry['image_ref'].split('@', 1)[0]}:{entry['tag']}"
        problems = _drift(entry, f"{name}:\n{surface}\n", "probe.yml")
        assert any("mutable reference" in problem for problem in problems), (
            f"{name}: reverting to a tag passed the checker"
        )
        assert _drift(entry, f"    image: {entry['image_ref']}\n", "probe.yml") == []


def test_the_pull_site_finder_catches_a_surface_the_lock_does_not_name() -> None:
    """The detector behind D.4 #4, seen working on surfaces that do not exist.

    `ci.yml` pulled a locked coordinate for the whole life of this file's
    predecessor without appearing in `referenced_by`, and no assertion could
    notice, because every assertion started from the list. The finder is what
    makes that visible, so it gets its own control: an unlisted pull, a divergent
    digest, and a listed site that stopped pulling.
    """
    entry = _image("redis")
    surfaces = {
        "docker-compose.yml": f"    image: {entry['image_ref']}\n",
        # pulled by tag in a workflow nobody listed -- the exact ci.yml shape
        ".github/workflows/quiet.yml": f"    image: {entry['repository']}:{entry['tag']}\n",
        # pinned, but to something else: a divergence, not an agreement
        ".github/workflows/other.yml": (
            f"    image: {entry['repository']}@sha256:{'0' * 64}\n"
        ),
        # listed by the lock, and no longer pulling it
        "scripts/certification/gone.sh": "echo nothing here\n",
        # a DSN is not a pull
        "backend/tests/probe.py": 'URL = "redis://user@host:6379/0"\n',
    }
    found = _pull_sites(entry, surfaces)
    assert found == {
        "docker-compose.yml",
        ".github/workflows/quiet.yml",
        ".github/workflows/other.yml",
    }, found
    assert entry["image_ref"] in surfaces["docker-compose.yml"]


def test_a_pointer_to_an_untracked_file_is_not_rescued_by_a_same_named_copy() -> None:
    """The control that keeps the deleted workaround deleted.

    Batch 1 accepted `outputs/x/registry-base-digests.json` as a pointer because
    a copy of that basename was tracked under `docs/quality/artifacts/`. Put the
    twin back and this must still fail -- otherwise the mechanism that hid an
    unreadable citation is only dormant.
    """
    twin = "docs/quality/artifacts/cap-1.0.6-rc1-artifact-closure/registry-base-digests.json"
    tracked = _tracked_files()
    assert twin in tracked, "the historical tracked twin vanished; the control proves nothing"
    entry = dict(_image("redis"))
    untracked_citation = json.loads(json.dumps(entry))
    untracked_citation["provenance"]["evidence"] = (
        "outputs/artifact-closure/registry-base-digests.json"
    )
    problems = _pointer_problems([untracked_citation], tracked, _evidence_claims())
    assert problems and "outside" in problems[0], problems

    # And with the citation moved *inside* the evidence roots but never tracked,
    # the basename match is what used to save it. It must not.
    look_alike = dict(untracked_citation)
    look_alike["provenance"] = {"evidence": "docs/quality/artifacts/registry-base-digests.json"}
    assert "docs/quality/artifacts/registry-base-digests.json" not in tracked
    problems = _pointer_problems([look_alike], tracked, _evidence_claims())
    assert problems and "is not git-tracked" in problems[0], problems


def test_a_pointer_whose_digest_moved_is_refused() -> None:
    claims = _evidence_claims()
    entry = dict(_image("redis"))
    entry["digest"] = "sha256:" + "c" * 64
    problems = _pointer_problems([entry], _tracked_files(), claims)
    assert problems and "asserts" in problems[0], problems
    # Conversely: the lock is right and the pointer names a coordinate nobody
    # measured. Same refusal path, different cause, both have to be visible.
    unknown = dict(_image("redis"))
    unknown["repository"] = "library/never-measured"
    problems = _pointer_problems([unknown], _tracked_files(), claims)
    assert problems and "measures no" in problems[0], problems


def test_stale_reference_pattern_is_precise() -> None:
    assert _STALE_PATTERN.search(f"image: {STALE_MINIO_REF}")
    assert not _STALE_PATTERN.search(
        "image: quay.io/minio/minio@sha256:" + "a" * 64
    ), "the pinned quay.io coordinate must not read as stale"
    assert not _STALE_PATTERN.search("pip install minio/minio-py"), (
        "a hyphenated sibling project is not the server image"
    )
