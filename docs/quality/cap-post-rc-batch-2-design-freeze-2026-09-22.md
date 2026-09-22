# CAP POST-RC HARDENING — BATCH 2 DESIGN FREEZE

Date 2026-09-22. Input state: batch 1 `REMOTE VALIDATED`, batch 1.1 `RELEASE-GATE CLOSED` at
`0561a7eafe73c48b99c80eeca9b219616bbbf923`, sealed `v1.0.6-rc1` untouched, Batch 2 not started.

This stage produces a **design**, not a runtime change. Every statement below about a file is a
read of the tree as it stands, with a line number; nothing under `deployment/`, no Dockerfile, no
workflow and no product source was modified to write it (§A.3 proves that from the diff).

## A. Base SHA and immutable boundaries

### A.1 The base this freeze is anchored to

| Name | SHA | What it is |
| --- | --- | --- |
| Validated base | `0561a7eafe73c48b99c80eeca9b219616bbbf923` | batch 1.1's CI-validated tip; `origin/main` as read back after the second push (CI run `35689729682`, 10/10 jobs, junit `1664/0/0/133`) |
| Batch 2 design-freeze HEAD | `8879599` plus the `docs`-only commits carrying this report and its cross-references | the docstring correction required by step 1 of this stage's instructions, stacked on the validated base |
| Certified lines still standing | `b671f53` (Linux/GA/soak), `d30b4e7` (K8s) | the rounds the release gate walks back to today; the distances are re-measured per candidate, never assumed |
| Sealed line | `4d8f9c72b72dcea74e8588374fe8ef3a04564eb6` | `release/1.0.6-rc1`, the tag object `d0c7c7dc14cb075c095bd7970650b180a73afbf1` |

### A.2 Boundaries that do not move in Batch 2

- `v1.0.6-rc1` is immutable: no tag created, moved, deleted or re-pointed; no overwrite of the
  published `1.0.6-rc1` image tags (the image tag has no `v`; the git tag does); no edit to the
  published GitHub Release or its assets; no RC re-run, no promotion to `latest`. `v1.0.5` stays
  stable/`latest`. Re-verified read-only on 2026-09-22 after both batch 1.1 pushes: 12 tags,
  `/releases/latest` = `v1.0.5` (published `2026-09-07T14:59:44Z`), newest release `v1.0.6-rc1`
  (id `392662225`), `release.yml`'s newest run still `35553750674` at `4d8f9c72`, **zero**
  `event: release` runs, and all five served `1.0.6-rc1` digests identical to the record
  (batch 1.1 report §L.1).
- F-25's publication record stays CLOSED on run `35553750674`. Batch 2 does not re-open it; a new
  release line would carry its own.
- `scripts/release/classify_diff.py` is not modified. If it returns `RECERTIFICATION_REQUIRED` for
  something this plan classified as inheritable (or the reverse), the batch stops and the
  discrepancy is investigated — the classifier is the authority, not the paperwork.
- This stage dispatches nothing and tags nothing.

### A.3 Proof that the freeze itself touched no runtime surface

Measured with the real classifier, as a separate process, not by reading a summary:

| Range | Verdict | runtime_affecting | files |
| --- | --- | --- | --- |
| `0561a7e` → `ae990dd` (batch 1.1 report) | INHERITED | false | 4 (`docs`) |
| `ae990dd` → `d495d71` (GA-round addendum) | INHERITED | false | 2 (`docs`) |
| `d495d71` → `8879599` (the stale-docstring fix) | INHERITED | false | 1 (`backend/tests/test_release_publication_gate.py`, `test_harness`) |
| `0561a7e` → HEAD | INHERITED | false | 5 (4 `docs`, 1 `test_harness`) |

Measured at `8879599`. Everything this freeze adds above that commit is a `docs`-only page — this
report and its cross-reference corrections — and the claim is checkable in one command from the
named base: `python scripts/release/classify_diff.py 8879599 HEAD` must answer INHERITED listing
nothing but `docs` files. Any other answer would mean the freeze edited code it claimed not to
touch, and the freeze would be void, not merely wrong.

## B. Batch 2 scope, frozen

Six items, one candidate SHA. Each row states the change, the contract it creates, and what the
classifier will charge for it.

| # | Item | The change, precisely | Contract created | Classifier charge |
| --- | --- | --- | --- | --- |
| C | **F-24** compose digest pinning | 5 mutable `image:` references in `docker-compose.yml` (`:10 :27 :231 :247 :266`) become `repository@sha256:<index digest>`; `:101` already is one | compose can only pull immutable bytes; the human-readable tag survives as lock metadata, never as the pull reference | `deployment/`+`docker-compose` → **runtime-affecting** |
| D | **F-41** uniform digest-only support | `deployment/helm/cap/values.schema.json`: the two asymmetric coordinates adopt the four-coordinate `anyOf` shape; `digest` pattern tightened; `values.yaml` defaults drop `digest: ""` | all six coordinates accept tag-only *or* digest-only, refuse "neither/empty", and agree on precedence | `deployment/` → **runtime-affecting** |
| E2 | **F-37 step 2** tracked generator | new `scripts/release/third_party_registry_evidence.py` + its tracked output under `docs/quality/artifacts/` | the registry measurement becomes reproducible, machine-readable, credential-free and clone-readable | `certification_generator` + `docs` → inheritable |
| E3 | **F-37 step 3** repoint and delete the twin | the 5 `provenance.evidence` strings (`deployment/third-party-images.json:99 :128 :154 :183 :207`) name the tracked path; the tracked-twin allowance is removed from `test_third_party_image_lock.py` | a cited evidence path must itself exist and be git-tracked — no permanent twin workaround | `deployment/` → **runtime-affecting** |
| B2 | **Producer pin** | `.github/workflows/{ci,release}.yml` buildx/login refs pinned by SHA with an explicit buildx version and a digest-pinned BuildKit image; `scripts/release/build_release_image.sh` records the resolved producer; the 2 `# syntax=docker/dockerfile:1` directives pinned | what produced these bytes is named in the repository, not inferred from a runner's defaults | workflow/script → inheritable; **Dockerfiles → `deployment/` ⇒ runtime-affecting**, and §K adds a policy override anyway |
| B3 | Provenance assertion | **conditional** — only if the contract in §H is approved; otherwise removed from Batch 2 implementation | (if approved) the release gate asserts a designed provenance shape | gate = `.github/workflows/release.yml` → `ci_workflow`, but it changes publication behaviour ⇒ treated as runtime-affecting by policy |

## C. Explicitly excluded from Batch 2

- **B4** (GitHub/Sigstore signed attestation as a security decision) — stays its own approved
  decision, its own round. It is not a dependency of C, D, E2, E3 or B2, and it is not smuggled in
  through B3: §H says which part of B3 would need it and prices that.
- **F** — the 24 h multi-leg soak roll-up.
- **G(ii)–(v)** — deterministic metadata, hash pins, apt policy, pin remediation. G(i) stays
  PARTIAL as recorded; finishing it is its own comparison-job change.
- **Any `v1.0.6-rc1` mutation** — see §A.2.
- Anything that would make the runtime-affecting work a second candidate: the six items land on
  **one** SHA (§L).

