# CAP F-56 OBJECT-STORE AVAILABILITY REMEDIATION DESIGN

Input: `POST-A2.2 CLOSURE COMPLETE`; certified historical candidate
`dea8c6f2f576bec667ee27dc8a9458673ca0982e`; `NEXT RELEASE BLOCKED — F-56`; F-55 OPEN.
Date: 2026-09-25 (UTC). This document **replaces** `docs/quality/cap-f56-object-store-remediation-design-2026-09-24.md`
(§A–§P, commit `4c77fc2`) with the fuller Stage 1–13 pass; what is new here is named in §A.5 and §L.

Two semantics the whole document is written under. `dea8c6f`'s certification is **valid history**: it
was certified against a source that was pullable at the time, and nothing below retroactively marks
it failed. Equally, "historically certified" is not "publishable today" — and as of this pass it is not
even *recertifable* today, because every required authority pulls the coordinate this report is about.

Nothing was implemented. No registry was written to, no image mirrored or pushed, no release tag
created, no certification round dispatched to watch an outage, no timeout raised, no mutable tag
adopted, no credential invented or stored, `dea8c6f` and sealed `v1.0.6-rc1` untouched, the classifier
unchanged, and no B3/B4/F-39/F-46 work started.

---

## A. Current availability evidence

One read-only pass (`_tmp/f56_stage1_probe.py`), stamped `2026-09-25T02:31:11Z` → `02:31:40Z`, from
this box through its own proxy. Two known-public repositories **in the same registry** are measured
first, with the full round trip, because the claim being made is a negative one and a negative claim
is only worth what the probe's positive control is worth.

| # | call | `quay.io/coreos/etcd` (control) | `quay.io/prometheus/prometheus` (control) | `quay.io/minio/minio` (subject) |
| - | ---- | ------------------------------- | ----------------------------------------- | ------------------------------- |
| 1 | `GET /v2/` challenge | 401, `realm="https://quay.io/v2/auth" service="quay.io"` | same | same |
| 2 | anonymous pull token for `repository:<repo>:pull` | **200**, 809 chars | **200**, 836 chars | **200**, 801 chars |
| 3 | manifest by tag | `v3.5.17` → **200**, `Docker-Content-Digest sha256:a055da833a7c0…` | `v3.0.1` → **200**, `sha256:565ee86501224…` | `RELEASE.2025-04-22T22-12-26Z` → **401** |
| 4 | manifest by the pinned digest | refetch of `sha256:a055da83…` → **200**, `sha256(body)==served: True`, byte-identical | **200**, `sha256(body)==served: True`, byte-identical | `sha256:a1ea29fa…` → **401** |
| 5 | `GET tags/list` | **200** | **200** | **401** `access to the requested resource is not authorized` |
| 6 | Quay repository API | **200**, `"is_public": true` | **200**, `"is_public": true` | **401** `Requires authentication` |

Two facts fall out. The probe works — the same token flow, the same digest round trip, the same header
arithmetic succeed for two other repositories minutes apart from the subject. And the subject's answer
is 401 where a *deleted* repository answers 404: **`minio/minio` is private, not gone**, and
`minio/mc` is in the same state, while its siblings `minio/console` and `minio/warp` are public. The
bytes are most likely intact behind an authentication door that closed around 2026-09-24 12:20Z.

The subject still refused at `2026-09-25T02:31Z`, so the observation window now runs from the last
green pull (`35997104443`, CAP Linux Certification at `5f550c4`, finished `2026-09-24T12:19:46Z`;
`35997104420`, K8s at the same commit, `12:21:19Z`) through at least 14 hours later — with the first
symptom a MinIO rollout timeout at `12:58:16Z` (job `107639698508`).

### A.5 What this pass adds over §A–§P of 2026-09-24

a timestamped re-measurement with positive controls; the full per-candidate coordinate records in §C
(child digests, platform sets, round trips, config labels); the derived rather than hand-written
inventory in §B; the measured mutation-control matrix in §L, which produced a new gap filed as
**F-57**; the credential-free end-to-end generator proof in §J; and the F-52 durability analysis in §P.

**Recovery rule, stated before it is needed.** If a future probe answers 200, F-56 is **not** closed.
The record would read `AVAILABILITY RECOVERED TEMPORARILY`, because what this incident established is
that an immutable coordinate can move from public to unauthorized with no change on our side, and that
property — not today's HTTP status — is the dependency-availability risk. A recovery changes the
remediation's *urgency*, never its *reason*.

## B. Exact reference inventory

Derived from the repository (`git grep -c` over tracked files at `4c77fc2`), not typed by hand:
**71 occurrences across 19 tracked files**, classified by what the file does with the coordinate.

