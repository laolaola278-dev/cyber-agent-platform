"""Contract tests for the tracked registry-evidence generator (Batch 2, item E2).

The generator's claim is that it refuses to write what it cannot verify, so every test here is
either a proof that a real resolution is recorded correctly or a control showing that a specific
doubt stops the run. A check no control reddens is documentation, not a guard, and this
repository has already been burned by that distinction (F-33, F-42).

Nothing here touches the network, and nothing here stubs the logic under test: the injected
object is the HTTP *transport*, so `Registry.token`, the WWW-Authenticate challenge parsing, the
401 retry, the digest round-trip and the platform filters are the code being exercised.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "release"))

import third_party_registry_evidence as gen  # noqa: E402

AUTH_REALM = "https://auth.example/token"
PULL_TOKEN = "pull-token-never-written"
EXAMPLE = "library/example"


def digest_of(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def child(image_digest: str, os_name: str, architecture: str, size: int = 1024,
          variant: str | None = None) -> dict:
    platform: dict[str, str] = {"os": os_name, "architecture": architecture}
    if variant:
        platform["variant"] = variant
    return {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": image_digest,
            "size": size, "platform": platform}


def index_body(children: list[dict]) -> bytes:
    return json.dumps({"schemaVersion": 2,
                       "mediaType": "application/vnd.oci.image.index.v1+json",
                       "manifests": children}, sort_keys=True).encode()


def single_body() -> bytes:
    return json.dumps({"schemaVersion": 2,
                       "mediaType": "application/vnd.oci.image.manifest.v1+json"}).encode()


def target(name: str = "example", repository: str = EXAMPLE, tag: str = "1.2",
           pinned: str | None = None, **kwargs) -> gen.Target:
    return gen.Target(name=name, registry="docker.io", repository=repository, tag=tag,
                      pinned_digest=pinned, sources=("docker-compose.yml:1",), **kwargs)


class FakeTransport:
    """A registry that answers exactly what a test tells it to, including badly.

    Content is keyed by repository *and* reference. Keying on the reference alone would let a
    request for an image that was never served be answered with another image's bytes, and the
    "incomplete target set" controls would then pass by accident.
    """

    def __init__(self) -> None:
        self.bodies: dict[str, bytes] = {}
        self.served_digest: dict[str, bool] = {}
        self.statuses: dict[str, int] = {}
        self.forced: dict[str, str] = {}
        self.moving: dict[str, list[bytes]] = {}
        self.challenge_fields = {"realm": AUTH_REALM, "service": "registry"}
        self.token_status = 200
        self.token_body: bytes | None = None
        self.urls: list[str] = []

    @staticmethod
    def key(repository: str, reference: str) -> str:
        return f"{repository}@{reference}"

    def serve(self, reference: str, body: bytes, *, repository: str = EXAMPLE,
              header: bool = True, status: int = 200) -> str:
        key = self.key(repository, reference)
        self.bodies[key] = body
        self.served_digest[key] = header
        self.statuses[key] = status
        return digest_of(body)

    def get(self, url: str, headers: dict[str, str] | None = None) -> gen.Response:
        self.urls.append(url)
        if url.endswith("/v2/"):
            challenge = ", ".join(f'{field}="{value}"'
                                  for field, value in self.challenge_fields.items())
            return gen.Response(401, {"www-authenticate": f"Bearer {challenge}"}, b"")
        if url.startswith(AUTH_REALM):
            body = (self.token_body if self.token_body is not None
                    else json.dumps({"token": PULL_TOKEN}).encode())
            return gen.Response(self.token_status, {"content-type": "application/json"}, body)
        if "/manifests/" not in url:
            return gen.Response(404, {}, b"")
        repository, _, reference = unquote(url.split("/v2/", 1)[-1]).partition("/manifests/")
        key = self.key(repository, reference)
        answer_headers = {"content-type": "application/vnd.oci.image.index.v1+json"}
        if key in self.moving:
            body = self.moving[key].pop(0)
            answer_headers["docker-content-digest"] = digest_of(body)
            return gen.Response(200, answer_headers, body)
        if key not in self.bodies:
            return gen.Response(404, {}, b"")
        body = self.bodies[key]
        if key in self.forced:
            answer_headers["docker-content-digest"] = self.forced[key]
        elif self.served_digest.get(key, True):
            answer_headers["docker-content-digest"] = digest_of(body)
        return gen.Response(self.statuses.get(key, 200), answer_headers, body)


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def registry(transport: FakeTransport) -> gen.Registry:
    return gen.Registry(transport)  # type: ignore[arg-type]


def serve_index(transport: FakeTransport, reference: str = "1.2",
                children: list[dict] | None = None,
                repository: str = EXAMPLE) -> tuple[bytes, str]:
    """Serve one index by tag and by digest, so a well-formed run can complete."""
    body = index_body(children or [child("sha256:" + "a1" * 32, "linux", "amd64")])
    digest = transport.serve(reference, body, repository=repository)
    transport.serve(digest, body, repository=repository)
    return body, digest


def isolate_locks_and_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Look at the claims alone, with the repository's own surfaces out of the way."""
    monkeypatch.setattr(gen, "_load_lock", lambda root: {"images": []})
    monkeypatch.setattr(gen, "compose_refs", lambda root: {})
    monkeypatch.setattr(gen, "dockerfile_refs", lambda root: {})