## D. F-24 — compose digest inventory and the frozen contract

### D.1 What compose pulls today (read from the tracked file, verified against the registry)

Only one compose file is tracked: `docker-compose.yml`. Its six external references, with the
registry answer read on 2026-09-22 using the documented method (§D.2):

| Image | Line | Reference as written | Implied repository | Resolved index digest | digest_kind | Platforms | Provenance / evidence source | referenced_by |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| postgres | `:10` | `postgres:16-alpine` | `docker.io/library/postgres` | `sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea` (amd64 child `sha256:1a66d744c1b459e13b05a8fca341da84cb63383e99ce262210efee5a319d4551`) | manifest-list (multi-arch) | 8, one of them `unknown/unknown` | none today — `policy.postgres.form = "tag"`, `digest: null` | `docker-compose.yml`, all four certification workflows, `scripts/certification/setup.sh` |
| redis | `:27` | `redis:7-alpine` | `docker.io/library/redis` | pending P1 (§O.1) | manifest-list | 8 | **absent from the lock entirely** | `docker-compose.yml` only |
| prometheus | `:231` | `prom/prometheus:v2.55.1` | `docker.io/prom/prometheus` | `sha256:2659f4c2ebb718e7695cb9b25ffa7d6be64db013daba13e05c875451cf51b0d3` (round-trip verified) | manifest-list | 5 | absent from the lock | `docker-compose.yml` (profile `observability`) |
| grafana | `:247` | `grafana/grafana:11.3.1` | `docker.io/grafana/grafana` | pending P1 (§O.1) | manifest-list | 3 | absent from the lock | `docker-compose.yml` (profile `observability`) |
| pgadmin4 | `:266` | `dpage/pgadmin4:8` | `docker.io/dpage/pgadmin4` | `sha256:8a68677a97b8c8d1427dc915672a26d2c4a04376916a68256f53d669d6171be7` (round-trip verified) | manifest-list | 3 | absent from the lock | `docker-compose.yml` (profile `admin`) |
| minio | `:101` | `quay.io/minio/minio@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e` | `quay.io/minio/minio` | `sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e` (re-verified today: refetch by digest byte-identical, digest equals recompute, exactly one linux/amd64 child, 3 platforms) | manifest-list | 3 | `deployment/third-party-images.json` `minio-object-store` + `policy.minio_object_store` upgrade procedure | `docker-compose.yml`, kind clusters, GitHub service containers, `scripts/certification/setup.sh` |

`pending P1 (§O.1)` is not a placeholder left unfilled by carelessness: it is the measured fact that
**this host cannot resolve Docker Hub on demand**. One window at 14:4x resolved all five Hub
references with the round-trip check passing, and three of those values are recorded above; a later
window at 15:0x–15:2x failed every one of them at the TLS layer (`RemoteDisconnected`,
`SSL: UNEXPECTED_EOF_WHILE_READING`, handshake timeouts) against both `auth.docker.io` and
`registry-1.docker.io`, and the box's own proxy does not help — it also fails those hosts (§O.1).
`quay.io` answered in both windows. What is left pending is therefore an implementation-time input,
and in every case **the generator's tracked file is what the compose edit cites, not this table**:
three of these references sit on rolling tags, so a digest is only meaningful as the output of a run
that can be reproduced and dated, which is exactly what §F.1's contract produces and a freeze-time
observation cannot claim to be.

Two honest addenda to that:

- **What the successful window did prove, and what it kept.** For every Hub reference that resolved,
  the answer was an index, the refetch by digest returned byte-identical content, the served digest
  equalled the recomputed one, and each index had exactly one `linux/amd64` child — so §D.2's method
  is verified against these repositories, not copied from documentation. Three of the five values are
  in the table above (postgres with its child, prometheus, pgadmin4). The other two, `redis` and
  `grafana`, resolved in that same window and their values were lost when
  a failing retry run overwrote the scratch JSON mid-correction of the script; they are reported as
  pending rather than reconstructed from memory of a truncated print, because a pin has to come from a
  record, not from a recollection. Either way P1 stands: **the generator's tracked file is the
  authority**, and these values are a freeze-time observation that the method works — not the
  artifact the compose edit will cite.
- **What pinning the index, rather than a child, preserves.** A compose host still resolves its own
  platform out of the frozen index, so `linux/amd64` users get amd64 bytes and arm64 users get
  arm64 — the *set* becomes immutable, the architecture does not. And the mobility the freeze is
  removing is not uniform across the six: `dpage/pgadmin4:8` is a bare major and the most volatile of
  them, `postgres:16-alpine` and `redis:7-alpine` are rolling minor lines that receive upstream
  security rebuilds (so pinning them stops that flow, which is the cost §D.3 names and the
  upgrade procedure pays), while `prom/prometheus:v2.55.1` and `grafana/grafana:11.3.1` are point
  releases upstream does not normally move — pinned for completeness and for a verifiable provenance
  chain, not because they were expected to drift under anyone.

### D.2 The documented read method (what the generator must do, and what already proves it)

1. Request the tag with `Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json`
   and nothing wider. A bare request can be served a legacy schema1 *conversion* whose digest
   differs — the trap `policy.minio_object_store.upgrade_procedure` already documents.
2. Take `Docker-Content-Digest` as the index digest; recompute `sha256(body)` and require equality.
3. **Refetch by that digest and require the bytes to be identical.** A tag is a pointer; this is
   the only cheap proof the digest names what the tag serves. (Verified end to end for MinIO today;
   it passed for all five Hub images in the window when Hub answered.)
4. Parse the index and name the `linux/amd64` child separately. **Exactly one** is required: two
   children (e.g. `v6`/`v7` variants) is ambiguity and must fail the run, not resolve to `next(...)`
   — which is precisely how `_tmp/verify_base_digests.py:76-83` behaves today, and why it stays
   scratch rather than becoming the generator as-is. This is not hypothetical: `postgres:16-alpine`
   resolved this session as an index of **eight** entries, one of them `unknown/unknown`, so an
   unfiltered platform enumeration both miscounts the architectures and can mistake an attestation
   entry for a build target. The accepted record filters `unknown/unknown` out of the platform list
   and still demands a single `linux/amd64` child.
5. Distinguish `manifest-list (multi-arch index)` from a single manifest, and refuse to pin the
   latter as if it were an index digest.
6. Auth is challenge-driven: read the registry's own `WWW-Authenticate` realm/service/scope; never
   serialize the token, never emit response headers, `Date`, or `Content-Type`-derived noise into
   the evidence file.
7. Record `verified_on` **separately** from the digest assertions, so two copies of one measurement
   taken on different days remain comparable (this is the distinction `_digest_claims()` in
   `test_third_party_image_lock.py:268-285` already makes, and which the twin copies' one differing
   date field proved necessary).

### D.3 The frozen decision

- **compose carries `repository@sha256:<index digest>`, never a tag.** The index digest (not the
  platform child) is what preserves today's behaviour: a compose user on arm64 keeps getting the
  arm64 child, and the multi-arch set is fixed rather than the single-arch bytes.