| role | files (count) | runtime / certification / user-facing | governed by |
| ---- | ------------- | ------------------------------------- | ----------- |
| user deployment surface | `docker-compose.yml` (1) | **user-facing + runtime** | `test_every_reference_site_matches_the_lock`, `test_compose_pulls_every_external_image_by_digest_only`, F-24's both-directions compose↔lock test |
| certification surfaces | `cap-linux-certification.yml` (3), `cap-k8s-certification.yml` (1), `cap-ga-certification.yml` (1), `cap-ga-reliability.yml` (1) | **certification** | same site test, plus the derived-unlisted test |
| local certification bootstrap | `scripts/certification/setup.sh` (1) | runtime (developer/CI-prep) | same |
| the lock itself | `deployment/third-party-images.json` (7) | contract | schema/well-formedness, `image_ref` composition, digest-kind and "every locked digest says which digest it is" |
| generated evidence | `docs/quality/artifacts/registry-resolution/third-party-registries.json` (4) | contract | `test_a_locked_compose_digest_is_the_one_the_evidence_measures`, `test_cited_evidence_is_tracked_and_asserts_exactly_the_locked_digests` (F-37) |
| asserted literals in tests | `test_third_party_registry_evidence.py` (3), `test_third_party_image_lock.py` (4), `test_secret_scan.py` (1), `test_compose_env_contract.py` (1) | test-only, but they **must change with the coordinate** | see §L.3 — this is the class with the gap |
| prose, live | `docs/known-issues.md` (9) | documentation | none (deliberately) |
| prose, dated | `CHANGELOG.md` (5), `cap-post-a22-closure-2026-09-24.md` (2), `cap-1.0.6-rc1-release-certification-2026-09-19.md` (3), `cap-post-rc-batch-2-implementation-2026-09-22.md` (1), `cap-post-rc-batch-2-design-freeze-2026-09-22.md` (1), this design's predecessor (22) | **historical record** | none, on purpose |

The scan's own definition matters when reading the above: `_surface_files()` is compose + every
`workflows/*.yml` + `scripts/**/*.sh` + `deployment/**/*.yaml` + `backend/tests/*.py` + every
`Dockerfile`, and `test_the_lock_names_every_site_that_pulls_a_locked_image` deliberately looks for
*unlisted* pull sites only among the non-test surfaces, because "a test file may name a coordinate as
parsing input without pulling it". Docs are not surfaces at all. So for every file that **pulls**,
inventory coverage is complete in both directions today, verified by construction and re-verified in
§L; what is *not* covered is (i) extra stale coordinates sitting beside a correct one (§L.2, F-57) and
(ii) the derived-vs-literal distinction in test files, which no test keeps in sync.

One harness fact worth recording because it bit this round: `_surface_files()` filters Dockerfiles on
`"_tmp" not in p.parts`, so a throwaway worktree placed under `_tmp/` loses its own Dockerfiles and
`test_the_lock_names_every_site_that_pulls_a_locked_image` fails **on an unmutated baseline**. Every
number in §L comes from a worktree outside the repository, and the baseline there passes.

## C. Official / vendor-supported source investigation