# ------------------------------------------------------------------- a clean resolution


def test_one_clean_index_resolves_to_a_verified_claim(registry: gen.Registry,
                                                     transport: FakeTransport) -> None:
    amd64 = child("sha256:" + "a1" * 32, "linux", "amd64", size=1234)
    _, digest = serve_index(transport, children=[
        amd64, child("sha256:" + "b2" * 32, "linux", "arm64")])

    claim = gen.resolve(target(), registry)

    assert claim["index_digest"] == digest
    assert claim["digest_kind"] == "manifest-list (multi-arch index)"
    assert claim["platforms"] == ["linux/amd64", "linux/arm64"]
    assert claim["linux_amd64_child"] == {"digest": amd64["digest"], "size": 1234,
                                          "variant": None}
    assert claim["served_digest_verified_against_body"] is True
    assert claim["refetch_by_digest_byte_identical"] is True
    assert claim["sources"] == ["docker-compose.yml:1"]


def test_the_pull_token_is_used_and_never_written(registry: gen.Registry,
                                                 transport: FakeTransport) -> None:
    serve_index(transport)

    document = gen.serialise(gen.build_document([gen.resolve(target(), registry)], "2026-09-22"))

    assert PULL_TOKEN not in document
    assert "authorization" not in document.lower()
    assert registry.secrets == {PULL_TOKEN}, "the run has to know what it must not leak"


def test_an_attestation_entry_is_not_counted_as_a_platform(registry: gen.Registry,
                                                          transport: FakeTransport) -> None:
    """Real indexes carry `unknown/unknown` entries; counting them misstates the arch set."""
    serve_index(transport, children=[
        child("sha256:" + "a1" * 32, "linux", "amd64"),
        child(digest_of(b"attestation"), "unknown", "unknown", size=633)])

    claim = gen.resolve(target(), registry)

    assert claim["platforms"] == ["linux/amd64"]
    assert claim["child_count"] == 2


def test_two_targets_naming_the_same_bytes_are_not_a_conflict(registry: gen.Registry,
                                                             transport: FakeTransport) -> None:
    """Two tags legitimately pointing at one build are not a defect; one coordinate answering
    twice is."""
    body, digest = serve_index(transport)
    transport.serve("1.2-alpine", body)
    pair = [target(name="first"), target(name="second", tag="1.2-alpine")]

    claims = gen.resolve_all(pair, registry)
    problems = gen.claims_are_reproducible(pair, registry)

    assert claims[0]["index_digest"] == claims[1]["index_digest"] == digest_of(body)
    assert digest == digest_of(body)
    assert problems == []