- **The human-readable tag stays as lock metadata only**: the lock entry keeps `tag` (what the
  digest was resolved *from*) and gains `pinned_ref` semantics via `image_ref` becoming the
  `repository@digest` string. `docker-compose.yml` carries a comment per service naming the tag the
  digest stands for, the way `:96-99` already does for MinIO.
- **Postgres' recorded "tag on purpose" policy is reversed deliberately**, and this is the one place
  where Batch 2 overrides a prior decision rather than filling a gap:
  - `deployment/third-party-images.json` `policy.postgres.form` goes from `"tag"` to
    `"repository@digest"`, and its `why` is rewritten to the truth of now: the reason it was tagged
    was that no digest could be resolved from the audit host (see
    `test_postgres_entry_is_tag_pinned_on_purpose`, `backend/tests/test_third_party_image_lock.py:546-556`),
    which is a measurement-transport fact, not a policy argument — and its own
    `upgrade_procedure` already says "resolve and record the digest here".
  - The guard test is **replaced, not deleted**: it becomes one case of the §D.4 matrix (a locked
    digest with a `name:tag` reference on a surface is a failure), so the exception cannot silently
    re-open.
  - The price is stated, not hidden: pinning `16-alpine` to a digest stops upstream patch flows for
    that compose deployment, and the version-matrix certification (§6 of the release checklist)
    covers Debian-suffix variants rather than this alpine digest. The mitigation is procedural and
    already exists in the file: upgrades are deliberate, generator-first, then the matrix re-runs.
- **The four images missing from the lock are added** (`redis`, `prometheus`, `grafana`,
  `pgadmin4`) with the existing entry key set, so `referenced_by` answers "which images does compose
  pull" for every service rather than for two of six.
- `_drift()` already reddens "uses a mutable tag where the lock pins a digest", so the enforcement
  mechanism exists from the moment the lock carries digests — the compose edit and the lock edit
  must land in the same commit, or CI fails between them.

### D.4 The future test contracts (each with the control that proves it bites)

| # | Contract | Negative control that must fail |
| --- | --- | --- |
| 1 | every external compose image reference is immutable — `image:` is `repository@sha256:<64 hex>` (a tag on the same line is a failure) | replace one pinned ref with `postgres:16-alpine` in a copy of the file → the check names that service |
| 2 | every external compose image is represented in the lock (set difference compose → lock is empty) | delete the `redis` entry from a copy of the lock → failure names `redis` |
| 3 | every lock entry whose `referenced_by` names a compose file is actually referenced there (set difference lock → compose is empty) | add a sixth bogus image entry with `referenced_by: [docker-compose.yml]` → failure names it |
| 4 | mutation in either direction fails: lock digest ≠ compose digest, compose carries a tag the lock pinned, a reference site edits without the lock, or the lock edits without the site | change only one side of a pair, in both directions, as separate controls |
| 5 | the digest in the lock is the **index** digest, and its recorded `digest_kind` says so; a platform-manifest digest is refused | substitute the recorded `linux/amd64` child digest → failure says "platform digest where an index digest is required" |

Contract 5 is the one that keeps a future operator from "fixing" a pin by pasting the child digest
from `docker inspect`, which is exactly the mistake the Accept-header note in §D.2 exists to prevent.

## E. F-41 — the Helm schema and rendering contract

### E.1 The asymmetry, measured

`deployment/helm/cap/values.schema.json` today holds two shapes:

| Coordinate | Lines | `required` | Digest path |
| --- | --- | --- | --- |
| `backend.image` | `29-49` | `["repository","tag"]` | `digest` property exists, pattern allows `^$`, **no `anyOf`** → tag is mandatory |
| `worker.image` | `98-118` | `["repository","tag"]` | byte-identical to the above |
| `worker.sandbox.image` | `161-202` | `["repository"]` | `anyOf` at `180-201`: tag **or** digest |
| `worker.sandbox.browserImage` | `203-244` | `["repository"]` | `anyOf` at `222-243` |
| `frontend.image` | `286-327` | `["repository"]` | `anyOf` at `305-326` |
| `egressProxy.image` | `391-432` | `["repository"]` | `anyOf` at `410-431` |

`cap.imageRef` (`deployment/helm/cap/templates/_helpers.tpl:42-52`) resolves the conflict already:
`{{- if $image.digest -}}` → `repository@digest`, and the tag branch is not taken at all, so **when
both are set the digest wins and the tag is ignored, silently**. `digest: ""` is Go-falsy, which is
why the shipped `values.yaml` defaults (`:29-31 :79-81 :115-121 :152-154 :205-207`, each
`tag: "1.0.6-rc1"` + `digest: ""`) work today.

The released file an operator installs with is generated, and it sets **all three** keys for all six
coordinates: `release.yml:984-1003` writes `repository`, `tag` and `digest: record["index_digest"]`.
So "tag + digest together" is not a hypothetical — it is the shipped shape, and the digest branch is
what actually renders.

### E.2 The frozen contract

For each of the six coordinates, uniformly:

- `repository` — **required**, `minLength: 1`.
- exactly one of `tag` / `digest` must be *usable*, expressed as the existing
  `anyOf: [{required:[tag], properties:{tag:{minLength:1}}}, {required:[digest], properties:{digest:{minLength:1}}}]`.
- `tag` keeps `pattern: ^[A-Za-z0-9][A-Za-z0-9._-]*$` (it already rejects `""`).
- `digest` becomes `pattern: ^sha256:[0-9a-f]{64}$` — dropping both the `^$` alternative and the
  loose `{16,}` length, so a malformed or truncated digest is refused instead of rendered.
- **Semantics when both are present: PASS, digest wins, tag is metadata.** That is what
  `cap.imageRef` already does and what the renderer already emits; freezing it means the schema
  permits it and a test states it, rather than an operator discovering it by reading a template.
- Because `digest` no longer admits `""`, **`values.yaml` must stop writing `digest: ""`** at all six
  coordinates — otherwise `helm lint` in the `packaging` job reddens on the chart's own defaults.
  Omitting the key is the schema-legal form of "resolve by tag".

Two constraints that decide *how* the schema may be written, both measured:

1. `test_release_image_completeness.py:1069` pins `SCHEMA_KEYWORDS = {type, required, properties,
   anyOf, minLength, pattern}` and `_assert_matches_schema` **fails loudly** on any keyword outside
   that set (`:1084-1088`). So the unified form must be the existing `anyOf` shape — introducing
   `oneOf`, `not` or `additionalProperties` requires extending that checker in the same commit, and
   that extension is a deliberate act with its own controls, not a quiet widening.
2. The same checker treats a `required` key whose value is `""` as missing (`:1097-1100`), so the
   "empty string must fail" cases are enforced twice over: by the pattern, and by the checker's own
   reading of `required`.

### E.3 The test matrix, and where each row is proved