**NO PUBLIC VENDOR-SUPPORTED ANONYMOUS SOURCE FOUND.** Documentary findings (vendor README, dated
commits/issues, the vendor's own 410 statement, its trademark page) plus the live records below:

- README, `github.com/minio/minio`: *"The MinIO community edition is now distributed as source code
  only. We will no longer provide pre-compiled binary releases for the community version."* Co-founder,
  issue #21647 (2025-10-18): *"This project is a source only distribution now, if you want to build
  containers you need to build them yourselves."* Source-only switch commit `9e49d5e7` (2025-10-15);
  repository **archived/read-only 2026-04-25**; README banner `7aac2a2c` (2026-02-12): *"THIS
  REPOSITORY IS NO LONGER MAINTAINED."*
- `dl.min.io` answers **HTTP 410** with an official statement covering *"all community releases and …
  all hotfix builds"*, and says no security advisories will be produced.
- No live vendor document names an anonymously pullable community image. `docker.io/minio/minio` and
  `quay.io/minio/minio` appear only in frozen artifacts (archived `docs/docker/README.md`, the old
  `helm/minio/values.yaml` default).
- On the September visibility change there is **no vendor statement**. The closest official answer is a
  MinIO employee in `minio-java` #1722 (2026-09-17), asked why the Docker Hub repository and all its
  images were deleted: *"This is not related to minio-java issue. If you are looking for AIStor MinIO,
  follow https://quay.io/repository/minio/aistor/minio…"* Secondary corroboration of the timing:
  community threads dated 2026-09-12/13/14, and issues in `readest` and `milvus` on 2026-09-12/13.

Measured records, each with the three checks the E2 contract requires (token issued → index digest with
`sha256(body)==served` → refetch by digest byte-identical → exactly one `linux/amd64` child):

| source | anonymous pull | index digest (`latest` / documented tag) | linux/amd64 child | platforms | artifact's own labels | vendor authority | license |
| ------ | -------------- | ---------------------------------------- | ----------------- | --------- | --------------------- | ---------------- | ------- |
| `quay.io/minio/minio` (subject) | **401** | — | — | — | — | was vendor-owned and public; now private | AGPLv3 code |
| `registry-1.docker.io/library/minio` | token 200, manifest **401** | — | — | — | — | vendor's retired channel | AGPLv3 |
| `public.ecr.aws/minio/minio` | token 200, **404 `NAME_UNKNOWN`** | — | — | — | — | namespace exists, repository does not | — |
| `ghcr.io/minio/minio` | **no anonymous token (403)** | — | — | — | — | no such public namespace | — |
| `mirror.gcr.io/minio/minio`, `public.ecr.aws/upstream-mirror/…` | **404** for the pinned digest | — | — | — | — | mirrors do not hold it | — |
| `quay.io/minio/aistor/minio` | **200** | `sha256:107cf2014a958…` ✔ round-trip ✔ | `sha256:a94e1fe399bd0…` | amd64, arm64, unknown | `vendor/maintainer = MinIO Inc <dev@min.io>`, `release/version = RELEASE.2026-09-19T17-05-25Z`, created 2026-09-20 | **vendor-owned**, confirmed by the artifact's own metadata + vendor docs | **proprietary AIStor subscription; licence key required; redistribution limited** |
| `registry-1.docker.io/chrislusf/seaweedfs` | **200** | `sha256:ce9e796f1fe6f…` ✔ ✔ | `sha256:f83509b0721df…` | 386, amd64, arm, arm64 | `licenses=Apache-2.0`, `vendor=Chris Lu`, `version=4.47`, created 2026-09-14 | project's own repo, not MinIO | Apache-2.0 |
| `registry-1.docker.io/dxflrs/garage` | **200** at the documented tag `v2.3.0` | `sha256:866bd13ed2038…` ✔ ✔ | `sha256:dac0c92add4f1…` | 386, amd64, arm, arm64 | **no labels** | docs at `garagehq.deuxfleurs.fr` name `dxflrs/garage:v2.3.0` and say prefer a fixed tag | AGPL-3.0 |
| `registry-1.docker.io/pgsty/minio` | **200** | `sha256:b6bfe7239bfc8…` ✔ ✔ | `sha256:2b36182f3479c…` | amd64, arm64, unknown | `maintainer=pgsty`, `org.opencontainers.image.version=RELEASE.2026-08-04T00-00-00Z`, `vendor=Red Hat, Inc.` (inherited from the `ubi9-micro` base), `release=1784702951` | a **community fork**, not vendor-supported; its tags imitate the vendor's `RELEASE.…` cadence | AGPLv3 code, unverified lineage |

A repository name was not treated as authority anywhere in this table: each row names what actually
establishes it — the artifact's own labels (AIStor, SeaweedFS, pgsty), the project's documentation
(Garage), or a registry error that distinguishes *closed to anonymous reads* from *not present*
(`401` vs `404`).

**Rebuildability, because "just build it from the signed tag" is the obvious wrong answer.** At
`RELEASE.2025-04-22T22-12-26Z` the plain `Dockerfile` is `FROM minio/minio:latest` (the deleted image),
and `Dockerfile.release` is `FROM golang:1.24-alpine` → `FROM registry.access.redhat.com/ubi9/ubi-micro:latest`
which **downloads the vendor's prebuilt binary** from `dl.min.io/…/archive/minio.${RELEASE}` and
minisign-verifies it. Release images were therefore never compiled from the git tag, that URL is now
410, and the recipe uses unpinned `:latest` bases plus `apk add -U` — so a rebuild could not reproduce
`sha256:a1ea29fa…` even in principle, and would be a *modification* for trademark purposes (§H).

## D. Option A — another official, vendor-supported public repository, digest-pinned

| axis | assessment |
| ---- | ---------- |
| default compose user | perfect (no change at all beyond the coordinate) |
| CI/certification | perfect |
| credentials | none |
| secret distribution | none |
| immutable digest | yes, and the *existing* digest, so all recorded evidence still describes it |
| provenance | strongest available (vendor-signed tag + image labels, already recorded) |
| redistribution/licence | untouched — no copying happens |
| architecture | the pinned index carries amd64/arm64 (+ attestation), so a non-amd64 host resolves its own build |
| maintenance | one coordinate in the lock, nothing new to feed |
| upgrade procedure | unchanged: the lock's `policy.minio_object_store.upgrade_procedure` |
| availability/SLA risk | **this is precisely the risk that just fired**: a third party flips one visibility switch and the release line stops |
| application compatibility | total |
| migration/data | none |
| classifier impact | `deployment`/runtime-affecting if the coordinate moves; none if nothing moves |
| recertification | none if the digest is unchanged |

**Not available.** No vendor-supported anonymous source exists (§C), and the pinned repository is
private rather than deleted. The design keeps §N's V-1 as a standing re-probe: if an anonymous manifest
GET for the pinned digest ever answers 200, Option A immediately outranks everything below — and even
then F-56 stays on the books per §A.5.

## E. Option B — CAP-controlled mirror of exact approved vendor bytes

**Destination: measured sound.** A public package in this project's own ghcr namespace is anonymously
pullable through exactly the handshake `scripts/release/third_party_registry_evidence.py` performs —
`ghcr.io/laolaola278-dev/cap-backend:1.0.6-rc1` → 200 with the digest §K of the closure report verified
on ghcr. No generator change is needed for a mirror there (`host()` passes `ghcr.io` through, and the
scope-from-us rule already handles ghcr's placeholder challenge).

**Source: measured blocked.** No anonymous source serves the pinned digest (§C). Routes, in order: a
cache someone already holds (any machine that ever ran `docker compose up` or
`scripts/certification/setup.sh`); a one-time credentialed vendor pull (§F); or a third-party copy that
answers to the *same digest* — acceptable only on digest equality, since content addressing, not trust
in the copier, is what makes it the same bytes.

**Contract requirement discovered here, and it is load-bearing.** The lock schema demands a non-empty
`tag` (`test_third_party_image_lock.py:268`) and `collect_targets()` builds the target coordinate as
`{registry}/{repository}:{tag}`, resolving **by tag** and asserting the digest afterwards. A mirror that
publishes only a digest would break evidence generation. So the mirror must carry the tag
`RELEASE.2025-04-22T22-12-26Z` too — which is a better position than today's, because "mutable tag"
inside a namespace we control means it changes when our release process says so.

| axis | assessment |
| ---- | ---------- |
| compose user | restored to exactly today's UX (anonymous, digest-pinned) |
| CI | restored, no secrets |
| credentials | needed **once**, to obtain the bytes; never stored |
| secret distribution | none in steady state |
| immutable digest | preserved bit-for-bit |
| provenance | must be *extended*, not replaced: vendor signed tag + `provenance.mirror` (who copied, when, from where, digest-equality proof) |
| redistribution/licence | the §H question; AGPL permits conveying unmodified copies with the licence retained; the trademark policy is silent on images → legal confirmation required |
| architecture | copy the whole index (amd64/arm64 + attestation), not one child |
| maintenance | ours: re-copy on every deliberate upgrade; nothing else changes |
| upgrade procedure | extended with "mirror first, verify digest equality, then move the sites" |
| availability | ours; the failure mode becomes operational, not vendor policy |
| application compatibility | total (same bytes, same version) |
| migration/data | none |
| classifier | `deployment` + runtime-affecting → new candidate (§O) |
| recertification | full set |

**Not executable without two authorizations** (registry write; legal read) **and one input** (the
bytes). It is the recommendation when those exist, because it preserves everything certified.

## F. Option C — credentialed pull from the vendor

Fatal as a shipped state, legitimate as a one-time bridge:

| axis | assessment |
| ---- | ---------- |
| compose user | **not fixed.** A fresh clone with no credentials fails the same 401. The brief's own test — can an ordinary documented compose user obtain credentials? — answers no: the vendor's public posture is to redirect to a paid product |
| CI | would work immediately with one org secret |
| credentials | a vendor-issued robot token we do not hold, whose owner just closed the anonymous path and has an incentive to withdraw access |
| secret distribution | a standing secret in four workflows plus every operator's docker config |
| immutable digest | unchanged |
| provenance | unchanged (same bytes) |
| licence | no redistribution question at all |
| architecture / compatibility / data | unchanged |
| maintenance | quietly fragile: the credential is the dependency |
| availability | same single-polity risk that produced F-56 |
| classifier | a workflow edit (login step + secret) → new candidate; and it makes certification depend on a secret, which F-49's governance line says must be described, not assumed |
| recertification | full set |

## G. Option D — replace MinIO with another S3-compatible object store

The only option executable **without third-party cooperation**. Cost is bounded and measurable: the
product touches the store through `backend/app/acquisition/store.py` (`from minio import Minio`,
`minio.error.S3Error/InvalidResponseError`) against `OBJECT_STORE_ENDPOINT`;
`settings.object_store_backend` is `local | s3`, so a non-S3 fallback already exists; `mc` appears only
in the GA/soak DR gates, installed from a pinned GitHub release asset with a sha256 check — measured
reachable during this outage (GA run `36004166008`'s `supply-chain` job succeeded while its sibling
failed on the MinIO deploy; the run before it, `36000276326`, was cancelled while queued and so says
nothing either way, F-54).

| axis | SeaweedFS | Garage | pgsty/minio (fork) | Ceph RGW |
| ---- | --------- | ------ | ------------------ | -------- |
| compose UX (anonymous, pinned) | ✔ measured 200 + round trip | ✔ at its documented `v2.3.0` | ✔ measured | ✔ measured |
| credential need | none | none | none | none |
| licence | Apache-2.0 (label-carried) | AGPL-3.0 | AGPLv3, fork not vendor | LGPL/Apache |
| provenance chain | its own repo | commit-id tags; **no image labels** | imitates `RELEASE.…`; `vendor` label inherited from base | mature, heavier |
| platforms | 386/amd64/arm/arm64 | 386/amd64/arm/arm64 | amd64/arm64 | amd64/arm64 |
| S3 feature risk | versioning + multipart + presign must be re-certified | same, plus its own error shapes | lowest (same code lineage) | lowest risk, highest ops cost |
| operational maintenance | modest | modest | **depends on a volunteer's continuity** | heavy |
| upgrade procedure | its own release cadence | its own | the fork's | stable |
| data compatibility | not interchangeable with existing buckets | same | bucket-compatible in principle | same |
| classifier / recertification | `deployment` → new candidate, full set | same | same | same |

Compatibility work is not optional: the certification exercises real S3 signatures, **versioning**,
**multipart evidence upload**, and the DR restore path (`K8S-GATE 9` isolates the sandbox from MinIO,
`test_phase_28_7_ga_certification.py` ports 9000 for the evidence gates, and the soak's outage scenario
scales `minio` to zero and back). "S3-compatible" is a claim those gates exist to test.

## H. Licence / redistribution analysis

The operative constraint is trademark, not copyright:

- Community MinIO is **AGPL-3.0** and did not change at archival; conveying an unmodified copy with the
  licence retained is what AGPL is for. The network-service source-offering obligation does not attach
  to CAP's use (an internal evidence store in the customer's own deployment), and that reasoning belongs
  in the lock file if the mirror route is taken rather than being assumed.
- The vendor README invites remixing under AGPLv3 while warning that commercial/proprietary use
  "including repackaging or reselling services/features, is done at your own risk"; `COMPLIANCE.md`
  disclaims compliance determinations.
- **Trademark policy (2025-08-29)**: *"You can distribute unchanged official binaries downloaded from
  https://min.io/download to anyone in any way subject to governing law, relevant terms of the license,
  without receiving any further permission from MinIO. However, you must not remove or change any MinIO
  Trademarks."* Modifications may not be redistributed under MinIO marks and must be renamed and
  de-branded.
- **On container images the policy is silent**, and the channel it names (`min.io/download`) is the one
  now returning 410. The defensible reading: a byte-identical copy *is* an unchanged official artifact
  (content addressing proves it), so the permission is the closest governing text — while the
  **destination name must not imply vendor authorship**. `ghcr.io/laolaola278-dev/cap-object-store` with
  provenance pointing at the vendor's tag and digest satisfies that; a name carrying "minio" in our own
  namespace is weaker, and a rebuilt image under the MinIO name is prohibited.
- **AIStor is a different instrument**: subscription tiers, a licence key to install, and a licence that
  *"limits any redistribution or resale"* — mirroring it is not available on the same reasoning, and
  adopting it is a product/legal decision, not a re-pin.
- A **rebuilt** image is a modification: rename and de-brand, and the AGPL source-offering question
  becomes live.

Legal review is therefore an explicit authorization item (§Q.3), and the answer decides B's shape, not
merely its paperwork.

## I. Recommended remediation

1. **Spend the first hour on §N's V-1, not on a decision.** Search every cache that could hold
   `sha256:a1ea29fa…` (machines that ran the compose stack or `setup.sh`, any registry the team
   operates, any CI cache that outlives a run). This box could not answer it: the Docker daemon is not
   running here, so the local image store is **unverified**, and the report says so rather than
   guessing either way.
2. **Bytes found → Option B**, and the design is already complete: push verbatim into a public
   `ghcr.io/laolaola278-dev/…` repository, publish the same tag, verify digest equality, regenerate
   evidence, move all sites in one commit, freeze a new candidate, recertify. This preserves every byte
   the certification history describes.
3. **Bytes unrecoverable → Option D, chosen deliberately**, with §G's table as the starting evidence
   and the deciding question named: implementation-divergence risk (SeaweedFS/Garage — independently
   maintained, licence-clean, measurable in V-6..V-12) versus continuity-of-a-volunteer risk (a
   MinIO-lineage fork — least behavioural change, no vendor authority, weakest provenance chain, and
   the one whose labels are inherited rather than owned).
4. **Reject A as unavailable; reject C as a shipped state.**
5. **Two cheap-looking moves are refused by rule**, not by taste: raising the 120 s rollout budget (it
   converts an unavailable dependency into a longer wait and then the same red) and pointing anything
   at a mutable tag whose name merely contains "minio".

## J. E2 integration

Any accepted source goes through `scripts/release/third_party_registry_evidence.py` and must satisfy,
per coordinate: an anonymous pull token from the registry's own challenge with the **scope named by
us**; `Docker-Content-Digest` equal to `sha256(body)`; refetch by that digest byte-identical; an index
(not a schema1 conversion or a platform manifest) as `digest_kind`; **exactly one** `linux/amd64`
child; a recorded platform set; the tracked evidence artifact; a `verified_on` date that is allowed to
change and is excluded from the digest claims; and no credential anywhere in the output (the generator
scans its own text before replacing the file).

**Measured, not argued — the pipeline is healthy and only the source is gone.** With the object-store
coordinate replaced by an anonymously resolvable public stand-in across all six surfaces plus the lock
entry, the generator completed end to end with no credential of any kind: **exit 0, 13 image claims
written, no `Bearer` text in the artifact, object-store entry present**
(`_tmp/f56_generator_probe.py`, worktree reset afterwards). Under the real outage it refuses loudly and
leaves the tracked artifact untouched:

```
refused: quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z as RELEASE.2025-04-22T22-12 answered HTTP 401
the previous evidence file, if any, is left untouched
```

Two integration rules follow. A public-contract source **must** resolve credential-free, so a private
mirror is not an option — it would fail `--check` for every reviewer without credentials, which is
F-56 rebuilt inside our own tooling. And a mirror **must publish a tag** (§E), because the generator
resolves by tag and asserts the digest afterwards. No digest is ever hand-pasted into compose or the
lock: the generator's artifact is what the lock's digests are checked against.

## K. Lock and history migration

The migrated entry keeps a *readable* lineage. Required shape:

- current: `registry`, `repository`, `tag`, `digest`, `digest_kind: "manifest-list (multi-arch index)"`,
  `image_ref` (which must recompose exactly as `f"{registry}/{repository}@{digest}"` — a test enforces
  the composition), `platform_digest_linux_amd64`, `provenance{verified_on, repository_namespace,
  image_config_labels, image_config_created, vendor_signed_tag, evidence, resolved_by, open_gap}`,
  `referenced_by`, `derived_by`.
- retired: `previous_ref` = `quay.io/minio/minio@sha256:a1ea29fa…`, `previous_ref_status` carrying the
  full F-56 sentence: *previously pullable anonymously (evidence: `35997104443` green at
  2026-09-24T12:19:46Z), then anonymous manifest and tag-list GETs answered 401 from
  2026-09-24T12:58:16Z through at least 2026-09-25T02:31:40Z, with the Quay repository API at 401
  `Requires authentication` while sibling repositories in the same namespace answered 200 public —
  private, not deleted*; and the earlier `docker.io/…` retirement preserved beneath it, since §L.3
  shows a test asserts on `previous_ref` itself.
- `policy.minio_object_store`: `why` rewrites to the source actually in use; `upgrade_procedure`
  stays explicit and gains its first step (B: mirror and verify digest equality first; D: resolve the
  replacement's index and check the single-amd64-child rule); and the honest `open_gap` for a fork or a
  rebuilt copy — state what is **not** proven.
- F-24 and F-37 stay CLOSED by construction, not by hope: every external compose image in the lock and
  vice versa, and the cited evidence file tracked, under the right root, and asserting exactly the
  locked digests. Both fired during §L's mutations, which is the evidence that they still fire.

## L. Reference migration, and the controls that make "no half migration" a fact

### L.1 Order that never leaves a lying gate

One commit: (1) regenerate the evidence artifact for the new coordinate; (2) rewrite the lock entry as a
whole, `image_ref` included; (3) re-point the six pull surfaces; (4) update the four test-bearing files,
including the two that assert literals (`test_third_party_registry_evidence.py`'s tag and digest-prefix
assertions, and `test_lock_records_the_retirement_with_evidence`'s `previous_ref` equality); (5) move
the old coordinate into the retired set (§L.2) so the *new* retirement is guarded; (6) update the live
prose and add a CHANGELOG entry, leaving dated reports and prior CHANGELOG lines as written;
(7) `git grep 'a1ea29fa\|quay.io/minio'` must then return only retired/historical sites.

### L.2 Measured control matrix

Each mutation applied in a throwaway worktree **outside** the repository, the lock contract run against
it, the worktree reset after every case (`_tmp/f56_mutation_probe.py`, `_tmp/f56_mutation_results.txt`):

| case | mutation | result | verdict |
| ---- | -------- | ------ | ------- |
| M0 | none (baseline) | **30 passed** | harness healthy |
| M1a | retired `docker.io/minio/minio:RELEASE.…` written into `setup.sh` **beside** the correct line | **30 passed** | **GAP — not caught. Filed as F-57** |
| M1b | current coordinate added to a workflow the lock does not list | 1 failed — derived-unlisted site test | caught |
| M2 | compose switched to a **mutable tag** | 6 failed (digest-only compose rule, evidence-vs-lock, both-directions lock↔compose, site tests, "one tagged line is enough to name that service") | caught loudly |
| M3 | compose and lock disagree on the digest | 4 failed (evidence-vs-lock, both-directions, both site tests) | caught |
| M4 | half migration: compose moved, certification surfaces did not | 5 failed | caught |
| M5 | lock fields edited without recomposing `image_ref` | 4 failed incl. `test_digest_pinned_image_uses_an_immutable_reference`, `test_lock_file_is_well_formed` | caught |
| M6 | coordinate moved in all surfaces and the lock, evidence not regenerated | 5 failed incl. `test_every_locked_digest_says_which_digest_it_is`, `test_cited_evidence_is_tracked…` | caught |
| M7 | prose-only edit outside every scanned surface | **30 passed** | no false alarms |

The drift checker compares each surface against `entry["image_ref"]` **verbatim**, and site derivation
asks "does this file name the coordinate" — neither asks "does this file name something it should not".
That is exactly the shape M1a exploits: a surface that carries the right reference *and* a wrong one is
clean. Compounding it, the existing retirement guard is a single hand-written regex
(`_STALE_PATTERN`) whose lookbehinds — `(?<!quay\.io/)` and `(?<![\w./-])` — exempt precisely the
fully-qualified form M1a used, because they were written to let the lock's own `previous_ref` text
pass. So today's guard catches a *bare* `minio/minio:` and misses `docker.io/minio/minio:`.

### L.3 The controls the migration must add, with their own negative control

1. Make retirement **data, not a regex**: a `retired_refs` list in the lock (`coordinate`, `status`,
   `retired_on`), and one parameterized guard over it that fails on any retired form — bare, prefixed,
   pinned, or mutable — in any live surface. This is what catches M1a's shape, including the *quay*
   coordinate once the migration retires it.
2. Assert **exclusivity**, not presence: a listed surface must name the locked `image_ref` **and** no
   other reference to the same repository or registry path.
3. Keep the test literals in sync from the lock rather than copying them: the two literal-bearing test
   sites should read the entry (as `test_phase_28_7_ga_certification.py` already does) so a future
   migration cannot half-update them.
4. Each new guard needs a case like M1a/M2/M3 proving it fails, and one like M7 proving it does not
   fire on harmless input.

## M. F-55 diagnostics — designed independently of the remediation

On failure, `cap-k8s-certification.yml` must print, from `cap-infra` (the namespace it currently never
looks at):

```
kubectl -n cap-infra get pods -o wide
kubectl -n cap-infra describe pods
kubectl -n cap-infra get events --sort-by=.lastTimestamp | tail -40
kubectl -n cap-infra get deployment minio -o yaml
kubectl -n cap-infra get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}'
  '{.spec.containers[*].image}{"\t"}{.status.containerStatuses[*].imageID}{"\t"}'
  '{.status.containerStatuses[*].state.waiting.reason}{"\t"}'
  '{.status.containerStatuses[*].state.waiting.message}{"\n"}{end}'
```

`waiting.reason` is the discriminator this round had to reconstruct by hand: `ErrImagePull` /
`ImagePullBackOff` with a 401 in the message is an unavailable dependency; `CreateContainerConfigError`
is configuration; a pending pod with no container status is scheduling. The same block belongs in
`cap-ga-certification.yml` and `cap-ga-reliability.yml`, whose cluster manifests are inline and share
the blind spot.

Boundaries, all of them the point: the dump runs only under `if: failure()`, every line `|| true`, and
**it changes no verdict** — no gate reads it, no artifact field is altered, and `--timeout=120s` stays
exactly as it is. Add a test that the dump names every namespace any job creates infrastructure in
(so the next `cap-infra` cannot be forgotten the way this one was), and keep F-55 **OPEN** until the
path has actually executed on a CI failure — a diagnostic that has never run is a claim, not a
capability.

## N. Validation matrix before a new candidate

| # | layer | check | passes when |
| - | ----- | ----- | ----------- |
| V-1 | source | pinned digest anonymously resolvable again? (token → manifest by digest) | 200 ⇒ Option A reopens. 401 ⇒ proceed. Either way §A.5's rule applies |
| V-2 | source | byte recovery hunt for the exact digest from caches, then `docker save`/`push` with digest equality | a copy exists whose `Docker-Content-Digest` equals `sha256:a1ea29fa…` |
| V-3 | registry | `python scripts/release/third_party_registry_evidence.py` then `--check` | exit 0, credential-free, no refusal, 13+ claims incl. the object store |
| V-4 | registry | `--conflict-check` | every target resolves twice to one answer |
| V-5 | contract | the §L.3 guards + M1a/M2/M3/M4-shaped negative controls | each intended failure fails; M7-style harmless input passes |
| V-6 | compose | `docker compose config` and `docker compose --images` | renders; the object store appears pinned by digest only |
| V-7 | compose | `docker compose up -d` on a **fresh clone with no credentials**, then the service healthcheck (`/minio/health/live` today) | object store starts and becomes healthy |
| V-8 | S3 | create bucket → put → get → delete, through the client `store.py` uses | all four succeed |
| V-9 | S3 | versioned bucket, multipart evidence upload, DR restore via `mc` (the paths certification actually exercises) | all green, not just reachable |
| V-10 | Linux | container initialization proceeds (where F-56 first announced itself) and the `layer=release` round reaches `cap-production-certification` | release-layer success, not a skipped job set |
| V-11 | K8s | `cap-infra` object-store rollout succeeds and the certification gates complete | **34/34** — `K8S-GATE 1..34` per `test_phase_28_6_k8s_certification.py`'s own header |
| V-12 | GA/reliability | the cluster-deploy prerequisite succeeds; the soak's object-store outage drill runs | deployment prerequisite green, supply-chain green |
| V-13 | supply chain | new coordinate present in lock + evidence + SBOM surfaces | no site or artifact left describing the old one |
| V-14 | producer authority | A2.2 path unchanged | five images `CONFORMING`; F-44/F-47 not reopened |
| V-15 | if the version changes | write on the old server, read on the new (and back) for versioned + multipart objects | passes, or the migration note says plainly that re-upload is required |

## O. Classifier and recertification impact

Measured with a throwaway migration commit in the worktree, `dea8c6f` as the certified SHA:

```
RESULT: RECERTIFICATION REQUIRED (runtime-affecting change found)   exit 2
  docker-compose.yml                    category=deployment  runtime_affecting=true
  deployment/third-party-images.json    category=deployment  runtime_affecting=true
```

So the classifier answers *correctly* here, unlike the wording round, and **no classifier change is
proposed or permitted** (its path-keying is F-49's subject, and the brief forbids touching it).
Consequences: the remediation freezes a **new candidate** — `dea8c6f` is not reused as a future release
candidate, and its certification is cited only as history — and the full set must be green at the new
SHA before any tag: CI; Linux with `layer=release` (a push run is not release evidence); K8s; the 7200 s
reliability soak; final-strict GA (40/40, `full_ga_certified: true`); supply chain; producer authority
(five `CONFORMING`); and both publication authorities dry-run at distance 0. F-54's lesson applies to the
plan itself: after dispatching a release-scoped round, **do not push to `main`** until its artifact
exists, or a queued round disappears with no trace but a `cancelled` row. Because the remediation
already edits `.github/workflows/`, F-54's comment corrections and F-55's diagnostics can ride the same
candidate at no additional recertification cost — the cheapest they will ever be.

## P. F-52 — durable producer-evidence preservation

F-52's defect today: the producer evidence lives in
`outputs/release-images/release-images-<version>.json`, uploaded as the Actions artifact
`cap-<version>-release-images`, which expires (measured 89 days, `retention-days` unset anywhere), while
the immutable GitHub Release carries the chart, the digest-naming values file, `CHANGELOG.md`, the notes
and `known-issues.md`. In a year nobody can say which producer built the published bytes.

The preferred shape is exactly the brief's: **the same file as a Release asset**,
`release-images-<version>.json`, whose payload already contains everything required —
`producer_contract`, `producer_verdicts`, `producer_summary`, per-image `images` records (digests,
`source_revision`, `run_id_recorded`, `job`, run binding), `failures`, and `verdict`. Nothing new needs
to be computed; it needs to be *attached*.

Implementation is three lines in one file (`release-chart`: copy the JSON into `dist/` and add
`dist/release-images-*.json` to the upload path list; `publish-release`: add it to `gh release create`'s
file list), and the repo already owns the guard that makes a half-doing impossible:
`test_every_uploaded_release_asset_is_attached_to_the_release` fails any path that is staged into
release-assets but not attached to the Release — the exact bug `values-release-<version>.yaml` once had.
What must be *added* is a positive assertion that this specific asset ships, which fails today — a real
requirement rather than a test that cannot fail.

**Does it belong in the F-56 candidate or its own?** Decide it from the classifier's blind spot, not
convenience: a commit touching only `.github/workflows/release.yml` classifies `ci_workflow`,
`runtime_affecting=false` ⇒ `INHERITED`, even though it changes what a published Release *contains*.
That is F-49's argument in a concrete case, so:

- **Recommended: fold F-52 into the F-56 candidate.** The recertification happens regardless, so
  bundling costs nothing, and it prevents the asset change from riding a future candidate on the
  strength of an `INHERITED` line that cannot see it.
- **Not acceptable: landing F-52 alone as "docs/CI-only".** If it ever is separate, it must be frozen
  as its own candidate with a release-scoped round, not inherit.
- **State the limit plainly:** no recertification set proves this end to end, because `publish-release`
  runs only on a `v*` tag. What is provable pre-tag is the static asset-list guard plus the dual-authority
  publication dry-run; the observation belongs to the first real release, which is F-51's window. The
  same distinction the closure round refused to blur — implementation-closed versus
  live-publication-observed — applies here.

## Q. Decisions requiring explicit authorization

1. **Byte-recovery hunt (V-1/V-2)** — read-only, but it means looking at other machines' caches and any
   registry the team runs. Name who may look where.
2. **Registry write** — creating a public `ghcr.io/laolaola278-dev/<mirror>` package and pushing vendor
   bytes into it. Forbidden under this brief; needs an explicit yes.
3. **Legal/trademark read** on §H: conveying an unmodified vendor image under a neutral name in our own
   namespace, where the vendor's policy addresses binaries and is silent on images.
4. **If the bytes are unrecoverable, the Option D choice** — lineage fork vs independent implementation,
   accepting the V-8..V-15 compatibility cycle either way.
5. **Vendor contact** — whether to ask MinIO for a one-time pull or a public reopen, and who owns that
   communication.
6. **AIStor** — a separate product/licence decision, not implied by anything above.
7. **Credential scope if C is used only as a bridge** — short-lived, never committed, never a standing CI
   secret.
8. **F-57** — the retirement-guard gap in §L: whether to fix it as part of this candidate (recommended,
   since the migration is what retires a coordinate and §L.3 keeps future migrations honest) or as its
   own round.
9. **F-52 placement** — §P's recommendation is "with F-56"; the alternative is its own candidate, never
   an inherited ride.

## R. FINAL STATUS

**F-56 REMEDIATION DESIGN READY — IMPLEMENTATION REQUIRES APPROVAL**

Ready, and not *BLOCKED — NO ACCEPTABLE SOURCE*, on measured grounds: the block is the source's
visibility, not the project's capability. With the pipeline proven healthy end to end credential-free
(§J: generator exit 0, 13 claims, no credentials), the migration controls mapped case by case (§L), the
destination registry proven anonymously pullable (§E), and a source-independent replacement whose
candidates are themselves measured pullable, digest-pinnable and licence-attributable (§C, §G), two
executable designs exist and each has a validation matrix.

The decision that unblocks implementation is one fact and two permissions: **do the exact bytes still
exist somewhere reachable** (V-1/V-2), and if so, **may we re-host them** (Q.2/Q.3). If they do not,
§G's Option D table is the starting point, chosen as a deliberate dependency change rather than as a
substitution that happens to share a name.