def test_one_coordinate_answering_twice_is_a_conflict(registry: gen.Registry,
                                                     transport: FakeTransport) -> None:
    first = index_body([child("sha256:" + "a1" * 32, "linux", "amd64")])
    second = index_body([child("sha256:" + "b2" * 32, "linux", "amd64")])
    transport.serve(digest_of(first), first)
    transport.serve(digest_of(second), second)
    transport.moving[transport.key(EXAMPLE, "1.2")] = [first, second]

    problems = gen.claims_are_reproducible([target()], registry)

    assert len(problems) == 1, problems
    assert "the tag moved" in problems[0]


def test_observation_metadata_is_outside_the_digest_claims(registry: gen.Registry,
                                                          transport: FakeTransport) -> None:
    serve_index(transport)
    claims = gen.resolve_all([target()], registry)

    early = gen.build_document(claims, "2020-01-01")
    late = gen.build_document(claims, "2026-09-22")

    # The reproducibility comparison runs on parsed claims, not on a text substitution: these
    # documents differ in serialised form and are identical where they assert content.
    assert gen.digest_claims(early) == gen.digest_claims(late)
    assert gen.serialise(early) != gen.serialise(late)
    assert "verified_on" not in json.dumps(gen.digest_claims(late))
    assert late["observation"]["target_count"] == 1


def test_the_document_is_sorted_key_sorted_and_lf_only(registry: gen.Registry,
                                                       transport: FakeTransport) -> None:
    serve_index(transport)
    document = gen.build_document(
        gen.resolve_all([target(name="zeta"), target(name="alpha")], registry), "2026-09-22")
    text = gen.serialise(document)

    assert [claim["name"] for claim in document["images"]] == ["alpha", "zeta"]
    assert text.endswith("\n") and "\r\n" not in text
    assert json.loads(text)["schema"] == gen.SCHEMA


# ------------------------------------------------------ the ways a run must be refused


def test_missing_content_digest_is_refused(registry: gen.Registry,
                                          transport: FakeTransport) -> None:
    body = index_body([child("sha256:" + "a1" * 32, "linux", "amd64")])
    transport.serve("1.2", body, header=False)
    transport.serve(digest_of(body), body)

    with pytest.raises(gen.Refusal, match="no Docker-Content-Digest"):
        gen.resolve(target(), registry)


def test_a_served_digest_that_is_not_the_body_is_refused(registry: gen.Registry,
                                                        transport: FakeTransport) -> None:
    """The rewrite case: the header names one digest, the bytes hash to another."""
    shown = index_body([child("sha256:" + "a1" * 32, "linux", "amd64")])
    hidden = index_body([child("sha256:" + "b2" * 32, "linux", "amd64")])
    transport.serve("1.2", hidden)
    transport.forced[transport.key(EXAMPLE, "1.2")] = digest_of(shown)
    transport.serve(digest_of(shown), shown)

    with pytest.raises(gen.Refusal, match="not the digest"):
        gen.resolve(target(), registry)


def test_a_digest_that_refetches_different_bytes_is_refused(registry: gen.Registry,
                                                           transport: FakeTransport) -> None:
    body = index_body([child("sha256:" + "a1" * 32, "linux", "amd64")])
    other = index_body([child("sha256:" + "b2" * 32, "linux", "amd64")])
    transport.serve("1.2", body)
    transport.serve(digest_of(body), other, header=False)

    with pytest.raises(gen.Refusal, match="bytes that differ"):
        gen.resolve(target(), registry)


def test_a_single_manifest_is_refused_where_an_index_is_required(registry: gen.Registry,
                                                                transport: FakeTransport) -> None:
    body = single_body()
    transport.serve("1.2", body)
    transport.serve(digest_of(body), body)

    with pytest.raises(gen.Refusal, match="single manifest"):
        gen.resolve(target(), registry)