| Input | Expected | Where proved |
| --- | --- | --- |
| `{repository, tag}` | PASS, renders `repository:tag` | `_assert_matches_schema` for all six + `helm template` assertion |
| `{repository, digest}` | PASS, renders `repository@digest` | same, for all six — this is the case the two asymmetric coordinates refuse today |
| `{repository, tag, digest}` | PASS, **renders the digest**, tag ignored (documented) | `_assert_matches_schema` + `helm template` image-line equality |
| `{repository}` only | FAIL (`required`) | `_assert_matches_schema` raising for each of the six |
| `tag: ""` | FAIL | pattern (`^[A-Za-z0-9]…`) + the checker's empty-required rule |
| `digest: ""` | FAIL | the tightened pattern; plus a defaults test that `values.yaml` no longer emits the key |
| `digest: "sha256:deadbeef"` / `"sha512:…"` / `"…"` 63 hex | FAIL | pattern control cases, one per coordinate shape |

**JSON Schema alone is not the check.** The plan requires `helm lint` + `helm template` rendering
tests, and the honest constraint is that **no unit test in this repository shells out to helm** —
every current chart assertion parses YAML/JSON in Python, and real `helm` runs only in the cluster-
and packaging-gated places: the `packaging` job (`ci.yml:191-230`) installs
`azure/setup-helm@v4` and runs `helm lint`, `helm template … > rendered-cap.yaml` and
`helm package`, and `release.yml:1055-1060` and the certification workflows lint/install as well.
This host has no `helm` binary, so these tests cannot be prototyped locally — they are written for
the `packaging` job and iterated there.

Frozen placement: a new step in `ci.yml`'s `packaging` job (the same job that already renders) that
takes the **generated released-values shape** (repository+tag+digest for all six, as
`release.yml:991-1002` emits it) and a digest-only variant, runs `helm lint -f` and `helm template`
for both, and asserts the rendered `image:` lines equal `repository@digest` — and, for the tag-only
variant, `repository:tag`. Rendering assertions belong in the job that has the binary; the Python
matrix above stays as the fast, offline half. The F-41 assertion inside
`test_the_released_values_satisfy_the_chart_schema` (`:1177-1179`, which today *requires*
`backend.image` to refuse a digest-only pin) is inverted in the same commit, with its comment
rewritten to say the asymmetry is closed rather than "recorded as F-41 rather than fixed".

## F. E2 / E3 — the evidence contract

### F.1 E2: the tracked generator

| Property | Frozen decision |
| --- | --- |
| Path | `scripts/release/third_party_registry_evidence.py` — `certification_generator`, inheritable, matching the plan's §E step 2 wording |
| Tracked output | `docs/quality/artifacts/registry-resolution/third-party-registries.json` — inside `EVIDENCE_ROOTS` as the existing test defines it (`test_third_party_image_lock.py:227`) |
| Method | §D.2, verbatim; the generator's docstring names the Accept list and the round-trip rule so the file and the evidence cannot drift apart |
| Determinism | pure stdlib, no third-party imports; `json.dump(..., indent=1, sort_keys=True)`; explicit `newline="\n"` (the repo has **no `.gitattributes`** and `core.autocrlf=true`, and `git ls-files --eol` already reports `i/lf w/crlf` for the lock and its twin, so a generator that opens `"w"` on Windows would write CRLF into tracked evidence); targets iterated from the lock's own order, never from a directory walk; no `Date`/`Content-Type`/exception-text fields in the output |
| Time vs claim | top-level `verified_on` records when the read happened; digest assertions live in a separate `images[]` block, and the comparison rule (`_digest_claims`) is date-blind by design |
| Credentials | anonymous + challenge-driven pull tokens only, used in a header and never written; nothing reads `~/.docker/config.json`, the git credential manager, or any env secret; a control asserts no `sha256-…` header dump and no `Authorization` substring lands in the file |
| Ambiguity = failure | exits non-zero and writes nothing partial when: the answer is not an index; more or less than one `linux/amd64` child; `Docker-Content-Digest` absent or unequal to the recomputed digest; the by-digest refetch differs in bytes; the platform set lacks the arch the certification matrix runs; two targets resolve to the same digest where they should not |
| Fresh clone | runs with only `python` and network to the registries; its own output path is created by it; and re-running it for unchanged bases must produce a byte-identical file except `verified_on` — that equality is E2's acceptance test |
| Partial state | writes to a temp file and renames once, so an interrupted run cannot leave a half-written tracked artifact |

Scope note: the generator covers **every** third-party image the lock names (the five Dockerfile
bases it replaces evidence for, the six compose images of §D, and minio), because a generator that
covers only some entries re-creates the "lock says nothing about them" hole F-24 documents.

### F.2 E3: repoint, then delete the workaround

1. The five `provenance.evidence` strings (`deployment/third-party-images.json:99 :128 :154 :183
   :207`) become `docs/quality/artifacts/registry-resolution/third-party-registries.json`. That is
   the **only** `deployment/` line E3 adds beyond the entries E2/F-24 already requires.
2. Then, in the same commit so no state exists where the twin is both unnecessary and still
   permitted:
   - delete `_tracked_copies()`' basename matching (`:297-303`) and the `measured is None`
     allowance (`:330-331`) that lets the cited path be absent;
   - delete the transitional prose block (`:211-226`) that says the tightening "is the second half
     of F-37, not this file's";
   - retarget the three twin-shaped controls (`:402`, `:410`, `:425`) onto the new rule;
   - replace the contract with: **the cited path exists in the checkout, is returned by
     `git ls-files`, and the digests it asserts are exactly the digests the entry asserts** — one
     requirement, no permanent twin, and `_digest_claims()` is what compares them.
3. `EVIDENCE_ABSENT_JUSTIFICATION` (`:236-239`) shrinks by construction: postgres' justification is
   reversed by §D.3, and minio's stays because it cites a vendor-signed tag and names its own
   `open_gap`. The set-comparison control that stops the convention widening by silence is kept.

## G. B2 — the producer pin contract

### G.1 The surface as it actually is (measured)

- `docker/setup-buildx-action@v3` at `ci.yml:277`, `ci.yml:332`, `release.yml:680`, `release.yml:718`
  — **four references, mutable major tag, and not one of them carries a `with:` block**, so driver,
  driver-opts, buildkitd flags, buildx version and the BuildKit image are all whatever the action
  resolved on the day.
- `docker/login-action@v3` at `release.yml:681 :719 :765`. `docker/build-push-action` and
  `docker/setup-qemu-action` are **absent**: builds are driven by
  `scripts/release/build_release_image.sh` (`:128` plain `docker build`; `:137`
  `--provenance=true --sbom=true --push`; `:139` `--provenance=false --sbom=false --load`;
  `:142-143` `docker buildx build … --metadata-file`; `:161` `imagetools inspect`).
- Exactly one third-party action in the whole repo is SHA-pinned today:
  `aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25` (`ci.yml:286 :380`,
  `release.yml:770`), with the rationale written at `ci.yml:285`. Everything else — checkout v6,
  setup-python v6, setup-uv v6, upload/download-artifact v4, setup-node v6, setup-helm v4 — is a
  mutable major tag.
- Dockerfile frontends: `backend/Dockerfile:1` and `frontend/Dockerfile:1` are
  `# syntax=docker/dockerfile:1` (mutable tag, not digest-pinned);
  `backend/docker/egress-proxy/Dockerfile`, `.../sandbox-http/Dockerfile` and
  `.../sandbox-browser/Dockerfile` declare **no** directive and so use the frontend embedded in
  whatever BuildKit the runner pulled.
- Coupling: **every Dockerfile is built by two producers.** CI's `release-image-builds` matrix builds
  all five — with `--local-docker`, i.e. the *docker* driver (`ci.yml:366`), plus one buildx
  prerequisite build of `sandbox-http` (`:358-362`) — and `release.yml` builds four by buildx
  `--push` (`:657-705`) plus the browser image on the just-published base (`:713-744`). The
  certification workflows build the same `backend`/`frontend`/`egress-proxy` Dockerfiles again with
  raw `docker build` (`cap-ga-certification.yml:105,106,441,442`,
  `cap-ga-reliability.yml:99,100`, `cap-k8s-certification.yml:78,79`,
  `cap-linux-certification.yml:107`).
- Nothing records the producer: `docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md:39-40`
  counts buildx version and BuildKit/driver instance as **0 of 17** recorded, and the sealed
  attestation contains no BuildKit or buildx version at all (V1, §H).
- No test pins any of these strings today; the nearest ones assert *flags*:
  `test_release_build_script.py:254` (`"--load" in calls and "--provenance=false" in calls`) and
  `:275` (`"--provenance=true --sbom=true --push" in calls`), plus `test_release_build_script.py:235`
  on `build_driver`. So a pin change breaks nothing today, while a *flag* change breaks those two
  assertions — which is the asymmetry §G.3's controls have to fix.

### G.2 The pinning strategy, frozen

1. **Action refs by full commit SHA**, with the version in a comment, following the trivy precedent
   already in the file: the four `setup-buildx-action` and three `login-action` references. Other
   actions are out of scope for Batch 2 (they do not produce image bytes) and are recorded as a
   standing observation rather than silently widened.
2. **Explicit buildx version** on each setup step (`buildx-version: v0.x.y`), so the CLI is named.
3. **BuildKit image by digest** — the `docker-container` driver's image set explicitly
   (`driver-opts: image=moby/buildkit:v0.x.y@sha256:…`) rather than inherited from an action default,
   and the same digest recorded in the lock as a new `builders[]`-style entry so the producer is
   *locked* like every other third-party input, and E2's generator can resolve it.
4. **Frontend directives pinned where a directive exists** (`# syntax=docker/dockerfile:1.x.y` +
   digest). The three Dockerfiles with no directive **keep no directive**: adding one changes what
   parses the Dockerfile and is a second, independent build-behaviour change; it is recorded in §O.5
   as its own decision, not slipped in beside a version pin.
5. **Record what was used.** `build_release_image.sh` gains a producer block in its evidence JSON —
   buildx version, BuildKit image digest, driver, runner image — because pinning without recording
   leaves the same audit blind spot §G.1 documents. The evidence fields are asserted, not merely
   printed.

### G.3 Impact assessment (the four the instructions require)

| Axis | Assessment |
| --- | --- |
| CI compatibility | A pinned buildx/BuildKit must exist for the runner's architecture and support the flags already used (`--metadata-file`, `--provenance`, `--sbom`, `--load`); the `--local-docker` cells do not use buildx at all, so pinning the builder does not make CI's five matrix builds reproducible — it makes the *published* builds' producer named. Naming that limitation is part of the contract: today CI proves buildability with the docker driver, and says so in its own evidence. |
| Provenance shape | The sealed SLSA v1 predicate carries no buildx/BuildKit version today, and pinning does not add one: the fields that change are the ones BuildKit itself writes (`resolvedDependencies` when base digests change). Any assertion about builder identity is §H's business, not B2's. |
| SBOM shape | SBOM digests may move (the layer set is unchanged; the syft/trivy view of the build can differ across a BuildKit version). The supply-chain gates compare findings, not SBOM byte equality, so a shift is visible but not blocking — and it must be *reviewed*, which is why B2 joins the recert round. |
| Image digests | **They will change.** Every pinned producer or frontend difference alters the five CAP images' digests at the candidate SHA, and `release.yml`'s values renderer and the completeness gate derive from those digests. This is the strongest reason B2 cannot be treated as a CI-only change even though the classifier calls a workflow-only diff inheritable. |

**Policy, independent of the classifier:** whatever `classify_diff.py` returns for B2's paths
(`ci_workflow` inheritable for workflow edits; `deployment` runtime-affecting for Dockerfiles),
Batch 2 treats B2 as **producer-affecting** and it joins the common re-certification round at the
candidate SHA. §K states how that is checked rather than assumed.

## H. B3 — provenance contract alternatives and the decision required

### H.1 The CURRENT OBSERVED CONTRACT (measured, not inferred — `cap-provenance-identity-observation-2026-09-21.md` §6)

For all five sealed images: a `spdx.dev/Document` and an `slsa.dev/provenance/v1` artifact exist;
`predicateType` is `https://slsa.dev/provenance/v1`; the attestation **subject is the platform
manifest digest**, not the index digest; `runDetails.builder.id` is **the empty string on every
image**; BuildKit self-declares `buildkit_completeness = {"request": true,
"resolvedDependencies": false}`; `vcs.revision` is present on only **2 of 5** (backend, frontend —
the two that pass the VCS build args), and `resolvedDependencies` counts (2 vs 4) track exactly
which; **no BuildKit or buildx version appears anywhere in any predicate**; there is **no**
`sha256-….sig`/`.att` tag for these images and `ghcr.io/v2/<name>/referrers/<index-digest>` answers
**404**, so the pushed attestations are unsigned OCI artifacts; and `gh attestation verify` was
deliberately never run, because reading an artifact is not verifying a signature.

**A B3 that asserted `builder.id == ""` would be an assertion that a field stays unpopulated** — it
can never fail for the right reason and it locks the accident in. It is excluded by design, as the
plan already requires.

### H.2 Two candidate DESIRED contracts