def test_a_single_manifest_is_allowed_only_when_the_target_says_so(registry: gen.Registry,
                                                                  transport: FakeTransport) -> None:
    body = single_body()
    transport.serve("1.2", body)
    transport.serve(digest_of(body), body)

    claim = gen.resolve(target(require_index=False), registry)

    assert claim["digest_kind"] == "single manifest"
    assert claim["platforms"] == []


@pytest.mark.parametrize("children", [
    pytest.param([child("sha256:" + "b2" * 32, "linux", "arm64")], id="no-amd64-child"),
    pytest.param([child("sha256:" + "a1" * 32, "linux", "amd64", 10),
                  child("sha256:" + "a2" * 32, "linux", "amd64", 20)], id="two-amd64-children"),
    pytest.param([child("sha256:" + "a1" * 32, "linux", "amd64", 10, "v2"),
                  child("sha256:" + "a2" * 32, "linux", "amd64", 20, "v3")],
                 id="two-amd64-variants"),
])
def test_an_ambiguous_amd64_target_is_refused(registry: gen.Registry, transport: FakeTransport,
                                             children: list[dict]) -> None:
    serve_index(transport, children=children)

    with pytest.raises(gen.Refusal, match=r"\d linux/amd64 children"):
        gen.resolve(target(), registry)


def test_an_unparsable_auth_challenge_is_refused(registry: gen.Registry,
                                                transport: FakeTransport) -> None:
    serve_index(transport)
    transport.challenge_fields = {"service": "registry"}  # a Bearer challenge with no realm

    with pytest.raises(gen.Refusal, match="no realm"):
        gen.resolve(target(), registry)


def test_the_token_scope_names_the_repository_being_pulled(registry: gen.Registry,
                                                          transport: FakeTransport) -> None:
    """A registry may advertise a placeholder scope; asking for that token is a 403.

    ghcr answers the `/v2/` probe with `scope="repository:user/image:pull"`, so a client that
    trusts the challenge instead of naming its own resource cannot pull anything.
    """
    serve_index(transport)
    transport.challenge_fields = {"realm": AUTH_REALM, "service": "ghcr.io",
                                  "scope": "repository:user/image:pull"}

    gen.resolve(target(), registry)

    from urllib.parse import parse_qs, urlparse
    token_urls = [url for url in transport.urls if url.startswith(AUTH_REALM)]
    assert len(token_urls) == 1, transport.urls
    query = parse_qs(urlparse(token_urls[0]).query)
    assert query["scope"] == ["repository:library/example:pull"], query
    assert query["service"] == ["ghcr.io"], query
    assert "user/image" not in token_urls[0], token_urls[0]


def test_a_token_endpoint_that_refuses_is_refused(registry: gen.Registry,
                                                 transport: FakeTransport) -> None:
    serve_index(transport)
    transport.token_status = 403

    with pytest.raises(gen.Refusal, match="HTTP 403"):
        gen.resolve(target(), registry)


def test_a_token_answer_without_a_token_field_is_refused(registry: gen.Registry,
                                                        transport: FakeTransport) -> None:
    serve_index(transport)
    transport.token_body = b'{"access_token": ""}'

    with pytest.raises(gen.Refusal, match="without a token field"):
        gen.resolve(target(), registry)


def test_a_token_answer_that_is_not_json_is_refused(registry: gen.Registry,
                                                   transport: FakeTransport) -> None:
    serve_index(transport)
    transport.token_body = b"<html>sign in</html>"

    with pytest.raises(gen.Refusal, match="non-JSON"):
        gen.resolve(target(), registry)


def test_a_token_answer_that_is_json_but_not_an_object_is_refused(registry: gen.Registry,
                                                                 transport: FakeTransport) -> None:
    serve_index(transport)
    transport.token_body = b'["a", "list"]'

    with pytest.raises(gen.Refusal, match="a list"):
        gen.resolve(target(), registry)


def test_a_registry_error_on_the_manifest_is_refused(registry: gen.Registry,
                                                    transport: FakeTransport) -> None:
    body = index_body([child("sha256:" + "a1" * 32, "linux", "amd64")])
    transport.serve("1.2", body, status=500)

    with pytest.raises(gen.Refusal, match="HTTP 500"):
        gen.resolve(target(), registry)


def test_an_empty_manifest_body_is_refused(registry: gen.Registry,
                                          transport: FakeTransport) -> None:
    transport.serve("1.2", b"")

    with pytest.raises(gen.Refusal, match="empty body"):
        gen.resolve(target(), registry)


def test_a_target_the_registry_does_not_know_is_refused(registry: gen.Registry,
                                                      transport: FakeTransport) -> None:
    serve_index(transport)

    with pytest.raises(gen.Refusal, match="HTTP 404"):
        gen.resolve(target(repository="library/never-served"), registry)


def test_a_dropped_connection_is_a_refusal_not_a_traceback(registry: gen.Registry,
                                                          transport: FakeTransport) -> None:
    """Stage 2 runs where registries are flaky; the artifact's protection has to survive that.

    A reset mid-read must be reported as a refusal, so the caller's rule -- never replace a
    complete evidence file with a partial one -- is the path actually taken.
    """
    serve_index(transport)
    # Bound before the reassignment below: calling `transport.get` here would call
    # `dropping`, which is what `transport.get` becomes -- an infinite loop, not a test.
    served = transport.get

    def dropping(url: str, headers: dict[str, str] | None = None) -> gen.Response:
        if "/manifests/" in url:
            raise http.client.RemoteDisconnected("connection reset by registry")
        return served(url, headers)

    transport.get = dropping  # type: ignore[method-assign]

    with pytest.raises(gen.Refusal, match="connection failed"):
        gen.resolve_all([target()], registry)


def test_a_pinned_digest_that_no_longer_resolves_is_refused(registry: gen.Registry,
                                                           transport: FakeTransport) -> None:
    """A surface pinned to bytes the registry dropped cannot pull, whatever the tag claims."""
    serve_index(transport)

    with pytest.raises(gen.Refusal, match="HTTP 404"):
        gen.resolve(target(pinned="sha256:" + "9f" * 32), registry)


def test_a_moved_pin_that_still_resolves_is_recorded_as_drift(registry: gen.Registry,
                                                             transport: FakeTransport) -> None:
    _, digest = serve_index(transport)
    old = index_body([child("sha256:" + "b2" * 32, "linux", "amd64")])
    transport.serve(digest_of(old), old)

    claim = gen.resolve(target(pinned=digest_of(old)), registry)

    assert claim["index_digest"] == digest
    assert claim["tag_currently_serves_pin"] is False
    assert claim["pinned_digest_still_resolves"] is True


def test_one_failure_refuses_the_whole_run(registry: gen.Registry,
                                          transport: FakeTransport) -> None:
    serve_index(transport)
    pair = [target(name="good"), target(name="bad", repository="library/absent")]

    with pytest.raises(gen.Refusal) as caught:
        gen.resolve_all(pair, registry)

    assert "library/absent" in str(caught.value)
    assert "library/example" not in str(caught.value), "a clean target is no reason to stop"


# ----------------------------------------------------------- the derived target set, live


def test_the_repository_surfaces_are_all_covered_by_derived_targets() -> None:
    targets = gen.collect_targets(str(PROJECT_ROOT))
    names = {one.name for one in targets}

    assert {"postgres", "minio-object-store", "astral-sh-uv-0.8.3-base",
            "python-3.13-slim-base", "python-3.13.12-slim-bookworm-base",
            "node-22-alpine-base", "nginx-1.30.4-alpine-base"} <= names
    assert {"library/redis", "prom/prometheus", "grafana/grafana",
            "dpage/pgadmin4"} <= {one.repository for one in targets}
    assert gen.target_set_problems(targets, str(PROJECT_ROOT)) == []