| Question | **B3-A — BuildKit-native content assertion** | **B3-B — signed workflow attestation, then assert** |
| --- | --- | --- |
| 1. Must builder identity be non-empty? | **No.** Identity is carried by content binding, not by `builder.id`; the assertion set is `subject` + `buildType` + `resolvedDependencies` + `vcs.*`. | **Yes**, and it is supplied by the signature, not by `builder.id`: the verified certificate identity is the workflow ref + repo + OIDC issuer. `builder.id` may still be empty and is then asserted *absent-or-equal-to-a-declared-value*, never "== ''". |
| 2. Authoritative source | BuildKit's own SLSA v1 predicate, read from ghcr by digest. | The GitHub-issued signed attestation (buildx `--attest`/`mode=min` under an OIDC token, or `cosign sign-attestation` over the BuildKit predicate) — with BuildKit's predicate still the payload. **Both**: BuildKit for content, GitHub for identity. |
| 3. Machine verifier | Registry HTTPS + digest equality + recompute (the §D.2 round-trip). No cryptographic verification: nothing signs these artifacts today, and `referrers` 404s. | `cosign verify-attestation` (or `gh attestation verify`) pinned to `--certificate-oidc-issuer https://token.actions.githubusercontent.com` and `--certificate-identity-regexp` for this repo's release workflow. That binary is **not installed in the release gate today** — the gate is a Python heredoc in `release.yml`; adding a verifier is a new tool dependency in the publication path, priced in §H.4. |
| 4. Subject binding | The **index digest**, with the platform child recorded as a derived fact: the index is what `repository@digest` resolves to and what F-24/D pin (so an assertion on the platform digest would not match the pin an operator uses). Requires the current platform-digest subject to *change*, i.e. new builds. | Same target, and the signature must cover the index-to-child relationship the registry's referrers/`manifests[]` state, or the gate must verify the child by digest and then the index — the two-step §D.2 already performs. |
| 5. Failure blocks publication? | **Yes** for the content assertions that are true of a correct build today (subject/`buildType`/`resolvedDependencies` completeness/`vcs.revision` on the images that must carry it), exactly like F-33's `AuthorityRejected` → exit 1. An unreadable or ambiguous artifact is a refusal, and a control-flow error stays `ERROR`, never a PASS. | **Yes**, and stricter: a missing signature, an identity that is not this repo's release workflow, or a verifier that cannot run at all, refuses publication (fail-closed). That is the whole point of B3-B and also its cost: it can only land after B4's tooling decision, because the signature has to be produced somewhere. |
| What it buys | Refuses an unsigned, wrong-subject, or VCS-blind round on evidence, from the current producer surface. Closes the "2 of 5 carry `vcs.revision`" gap by demanding it of all five. | Plus cryptographic proof of *who* built it, which nothing today establishes — the sealed attestations are unsigned artifacts in a registry we log into with a pull scope. |
| What it costs | Zero new tooling; but its assurance is transport + content, and §H.1's `resolvedDependencies: false` self-declaration means the dependency list must be asserted against *our* lock, not trusted from BuildKit. | New pinned verifier in the publication job, a signing path in `release.yml` (OIDC claims, `id-token: write` — a **write-permission** change to the release job, which is why it is B4-adjacent), and a re-certification that produces signatures which do not exist for any past release. |

### H.3 Recommendation and the shape of the approval asked for

Recommend **B3-A in Batch 2**, and B3-B not at all in this batch: it changes the publication job's
permissions and introduces a verifier dependency, which is §H.2's last row — a decision the user
reserved as B4-adjacent. B3-A already refuses the failure modes that have actually been observed
here (a development round passing as certification is F-33's; a green round whose release job never
ran is F-42's; a round whose provenance names no VCS revision or whose subject is the wrong digest
is B3-A's).

Approval needed, expressed as five answers, is exactly the instruction's list: (1) non-empty
builder identity required? (2) which artifact is authoritative? (3) which verifier, and where does it
run? (4) index or platform digest as subject? (5) does an assertion failure block publication?

### H.4 If it is not approved

B3 is **removed from Batch 2 implementation scope** and C / D / E2 / E3 / B2 proceed on their own:
none of them reads or writes the attestation predicate, and the release gate's current authority
rules (§A.2, F-33/F-42) already hold publication without it. This is a designed independence, not an
optimistic one: §K's classification is computed both with and without B3's files, and §M's execution
order has no step that waits on B3. B4 stays independent either way.

## I. Files expected to change

| Item | File | Classifier category |
| --- | --- | --- |
| C | `docker-compose.yml` (5 references + comments) | `deployment` (runtime-affecting) |
| C | `deployment/third-party-images.json` (4 new entries, digests, `policy.postgres` reversal) | `deployment` |
| C | `backend/tests/test_third_party_image_lock.py` (contracts 1-5, postgres guard replaced) | `test_harness` |
| D | `deployment/helm/cap/values.schema.json` (6 coordinates unified, digest pattern tightened) | `deployment` |
| D | `deployment/helm/cap/values.yaml` (`digest: ""` removed at 6 sites) | `deployment` |
| D | `backend/tests/test_release_image_completeness.py` (F-41 assertion inverted; matrix added) | `test_harness` |
| D | `.github/workflows/ci.yml` `packaging` job (helm lint/template render step) | `ci_workflow` |
| E2 | `scripts/release/third_party_registry_evidence.py` (new) | `certification_generator` |
| E2 | `docs/quality/artifacts/registry-resolution/third-party-registries.json` (new, generated) | `docs` |
| E2/E3 | `backend/tests/test_third_party_image_lock.py` (generator acceptance + twin deletion) | `test_harness` |
| E3 | `deployment/third-party-images.json` (5 `provenance.evidence` strings) | `deployment` |
| B2 | `.github/workflows/ci.yml`, `.github/workflows/release.yml` (action SHAs, buildx version, BuildKit image) | `ci_workflow` |
| B2 | `backend/Dockerfile`, `frontend/Dockerfile` (`# syntax=` pinned) | `deployment` |
| B2 | `scripts/release/build_release_image.sh` (+ its evidence block), `backend/tests/test_release_build_script.py` | `certification_generator` / `test_harness` |
| B2 | `deployment/third-party-images.json` (the builder entry the producer digest belongs in) | `deployment` |
| B3 (if approved) | `.github/workflows/release.yml` gate heredoc, `backend/tests/test_release_publication_gate.py` | `ci_workflow` / `test_harness` |
| Housekeeping | `docs/known-issues.md` (F-24, F-41, F-37 items), `CHANGELOG.md`, this plan's status rows | `docs` |

Nothing here touches `backend/app/**`, `console/**`, `deployment/postgres/**`, or any Alembic
revision: Batch 2 is supply-chain and deployment-contract work, not product behaviour.

## J. Test matrix (the union Batch 2 must land green)

1. §D.4's five F-24 contracts, each with its named negative control.
2. §E.3's seven schema cases × the two coordinate shapes that exist today (so a regression in the
   already-`anyOf` four is caught too), plus the rendering assertions from real `helm lint` /
   `helm template` in the `packaging` job, plus a defaults test that `values.yaml` no longer emits
   `digest: ""`.
3. E2: re-run reproducibility (byte-identical except `verified_on`); each ambiguity rule in §F.1
   turned into a failing control (two amd64 children; non-index answer; digest ≠ recompute; refetch
   differs; a header/token string appearing in the output; an interrupted run leaving a partial
   file).
4. E3: the cited path must exist and be tracked (an untracked-only citation fails); the digests must
   match the entry's; **and a control that re-introduces a basename twin under
   `docs/quality/artifacts/` must now FAIL**, which is what proves the transitional allowance is
   really gone rather than merely unused.
5. B2: exact-equality assertions on the pinned refs (`uses:` strings, `buildx-version`, the BuildKit
   `image=…@sha256:`), on the `# syntax=` directives, and on the new producer block in the build
   evidence — plus the two existing flag assertions updated to the new command lines and kept
   asserting the attestation contract they guard.
6. B3 (only if approved): the assertion set against a real captured predicate, plus per-assertion
   controls, plus the "verifier could not run is not a PASS" control the gate's ERROR path already
   models.
7. Existing guards that must stay green without edit: `test_release_image_completeness.py`'s chart ↔
   release set equality and "no `:latest`" scan, `test_quality_gate_parity.py`,
   `test_deployment_doc_contract.py`, `test_secret_scan.py`, `test_release_diff_classifier.py`,
   and the whole `test_release_publication_gate.py` F-33/F-42 set (Batch 2 must not weaken a single
   one of them; §M step 7 checks them against live state).