def test_the_compose_services_appear_as_targets_with_their_own_sites() -> None:
    targets = {one.name: one for one in gen.collect_targets(str(PROJECT_ROOT))}
    postgres = targets["postgres"]

    assert "docker-compose.yml:10" in postgres.sources
    assert postgres.pinned_digest is None, "postgres is pinned by Batch 2, not before it"


def test_scratch_directories_are_not_read_as_surfaces() -> None:
    """`_tmp/` holds probe Dockerfiles and stale worktrees; a fresh clone has none of them."""
    paths = gen.tracked_paths(str(PROJECT_ROOT), gen.DOCKERFILE_RE)

    assert paths, "the index listed no Dockerfiles, so this check would pass vacuously"
    assert not [path for path in paths if path.startswith("_tmp/")], paths


def test_the_git_index_and_not_the_working_tree_decides_a_surface(tmp_path: Path) -> None:
    """A Dockerfile someone dropped in the tree without committing is not a product surface."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", str(repo)), check=True, capture_output=True)
    (repo / "Dockerfile").write_text("FROM committed/base:1\n", encoding="utf-8")
    (repo / "dropped.Dockerfile").write_text("FROM dropped/in:1\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(repo), "add", "Dockerfile"), check=True,
                   capture_output=True)

    assert gen.tracked_paths(str(repo), gen.DOCKERFILE_RE) == ["Dockerfile"]
    refs = gen.dockerfile_refs(str(repo))
    assert "committed/base:1" in refs
    assert not any("dropped" in ref for ref in refs), (
        "an untracked Dockerfile leaked into the target set")


def test_a_compose_image_no_target_resolves_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    targets = gen.collect_targets(str(PROJECT_ROOT))
    monkeypatch.setattr(gen, "compose_refs",
                        lambda root: {"evil/image:tag": ["docker-compose.yml:999"]})

    problems = gen.target_set_problems(targets, str(PROJECT_ROOT))

    assert any("evil/image:tag" in problem for problem in problems), problems


def test_an_untagged_undigested_base_is_refused_as_implicit_latest(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gen, "dockerfile_refs", lambda root: {"python": ["b/Dockerfile:1"]})

    with pytest.raises(gen.Refusal, match=":latest"):
        gen.collect_targets(str(PROJECT_ROOT))


@pytest.mark.parametrize(("ref", "expected"), [
    ("postgres:16-alpine", ("docker.io", "library/postgres", "16-alpine", None)),
    ("prom/prometheus:v2.55.1", ("docker.io", "prom/prometheus", "v2.55.1", None)),
    ("quay.io/minio/minio:latest", ("quay.io", "minio/minio", "latest", None)),
    ("busybox", ("docker.io", "library/busybox", None, None)),
    ("ghcr.io/astral-sh/uv:0.8.3@sha256:aa",
     ("ghcr.io", "astral-sh/uv", "0.8.3", "sha256:aa")),
])
def test_reference_parsing_keeps_tag_absent_distinct_from_latest(ref: str,
                                                                expected: tuple) -> None:
    assert gen.split_image_ref(ref) == expected


def test_a_digest_only_compose_reference_borrows_the_locks_tag() -> None:
    """MinIO's compose line names no tag; the lock knows which tag that digest came from."""
    targets = {one.name: one for one in gen.collect_targets(str(PROJECT_ROOT))}
    minio = targets["minio-object-store"]

    assert minio.reference == "RELEASE.2025-04-22T22-12-26Z"
    assert minio.pinned_digest and minio.pinned_digest.startswith("sha256:a1ea29fa")


# ------------------------------------------------------- the artifact itself is complete


def test_a_missing_claim_is_reported_as_an_incomplete_artifact(registry: gen.Registry,
                                                              transport: FakeTransport,
                                                              monkeypatch: pytest.MonkeyPatch
                                                              ) -> None:
    """The file has to carry every coordinate, or it is a partial artifact wearing a complete
    artifact's clothes."""
    serve_index(transport)
    claims = gen.resolve_all([target()], registry)

    problems = gen.claim_set_problems(claims, str(PROJECT_ROOT))

    assert problems, "one claim cannot cover this repository's surfaces"
    assert any("postgres" in problem for problem in problems), problems

    isolate_locks_and_surfaces(monkeypatch)
    assert gen.claim_set_problems(claims, str(PROJECT_ROOT)) == []


def test_two_claims_sharing_a_name_are_reported(registry: gen.Registry, transport: FakeTransport,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Names are the keys a reader looks up, so a duplicate makes the artifact ambiguous."""
    serve_index(transport)
    transport.serve("1.3", index_body([child("sha256:" + "b2" * 32, "linux", "amd64")]))
    transport.serve(digest_of(index_body([child("sha256:" + "b2" * 32, "linux", "amd64")])),
                    index_body([child("sha256:" + "b2" * 32, "linux", "amd64")]))
    claims = gen.resolve_all([target(name="same"), target(name="same", tag="1.3")], registry)
    isolate_locks_and_surfaces(monkeypatch)

    problems = gen.claim_set_problems(claims, str(PROJECT_ROOT))

    assert any("share a name" in problem for problem in problems), problems


# ------------------------------------------------------------------ writing it out safely


def test_write_atomic_creates_directories_and_leaves_no_temporary(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "registries.json"

    gen.write_atomic(str(out), '{"a": 1}\n')

    assert out.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert not (tmp_path / "nested" / "registries.json.partial").exists()


def test_a_refusal_happens_before_any_write_is_attempted(tmp_path: Path, registry: gen.Registry,
                                                        transport: FakeTransport) -> None:
    """The ordering that protects the artifact: resolve everything, then write once."""
    out = tmp_path / "registries.json"
    gen.write_atomic(str(out), "previous complete content\n")
    body = index_body([child("sha256:" + "a1" * 32, "linux", "amd64")])
    transport.serve("1.2", body)  # the by-digest refetch deliberately 404s

    with pytest.raises(gen.Refusal):
        gen.resolve_all([target()], registry)

    assert out.read_text(encoding="utf-8") == "previous complete content\n"


@pytest.mark.parametrize("needle", [
    pytest.param(PULL_TOKEN, id="a-live-token"),
    pytest.param('{"Authorization": "Bearer x"}', id="an-auth-header"),
    pytest.param("WWW-Authenticate: Bearer realm", id="a-challenge"),
    pytest.param('{"password": "hunter2"}', id="a-password"),
])
def test_a_credential_anywhere_in_the_output_refuses_the_write(needle: str) -> None:
    document = {"schema": gen.SCHEMA, "images": [{"note": needle}]}

    with pytest.raises(gen.Refusal, match="refusing to write"):
        gen.assert_no_credentials(gen.serialise(document), {PULL_TOKEN})


def test_a_clean_document_passes_the_credential_check() -> None:
    document = gen.build_document(
        [{"name": "x", "index_digest": "sha256:" + "a1" * 32}], "2026-09-22")

    gen.assert_no_credentials(gen.serialise(document), set())


def test_the_generator_carries_no_machine_credential_path() -> None:
    """E2's contract is anonymous-plus-challenge: nothing here reads a login store."""
    source = (PROJECT_ROOT / "scripts" / "release"
              / "third_party_registry_evidence.py").read_text(encoding="utf-8")

    for banned in ("credential fill", "GH_TOKEN", "GITHUB_TOKEN", "getpass", "netrc",
                   "keyring", "config.json", "api.github.com"):
        assert banned not in source, banned
    assert "self.secrets.add" in source, "the token has to be remembered to be excluded"


def test_main_refuses_an_unparsable_observation_date(capsys: pytest.CaptureFixture) -> None:
    assert gen.main(["--verified-on", "yesterday"]) == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err