Every new guard is required to carry its own mutation control: a guard that no control reddens is
documentation, not a test. That rule has caught real things here already (the batch 1.1 controls
reddened 19 and 11 tests for the two F-42 mutations).

## K. Classifier expectations

| Commit | Expected verdict | Why that, and what would be a surprise |
| --- | --- | --- |
| C (compose + lock + tests) | **RECERTIFICATION_REQUIRED**, `runtime_affecting=true`, blocking file `docker-compose.yml` / `deployment/third-party-images.json` | `deployment` is a blocking category (`classify_diff.py:78-80, 89`) and `docker-compose` is named in the same rule. An INHERITED here would mean the classifier stopped seeing deployment paths — stop, do not proceed |
| D (schema + values + tests + one workflow step) | **RECERTIFICATION_REQUIRED** (`deployment` present) | a mixed commit is fine and expected; the verdict must be driven by the `deployment` files, not diluted by the `ci_workflow` one |
| E2 (generator + artifact + tests) | **INHERITED** (`certification_generator` + `docs` + `test_harness`) | this is the inheritable half of F-37; if the generator's path classified as runtime-affecting, the path choice is wrong, not the classifier |
| E3 (5 pointer strings) | **RECERTIFICATION_REQUIRED** | the only runtime change in E3 is those 5 lines — priced exactly as the plan's §E step 3 says |
| B2 (workflows + Dockerfiles + script + lock) | **RECERTIFICATION_REQUIRED** because of the Dockerfiles | and **even a workflow-only variant of B2 is executed as producer-affecting by Batch 2 policy** (§G.3): it joins the recert round, and §M's ordering makes that explicit rather than relying on the classifier to notice |
| B3 (if approved) | `ci_workflow` + `test_harness` ⇒ INHERITED **by classification**, publication-affecting **by nature** | which is why its execution belongs inside the same candidate round (§M), and why a separate "just land the gate change" CI-only batch would be a mistake this plan forbids |
| Cumulative `4d8f9c7 → candidate`, `b671f53 → candidate`, `d30b4e7 → candidate`, `0561a7e → candidate` | **RECERTIFICATION_REQUIRED** with `runtime_affecting=true` | the whole point: certification is re-earned at one SHA. Any INHERITED on this cumulative range would mean Batch 2's runtime edits never landed, and the "recertified" claim would be about a line that changed nothing |

The with/without-B3 pair is computed as two ranges (`… → candidate-without-B3` and
`… → candidate-with-B3`) so §H.4's independence claim is a measurement rather than a sentence.

## L. Candidate-SHA strategy

1. Commit order within Batch 2, one item per commit, so each is revertible alone:
   **E2 → C → E3 → D → B2 → (B3)**, docs/housekeeping last.
   - E2 first because C's compose digests and E3's pointers both consume its output.
   - C is one commit (compose + lock + tests) because `_drift()` fails on a half-changed pair.
   - E3 follows C: the lock's entry set must be final before the pointers name the file that
     measures them.
2. **Preconditions before the candidate is declared:**
   - **P1** — the generator has produced its tracked file with real digests for all six compose
     images (not from this host necessarily; §O.1 prices that), and its re-run reproduced it.
   - **P2** — the §J test matrix is green locally, and the `packaging` job's helm render assertions
     have been iterated in CI (no helm on this host).
   - **P3** — if B3 is unapproved, its files are absent from the range, and the §K B3 row is marked
     out of scope rather than deleted.
3. The **candidate SHA is recorded in the report before any dispatch**, and it is the *last* commit
   in the tree the certification runs against: no docs commit may follow it until the round is
   over. Batch 1.1 learned this the measurable way — the two dry-run passes at `7da216c` differed by
   one commit's distance (`+40` vs `+41`, §J of that report) purely because a report commit landed on
   top of the SHA being resolved.
4. Local stack before the candidate is cut is reconciled first: `ae990dd`, `d495d71`, `8879599` (and
   this freeze's own commits) sit on `0561a7e`; `origin/main` is at `0561a7e`, so the push of the
   report/docstring line is a precondition of a candidate that anyone else can fetch.
5. Nothing about the candidate is time-boxed. It is cut when P1-P3 hold; capacity estimates (§M) are
   for scheduling, and §N shows what happens when a class fails.

## M. Certification execution order at the candidate SHA

All six classes run against **one** SHA. `push` events and `workflow_dispatch` inputs are named
because F-33's authority reads the round's own artifact and requires `commit == run.head_sha`.

| Step | Class | How it is started | What must be true |
| --- | --- | --- | --- |
| 1 | CI | push of the candidate to `main` (plain fast-forward; no force) | 10/10 jobs success, junit tally reported, the two live Actions/API checks executed and green (batch 1.1 §K.1), helm render step green |
| 2 | Linux production certification | push-triggered `cap-linux-certification.yml` **and** the production layer it must actually execute | the release job set is *executed*, not skipped — this is the exact shape F-42 now refuses (§J.1 of the 1.1 report), so the candidate's own Linux round must show `cap-production-certification: success` |
| 3 | K8s certification | `cap-k8s-certification.yml` at the candidate | uploads `k8s-cert-artifacts` whose `commit` equals the candidate (34/34 today at `0561a7e`), so the K8s leg resolves at **+0** and needs no inheritance |
| 4 | Reliability soak | `cap-ga-reliability.yml` at the candidate | its verdict artifact binds to the candidate; the 24 h multi-leg roll-up (item F) stays out of scope |
| 5 | **strict / final-strict GA** | dispatched at the candidate with the strict flag, never in development mode | `mode == "final-strict"`, `full_ga_certified is True`, `planned == 0`, `failed == not_run == skipped == 0`, `passed == total`, `commit == head_sha` — the `AUTHORITY` table the gate already enforces |
| 6 | Supply-chain requirements | the GA round's supply-chain job + CI's trivy/sbom gates | Trivy findings at the blocking policy, SBOMs present, and (if B3-A approved) the provenance content assertions green against the round's own predicate |
| 7 | Gate dry-run | the read-only publication-gate dry-run executed against live state at the candidate, exactly as in batch 1 and §J.1/§J.2 | **F-33 authority PASS**; **F-42: every required job's conclusions all-success**, with a development GA round *and* a green-with-skipped-release-job round both refused; no `PLANNED`/`not_run` counted as passed; and the verdict artifact read is this round's own |

Steps 2-5 are the long poles and they run in parallel where the workflows allow; step 7 runs only
after all of 1-6 are complete, because a dry-run taken while a round is in flight resolves to an
older round's evidence — which is exactly the ambiguity F-42 was about.

**Steps 1-6 include dispatching certification and (for step 5) a strict GA dispatch. Those are
implementation-stage acts, explicitly out of bounds for this design freeze** (§A.2), and the
user authorizes them separately when the candidate is cut.

## N. Rollback and recovery

| Failure | Response |
| --- | --- |
| A certification class fails at the candidate | Do not patch the candidate to make it pass and reuse its SHA. Record the cause, fix it as a new commit, cut a **new** candidate SHA, and re-run the classes that are affected by what changed plus the ones that must bind to the new SHA. The cost is capacity, not correctness (§O.7). |
| The gate dry-run at step 7 refuses a round that looks green | That is a *finding*, not a failure, until the reason is examined: it has twice meant a real defect (F-33's development round, F-42's skipped job). Never resolve it by relaxing the gate. |
| E2's re-run does not reproduce its own artifact | E2 does not land; F-24/E3 are blocked behind it (P1), because pinning to an unreproducible measurement is the same mistake in a new costume. |
| A registry renames or retires a pinned digest (the MinIO precedent) | The lock's `previous_ref` / `previous_ref_status` fields and the per-image `upgrade_procedure` already model this; the failing compose image is re-resolved deliberately, with the matrix re-run, not "fixed forward" by unpinning. |
| Any runtime-affecting commit classifies unexpectedly | Stop (§K). The classifier is not edited to fit the plan. |
| Volume detach / transport loss mid-round (both have happened this week) | Recovery is re-measure, not resume-from-transcript: `git fsck --full`, per-file integrity, re-run tests and the classifier, as batch 1.1's §A-§C did. Nothing about a sealed release is at risk from that, because nothing in Batch 2 writes to the sealed line. |
| The whole batch is abandoned | Every item is one revertible commit; the sealed `v1.0.6-rc1` and `v1.0.5` are untouched throughout; compose/helm edits are inert until a release is cut from the line. The fallback state is the current one: RC1 published and certified, batch 1.1's gate fixes on `main`. |

## O. Risks and open decisions

1. **Docker Hub reachability from this host is intermittent, and that is a measured blocker for the
   digest values, not a hunch.** One window resolved all five Hub references with the round-trip
   check passing; a later window failed all five at the TLS layer (`RemoteDisconnected`,
   `SSL: UNEXPECTED_EOF_WHILE_READING`, handshake timeouts), against `auth.docker.io` as well, and
   the box's proxy fails those hosts too. Mitigation: E2's authoritative run happens where the
   registry answers (CI), and §L's P1 refuses the compose commit until then. This is also the
   literal reason `test_postgres_entry_is_tag_pinned_on_purpose` exists, which §D.3 reverses.
2. **Postgres policy reversal** — a deliberate contract change, with a guard test replaced rather
   than removed, and the loss of automatic upstream patch flow named in §D.3. Needs the user's nod
   as much as B3 does; this plan recommends reversing it, because "mutable bytes for a certified
   deployment" is the defect F-24 was filed for and the recorded reason for the exception is a
   transport fact.
3. **`helm lint` blast radius in D** — dropping `^$` from the digest pattern makes the chart's own
   defaults invalid unless `values.yaml` stops writing `digest: ""` at all six sites, so D is two
   files plus the docs that show the shape. `helm` is absent on this host, so the rendering tests
   are written blind and iterated in CI; the docs-contract tests that scan chart defaults may also
   need their fixtures updated, which is a `docs`/`test_harness` cost, not a schema cost.
4. **Schema-expression constraint** — the mini-evaluator's keyword set (§E.2) limits how the
   uniformity may be written unless it is extended deliberately.
5. **Frontend directives** — pinning `# syntax=` on the two Dockerfiles that have one is cheap;
   adding one to the three that don't would change what parses the build and is left out (§G.2.4),
   listed as its own open decision rather than hidden in a pin.
6. **B3 needs the user's five answers** (§H.3) — and B3-B specifically requires a verifier binary in
   the publication job and a signing permission change there, which is why it is not recommended in
   this batch.
7. **Capacity is not correctness** — the plan's ≈3.5 h serial estimate for a full re-certification is
   a scheduling note only. The completion criterion is: the required classes present, bound to the
   one candidate SHA, with artifact authority satisfied and the gate's own verdict — see §M and the
   instruction that time is not the criterion.
8. **Two unpushed-docs/report commits and one test commit** (`ae990dd`, `d495d71`, `8879599`) sit on
   the validated base while `origin/main` is at `0561a7e`. Batch 2's candidate cannot be cut from a
   local-only tree, so pushing that line is a precondition (§L.4) and the user's authorization, not
   this stage's.
9. **Compose is only syntax-validated today** (`docker compose config --quiet`, `ci.yml:215`) and no
   certification workflow exercises a compose deployment; the K8s certification always renders with
   `--set *.image.tag=ci` and never a digest. Batch 2 makes the compose references immutable and the
   chart's digest path *render-verified*, but it does not certify a compose deployment end to end —
   stated so nobody reads F-24 as more than it is.

## P. FINAL DESIGN VERDICT

| Requirement of the freeze | Met? | Where |
| --- | --- | --- |
| Base SHA and immutable boundaries named, and re-verified rather than quoted | **yes** | §A |
| The stale text closed first, as its own inheritable commit, with test + classifier | **yes** — `8879599`, 92 passed / 4 skipped in the two gate modules, ruff clean, hop INHERITED (`test_harness`, 1 file) | §A.3 |
| Scope frozen, with explicit exclusions | **yes** | §B, §C |
| F-24 inventory complete per image, decision made, future contracts stated | **yes** — three of the five Hub digests published from a verified window (postgres with its amd64 child, prometheus, pgadmin4), `redis` and `grafana` pending, and the generator's tracked file stays authoritative for the compose edit under P1 either way | §D, §L.2, §O.1 |
| F-41 six-coordinate contract with the seven-case matrix, precedence determined from the code that implements it, and real `helm lint`/`helm template` rendering planned | **yes** | §E |
| E2 evidence contract (method, determinism, credential-free, ambiguity-refusal, index-vs-platform distinction, fresh-clone runnable, tracked path) and E3 repoint + twin deletion | **yes** | §F |
| B2 producer inventory (action refs and their pin forms, buildx inputs, BuildKit image, frontend directives, build coupling) and the pinning strategy with the four impact assessments, incl. the policy that B2 joins recert regardless of classification | **yes** | §G |
| B3 with ≥2 designed contracts, the five questions answered, no `builder.id == ""` assertion, and a designed independence if unapproved | **yes — and the decision itself is the user's, so B3 is out of scope until answered** | §H, §K, §L.2 |
| Files-expected-to-change table and a test matrix with a control per guard | **yes** | §I, §J |
| Classifier expectations per commit and cumulatively, computed with and without B3 | **yes** | §K |
| One candidate SHA, ordered commits, preconditions, and no docs commit after it | **yes** | §L |
| Certification order for all six classes at one SHA + the gate dry-run's four required properties | **yes** | §M |
| Rollback/recovery per failure mode, and time explicitly excluded as a correctness criterion | **yes** | §N, §O.7 |
| No runtime-affecting file modified, no certification dispatched, no tag created, no `v1.0.6-rc1` mutation, classifier untouched | **yes** — every claim in this report is a read, and §A.3's classifier table is the proof | §A |

**BATCH 2 DESIGN FROZEN**

The freeze covers the contracts, not the two decisions reserved to the user — the postgres policy
reversal (§O.2) and B3's five answers (§O.6) — and it names one thing it cannot yet publish: the
final index digests, which §L's precondition P1 refuses until the generator produces them where the
registry answers. Implementation of C, D, E2, E3 and B2 may begin on that basis; B3 may not, until
approved, and nothing in the other five waits for it.
