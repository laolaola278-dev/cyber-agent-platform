# CAP F-56 OBJECT-STORE AVAILABILITY REMEDIATION DESIGN

> **Superseded on 2026-09-25** by
> [`cap-f56-object-store-availability-remediation-design-2026-09-25.md`](cap-f56-object-store-availability-remediation-design-2026-09-25.md),
> which runs the fuller Stage 1–13 brief: a re-probe with two known-public controls in the same registry,
> full per-candidate coordinate records (child digests, platform sets, round trips, image labels), an
> inventory derived from tracked files rather than written by hand, a measured mutation-control matrix
> that filed **F-57**, an end-to-end credential-free generator proof, and the F-52 asset decision.
> What follows stands as a dated record of what was measured on 2026-09-24; nothing in it has been
> rewritten to match the later pass.


Input: `POST-A2.2 CLOSURE COMPLETE`, `NEXT RELEASE STATUS: BLOCKED — F-56`, certified historical
candidate `dea8c6f2f576bec667ee27dc8a9458673ca0982e`. Date: 2026-09-24.

**This round designed; it implemented nothing.** No registry was written to, no tag was created, no
certification round was dispatched, no candidate was modified, and no file outside this report and the
register entry it updates names a new object-store coordinate. Every forbidden action in the brief was
respected, including the two that were easiest to slip past: pushing a mirror image "just to test", and
raising the K8s `--timeout=120s` that F-55 documented.

One premise in the brief deserves stating before the evidence, because the report depends on it: the
candidate's certification is **not** invalidated by F-56. `dea8c6f` was certified on 2026-09-24
morning against a source that was then pullable; the source changed afterwards. Everything below is
about restoring the *next* release, not about re-judging the last one.

---

## A. Current failure evidence

Measured in one read-only pass (`_tmp/f56_source_probe.py`, `_tmp/f56_open_questions_probe.py`, both
against the registries' own HTTP APIs from this box through its proxy; no token printed, nothing
pushed).

| check | result |
| ----- | ------ |
| registry | `quay.io` — reachable, `Docker-Distribution-Api-Version: registry/2.0` |
| repository | `minio/minio`, pinned `…@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e` |
| tag still known | `RELEASE.2025-04-22T22-12-26Z` (from the lock file; **not** verifiable against the registry any more) |
| anonymous auth challenge | `401` + `Bearer realm="https://quay.io/v2/auth",service="quay.io"` |
| token result | **200**, an ~800-char anonymous pull token is issued for `repository:minio/minio:pull` |
| manifest by digest, with that token | **401** `access to the requested resource is not authorized` |
| manifest by tag, with that token | **401**, same body |
| `tags/list`, with that token | **401**, same body |
| Quay repository API `minio/minio` | **401** `Requires authentication` |
| same API, sibling repos `minio/console`, `minio/warp` | **200** with `"is_public": true` |

Read together, the last two rows are the ones that change the plan: a deleted repository answers
`404`, and a *private* one answers `401`. The vendor's `minio/minio` repository answers 401 while other
repositories in the same namespace answer 200 as public. **The bytes are almost certainly still there;
the door was closed to anonymous readers.** That is a different problem from "the image disappeared",
and it is the problem that decides which options are executable.

The failure is also visible in the project's own machinery, not only in CI logs: running the E2
generator in read-only check mode refuses and stops, leaving the tracked artifact untouched —

```
$ python scripts/release/third_party_registry_evidence.py --check
refused: quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z as RELEASE.2025-04-22T22-12 answered HTTP 401
the previous evidence file, if any, is left untouched
```

— which is the correct behaviour of a gate that would rather stop than write weak evidence, and a
useful sign that the repair, whatever it is, will be *checked* rather than waved through.

Certification impact, already measured in the closure round: Linux at container initialisation, K8s at
the MinIO rollout wait, GA at its cluster deploy step, all on the same dependency
(`docs/quality/cap-post-a22-closure-2026-09-24.md` §N/§O).

## B. Reference-site inventory

Every place the current coordinate lives, from a repo-wide read (line numbers as of `a378c3d`):

| # | site | form | who checks it |
| - | ---- | ---- | ------------- |
| 1 | `docker-compose.yml:101` | `image: quay.io/minio/minio@sha256:a1ea29fa…` | F-20 lock↔site test, compose contract tests |
| 2-4 | `.github/workflows/cap-linux-certification.yml:91,173,263` | service-container `image:` ×3 | same |
| 5 | `.github/workflows/cap-k8s-certification.yml:149` | inline pod manifest | same |
| 6 | `.github/workflows/cap-ga-certification.yml:176` | inline pod manifest | same |
| 7 | `.github/workflows/cap-ga-reliability.yml:170` | inline pod manifest | same |
| 8 | `scripts/certification/setup.sh:52` | `docker run … quay.io/minio/minio@sha256:…` | same |
| 9 | `deployment/third-party-images.json:25-66` | `registry`, `repository`, `tag`, `digest`, `image_ref`, `previous_ref`, `previous_ref_status`, `provenance.*`, `referenced_by`, `derived_by`, `platform_digest_linux_amd64` | schema + composition + evidence tests |
| 10 | `deployment/third-party-images.json:6-10` | `policy.minio_object_store` — `form`, `why`, `upgrade_procedure` (the procedure names `quay.io` explicitly) | reviewed, not executed |
| 11 | `docs/quality/artifacts/registry-resolution/third-party-registries.json:146-157` | generated evidence, `ref: quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z` | regenerated by the generator; asserted by the lock tests |
| 12 | `backend/tests/test_third_party_registry_evidence.py:579,592-595` | literals: a parse case `quay.io/minio/minio:latest`, and asserts `reference == "RELEASE.2025-04-22T22-12-26Z"`, `pinned_digest.startswith("sha256:a1ea29fa")` | the tests themselves |
| 13 | `backend/tests/test_third_party_image_lock.py:51,779-781,892` | `STALE_MINIO_REF` (the *retired* `minio/minio` form) and negative-control fixtures | the tests themselves |
| 14 | `docs/known-issues.md:107-119` | the standing limitation, naming quay.io and this digest | prose |
| 15 | narrative history — `CHANGELOG.md:712-719,835-845`, `docs/quality/cap-1.0.6-rc1-release-certification-2026-09-19.md:231-267`, `…/cap-post-a22-closure-2026-09-24.md`, `…/cap-post-rc-batch-2-design-freeze-2026-09-22.md` | what was true when written | **not migrated**; historical reports are dated statements |

Two sites are *derived* rather than copied and must not be touched: `backend/tests/test_phase_28_7_ga_certification.py`
reads `image_ref` out of the lock file precisely so a migration cannot half-apply, and the closure
round's `_tmp/closure_sealed_audit.py` reads the sealed release's digests, which do not involve MinIO.

Nine editable coordinate sites (1-8 plus the lock entry), one generated artifact, four test/literal
sites, and one limitation paragraph. That is the "no half-migration" surface.

## C. Vendor-supported source investigation

Answered by documentary research (vendor README, dated commits and issues on `github.com/minio/minio`,
the vendor's own 410 statement at `dl.min.io`, its trademark page and its AIStor docs) plus the live
probes in §A. Quoted findings:

1. **The community edition is source-only.** The repository README states: *"The MinIO community
   edition is now distributed as source code only. We will no longer provide pre-compiled binary
   releases for the community version."* Co-founder comment, issue #21647 (2025-10-18): *"This project
   is a source only distribution now, if you want to build containers you need to build them
   yourselves."* The switch commit is `9e49d5e7` (2025-10-15).
2. **The repository is archived and read-only** (archived by the owner 2026-04-25; last push
   2026-04-24), with a README banner from commit `7aac2a2c` (2026-02-12): *"THIS REPOSITORY IS NO
   LONGER MAINTAINED."* The earlier "maintenance mode" commit `27742d46` (2025-12-03) and the locked
   issue #21714 record the transition.
3. **`dl.min.io` answers HTTP 410 with an official statement** covering *"all community releases and
   … all hotfix builds"*, and explicitly saying no security advisories will be produced. This
   contradicts the README's claim that historical binaries remain there.
4. **No vendor page names any anonymously pullable community container any more.** `docker.io/minio/minio`
   and `quay.io/minio/minio` appear only in *frozen* artifacts (the archived `docs/docker/README.md`
   and `helm/minio/values.yaml` at the old tag). The only live vendor container distribution is
   **AIStor** at `quay.io/minio/aistor/minio`, documented on the vendor's own domain.
5. **The September-2026 visibility change has no vendor statement.** The closest official response is
   a MinIO employee in `minio-java` #1722 (2026-09-17), asked "why you delete docker/minio/minio
   repository and ALL images": *"This is not related to minio-java issue. If you are looking for
   AIStor MinIO, follow https://quay.io/repository/minio/aistor/minio…"* — no explanation, a
   redirection to the paid line. Everything else on the timing is secondary (community threads dated
   2026-09-12..14). **So the outage is confirmed by measurement here and unexplained by the vendor.**
6. **AIStor is anonymously pullable — measured** (`…/minio/aistor/minio:latest` → 200, OCI index,
   `sha256(body) == Docker-Content-Digest`, exactly one `linux/amd64 child`). It is nonetheless *not*
   a new home for the same dependency: AIStor Server requires a **license key to install**, ships as
   Free / Enterprise Lite / Enterprise tiers, and its license *"limits any redistribution or resale of
   the AIStor software"*. Adopting it is a licence decision, not a re-pin.
7. **Rebuilding the pinned image from the signed tag is not achievable, and never was what the vendor
   shipped.** At `RELEASE.2025-04-22T22-12-26Z` the plain `Dockerfile` is `FROM minio/minio:latest`
   (the deleted image), and the real recipe `Dockerfile.release` does
   `FROM golang:1.24-alpine` … `FROM registry.access.redhat.com/ubi9/ubi-micro:latest` while
   **downloading the vendor's prebuilt binary** from `dl.min.io/…/archive/minio.${RELEASE}` and
   minisign-verifying it. Release images were therefore *built from binaries, not from the git tag* —
   and that URL is now 410. Unpinned `:latest` bases plus `apk add -U` mean a rebuild cannot equal the
   recorded digest.

**Conclusion of Stage 2: `NO PUBLIC VENDOR SOURCE FOUND`** for an anonymously pullable, AGPL,
community-licensed MinIO server image. No community image was substituted on the strength of a name
(§C.8 in the option table below explains why the two look-alikes found are treated as Option D, not
Option A).

## D. Option A — move to another official / vendor-supported public MinIO source, digest pinned

| axis | assessment |
| ---- | ---------- |
| premise | survives only if a vendor-published, anonymously pullable, community-licensed image exists |
| measured | `docker.io/library/minio` → 401 with a valid anonymous token (tag **and** digest); `registry-1.docker.io/minio/minio` → 401; `quay.io/minio/minio` → 401; `public.ecr.aws/minio/minio` → **404 `NAME_UNKNOWN`** (namespace exists, repository does not); `ghcr.io/minio/minio` → no anonymous token (403) |
| end-user compose | n/a |
| CI | n/a |
| credential requirement | would require the vendor to reopen public reads — a decision only the vendor can make |
| supply-chain provenance | best of all options if it returned |
| licensing | unchanged (AGPLv3) |
| digest stability | the pinned digest is unchanged, so all existing evidence would still describe it |
| maintenance | zero change: one coordinate, no new mirror to feed |
| certification impact | none beyond a re-run once a source answers 200 |
| migration compatibility | total (same bytes) |
| availability risk | **this option is exactly the risk that just fired** — a third party's visibility switch ends the release line |

**Status: not available.** Option A stays on the list because it is the cheapest fix if the vendor ever
reopens the repository, and the design should keep a probe for it (§L, V-1): a single
`manifest by digest` GET returning 200 makes A immediately preferable to everything below.

## E. Option B — project-controlled mirror of the exact vendor bytes

Mechanically verified as sound, blocked on one input the project does not have.

**Feasibility of the destination — measured, not assumed.** A public package in this project's own ghcr
namespace is anonymously pullable through exactly the handshake the E2 generator performs:
`ghcr.io/laolaola278-dev/cap-backend:1.0.6-rc1` → token issued, manifest 200,
`Docker-Content-Digest = sha256:a733b90c7a84…` — the same digest §K of the closure report verified on
ghcr today. So `ghcr.io/laolaola278-dev/<mirror>` satisfies pullability, digest pinning and the
generator's rules with **no change to the generator's code path** (its `host()` falls through to
`ghcr.io`, and it already documents the ghcr placeholder-scope trap).

**The blocker is the source, not the destination.** To copy the *exact* bytes someone must be able to
read them. Measured: no anonymous source serves this digest (Docker Hub 401, Quay 401, ECR 404,
`mirror.gcr.io` 404, `public.ecr.aws/upstream-mirror/…` 404 under both plausible paths, `ghcr.io` no
namespace). Quay answering 401 rather than 404 (§A) says the content is likely intact behind
authentication. Remaining routes, in descending order of likelihood:

1. **A cache someone already holds** — any developer machine, on-prem registry or CI cache that pulled
   `quay.io/minio/minio@sha256:a1ea29fa…` before 2026-09-24 and can `docker save` it. Unverified from
   this box: the Docker daemon here is not running (`docker info` → "failed to connect …
   dockerDesktopLinuxEngine"), so *this* machine's image store was not examined. **Check it before
   anything else**; it is the difference between a one-day fix and a product decision.
2. **A vendor grant** — a one-time credentialed pull (see Option C; the credentials belong to the
   vendor relationship, not to the repository, and §"prohibited" forbids inventing or persisting them).
3. **A third-party copy of the identical manifest** — acceptable *only* if
   `Docker-Content-Digest` of the candidate equals `sha256:a1ea29fa…` byte-for-byte, because then
   content addressing, not trust in the copier, is what proves it. A verified-by-digest hit would make
   this a copy operation, not a rebuild.

**Contract consequences that must not be missed.** The lock schema requires a non-empty `tag`
(`test_third_party_image_lock.py:268`), and the generator resolves targets *by tag* then asserts the
digest (`collect_targets` builds the coordinate from `entry["tag"]`). So a mirror that is only pullable
by digest would break evidence generation. The mirror must therefore **publish the same tag**
(`RELEASE.2025-04-22T22-12-26Z`) beside the digest — which keeps the tag as a resolution key inside a
namespace the project controls, where its mutability is a property of our release process rather than
of a third party's.

**Authorization gate.** Option B is *not executable under the present brief*: it requires (i) an
explicit approval to write to a registry, and (ii) the licence check in §H to come back permissive for
the specific act of republishing an unmodified vendor artifact under a neutral name.

## F. Option C — authenticated vendor pull

| axis | assessment |
| ---- | ---------- |
| mechanism | a Quay (or vendor-supplied) robot account, `docker login quay.io` in the jobs and in `~/.docker/config.json` for compose users |
| end-user compose | **does not fix it.** An ordinary `docker compose up` from a fresh clone would fail with the same 401 unless every user is issued credentials — the brief names this explicitly, and it is fatal to C as a *product* remediation |
| CI | workable today, and would unblock certification fastest |
| credentials | a secret the project does not hold and may not fabricate; the vendor's last public statement redirects users to a paid product rather than offering access |
| supply-chain | unchanged bytes if the pull succeeds |
| licensing | no redistribution issue at all — pull-through only |
| digest stability | the pinned digest is unchanged |
| maintenance | a credential with no expiry guarantee, whose owner has an incentive to withdraw it (they already closed the anonymous path once) |
| certification impact | a workflow edit (login step + secret) → new candidate + recertification, and a *dependency of certification on a secret* that F-49's governance line would want described |
| migration compatibility | total |
| availability risk | high: this is the same single-vendor-polity failure that produced F-56 |

**Status: viable only as an emergency bridge to fetch the bytes for Option B**, never as the shipped
state — because the product's default deployment path must not require the customer to hold vendor
credentials.

## G. Option D — replace MinIO with another S3-compatible dependency

The only option executable without third-party cooperation. What "replace" costs here is bounded by a
narrow surface: `backend/app/acquisition/store.py` uses the `minio` Python SDK (`from minio import
Minio`, `minio.error.S3Error/InvalidResponseError`) against `OBJECT_STORE_ENDPOINT`,
`settings.object_store_backend` is `local | s3` (a `local` backend already exists), and `mc` appears
only in the DR/backup gates of the GA and soak workflows, installed from a pinned GitHub release asset
verified against its published sha256 — measured reachable today by one instance: the `supply-chain`
job of GA run `36004166008` at `a8d3fb2` succeeded on that download while `ga-certification` in the
same run failed on the MinIO deploy. (The GA run one commit earlier, `36000276326`, was cancelled while
waiting rather than run, so it says nothing either way — F-54.)

Candidates, each judged on whether it can be anonymously pulled and digest-pinned at all — measured
here, in one pass, with the same three checks the E2 generator applies:

| candidate | anonymous index GET | digest arithmetic | linux/amd64 children | license | note |
| --------- | ------------------- | ----------------- | -------------------- | ------- | ---- |
| `registry-1.docker.io/chrislusf/seaweedfs:latest` | **200** | `sha256(body)==served` ✔ | exactly 1 (platforms 386/amd64/arm/arm64) | Apache-2.0 | config labels: `org.opencontainers.image.vendor=Chris Lu`, `version=4.47`, built 2026-09-14 — actively published |
| `registry-1.docker.io/dxflrs/garage` (Garage) | tags/list **200** but no `latest` (tags are 40-char commit ids) | n/a | n/a | AGPL-3.0 | pullable and pinning-able once its documented tag is used. Several guessed `ghcr.io/.../garage` paths failed in two different ways — no anonymous token issued, and tags/list 404 — which says only that *those* paths are not public repositories, not that Garage publishes nothing there; the 200 tag list under the Hub namespace the project's own docs name is the evidence used here |
| `registry-1.docker.io/pgsty/minio:latest` (community MinIO fork) | **200** | ✔ | exactly 1 | AGPLv3 code, fork not vendor-supported | tags mirror MinIO's `RELEASE.…` cadence (through `RELEASE.2026-08-04T00-00-00Z`); config labels name `maintainer: pgsty`, `org.opencontainers.image.version: RELEASE.2026-08-04T00-00-00Z`, base `ubi9-micro` — **the same base family as the vendor's own release recipe**, which is the strongest fidelity signal available without the vendor |
| `quay.io/ceph/ceph:v19.2.1` (RADOS Gateway) | **200** | ✔ | 1 (amd64, arm64) | LGPL/Apache | operationally far heavier than CAP needs for evidence storage |
| `registry-1.docker.io/scality/cloudserver:latest` (Zenko) | tags/list 200, no `latest` | n/a | n/a | Apache-2.0 | not further assessed |

Compatibility work D requires, and it is real: S3 semantics CAP actually exercises are
`put_object`/`get_object`/`delete`, **versioning**, **multipart evidence upload**, the DR restore path,
and presigned URLs — each has to be re-certified against the new server, because "S3-compatible" is a
claim, not a guarantee. The `minio` Python client would keep talking S3 to a foreign server, but every
`S3Error` code path and the `mc`-based DR gates need re-running, not re-reading. A fork with MinIO
lineage (pgsty) minimises this because it is the same code; a different implementation (SeaweedFS,
Garage) minimises the *vendor* risk, which is what the incident was about.

**Status: executable, at the price of a full compatibility certification cycle**, and — if the pinned
version matters — a data-format question that §L answers by keeping it to a fresh-bucket smoke test
rather than pretending an upgrade is a migration.

## H. Licensing / redistribution considerations

This is the check the brief demands before *recommending* a mirror, and the answer is not a slogan:

- **Copyright licence: AGPLv3**, unchanged at archival. The vendor's own README invites remixing under
  AGPLv3 while warning that commercial/proprietary use "including repackaging or reselling
  services/features, is done at your own risk", and `COMPLIANCE.md` disclaims compliance determinations.
  AGPLv3 §6 permits conveying unmodified copies with the licence retained; for a network service it
  obliges offering the corresponding source, which CAP does not run for third parties — this is a
  deployment-shape fact worth restating in the lock file rather than assuming.
- **The operative constraint is trademark, not copyright.** The MinIO Trademark Policy (updated
  2025-08-29) states: *"You can distribute unchanged official binaries downloaded from
  https://min.io/download to anyone in any way subject to governing law, relevant terms of the license,
  without receiving any further permission from MinIO. However, you must not remove or change any MinIO
  Trademarks."* It prohibits redistributing modified software under MinIO marks and requires renaming
  modified products/binaries.
- **On container images the policy is silent.** It speaks of "binaries downloaded from
  min.io/download" — a channel that now returns 410. So neither an explicit permission nor an explicit
  prohibition covers "re-host the vendor's unmodified image in our own registry". The honest reading is
  that the permission covers *unchanged official artifacts*, that a verbatim copy is unchanged (its
  digest is literally the same bytes), and that the naming must not imply vendor authorship:
  `ghcr.io/laolaola278-dev/cap-object-store` with provenance pointing at the vendor's tag and digest
  is the shape that satisfies both the silence and the rename requirement. A name containing "minio"
  in a namespace we control is the weaker position, and a *rebuilt* image keeps the MinIO name is the
  prohibited one.
- **AIStor is a different answer**: its subscription license *limits redistribution or resale*, so
  mirroring AIStor is not available on the same reasoning, and adopting it needs a licence key and a
  legal review — Option D with extra steps.
- **A rebuilt-from-source image** (§C.7) would be a *modification* for trademark purposes and must be
  renamed and de-branded; the AGPL source obligation then clearly applies to whatever we ship.
- **`mc` stays as it is**: pulled from pinned GitHub release assets with a sha256 check, unchanged by
  any option above, and measured reachable today.

Legal review is named as an authorization item in §O: this report states what the vendor's documents
say and where they are silent; it does not decide the question.

## I. Recommended option

Recommendation, in the order it should be *attempted*, because the honest answer depends on one fact
this round could not establish:

1. **First, spend one hour on byte recovery, not on a decision.** Search the caches that could already
   hold the image — every machine that has run `scripts/certification/setup.sh` or a compose stack, plus
   any registry the team operates. If `sha256:a1ea29fa…` is found anywhere, **Option B becomes the
   recommendation** and the whole design collapses into: push it verbatim into a public
   `ghcr.io/laolaola278-dev/…` repository tagged `RELEASE.2025-04-22T22-12-26Z`, re-point the nine
   sites in §B, regenerate the evidence, recertify.
2. **If the bytes cannot be recovered: choose Option D, and choose the MinIO-lineage fork route as the
   migration shape** — but adopt it as a *deliberate dependency change* with the pgsty-or-equivalent
   fork evaluated against its own provenance audit (`vcs-ref` resolvable in the fork's repository, a
   signed release, an actual maintenance commitment, a licence statement), not as a stand-in for the
   vendor. Between a foreign-but-independently-maintained server (SeaweedFS Apache-2.0, measured
   pullable, actively built) and a fork of the archived code, the deciding question is which risk the
   project would rather own: implementation divergence from S3 semantics, or dependence on a
   volunteer's continuity. **This report does not choose for you; §O puts the two as a single named
   decision.**
3. **Reject A as unavailable, and C as a shipped state.** C remains legitimate for exactly one use: a
   one-time credentialed fetch to enable B, if a vendor relationship can produce it.
4. **Whatever is chosen, refuse the cheap-looking alternative**: raising the 120 s K8s timeout, or
   pointing compose at `:latest` of some image whose name contains "minio". The first hides an
   unavailable dependency behind a longer wait; the second replaces an immutable pin with a mutable one
   and trusts a name.

Why B over D when B is possible: it preserves the certified bytes, the version matrix, all evidence
already recorded, and every behavioural test's meaning — and it converts a third party's availability
decision into a copy operation we control, which is the outcome F-20's whole digest-pinning design was
reaching for.

## J. E2 / lock migration

Design, per Stage 5. All of it is *after* approval; nothing here was executed.

1. **Resolve the new coordinate the way the gate will**: `python scripts/release/third_party_registry_evidence.py`
   for a candidate source, which requires the served `Docker-Content-Digest` to equal `sha256(body)`,
   refetches by that digest for byte equality, and refuses unless the index carries exactly one
   `linux/amd64` child. §A/§G's probes used the same three checks, so a candidate that passes here has
   already survived them.
2. **Keep the tag, and understand why.** The schema asserts a non-empty `tag` and the generator builds
   its target coordinate from `{registry}/{repository}:{tag}`, asserting the digest afterwards. A
   mirror must therefore publish a tag (`RELEASE.2025-04-22T22-12-26Z`) — inside our namespace, where
   "mutable" means "changes when our release process says so". The alternative, teaching the generator
   to resolve digest-only, is a certification-generator change and owes the four measurements §G of the
   closure report established before it can ride any candidate.
3. **Lock entry fields to update, all in one commit**: `registry`, `repository`, `tag`, `digest`,
   `image_ref` (which must recompose exactly as `f"{registry}/{repository}@{digest}"` — a test asserts
   the composition, and a half-edited entry is caught by it, measured in §K), `previous_ref` ←
   `quay.io/minio/minio@sha256:a1ea29fa…`, `previous_ref_status` ← a dated sentence naming the 401 with
   an anonymous token and pointing at this report, `provenance.verified_on`, `provenance.repository_namespace`
   (for B: *this project*, mirrored verbatim by digest, with the vendor's own signed tag and image
   config labels carried forward as the upstream chain), `provenance.evidence` (the regenerated
   artifact), `provenance.open_gap` (state honestly what cannot be proven — for a fork, that the
   lineage is the fork's and not the vendor's), `referenced_by` (all §B sites), `derived_by`, and
   `platform_digest_linux_amd64` from the child manifest.
4. **`policy.minio_object_store`** must stop naming `quay.io` in its `upgrade_procedure` and describe
   the source we actually have, including for B the verification that the mirror's digest equals the
   vendor's recorded one, and for D the fact that the "same product" claim no longer holds.
5. **F-24 and F-37 stay closed by construction, and the design depends on it**: every external compose
   image must be in the lock and vice versa (F-24), and the cited evidence file must be tracked and
   assert exactly the locked digests (F-37's pointer guard). Both fired in §K's deliberate half-migration,
   which is the proof they still hold after the change.
6. **Credential-free reproducibility is part of the contract, not a nicety.** The chosen source must be
   anonymously resolvable — the generator mints its own anonymous token from each registry's challenge
   and scans the output for secrets before writing. A private mirror would fail `--check` for every
   reviewer without credentials, which is F-56 repeating itself inside our own tooling.
7. **`mc`** needs no change; the design keeps it pinned by release + sha256 from GitHub assets and
   records that `dl.min.io` is 410 (already fenced by a test that forbids a `dl.min.io` curl).

## K. Reference-site migration

Order that cannot leave the repository in a state where a gate is lying — validated by measurement,
not by argument. A throwaway worktree at `a378c3d` with a *deliberately incomplete* migration
(`docker-compose.yml` moved to a mirror coordinate, and the lock's `registry`/`repository`/`digest`
edited but `image_ref` left un-recomposed; workflows untouched) was run through the lock contract:
**9 failed, 21 passed**, including

- `test_every_reference_site_matches_the_lock` → the six untouched workflow/setup.sh sites still name
  the old coordinate,
- `test_the_lock_names_every_site_that_pulls_a_locked_image` → the same from the other side,
- `test_digest_pinned_image_uses_an_immutable_reference` → `image_ref` no longer composes from the
  entry's own fields (the invariant that protects a hand-edited lock),
- `test_a_locked_compose_digest_is_the_one_the_evidence_measures` and
  `test_cited_evidence_is_tracked_and_asserts_exactly_the_locked_digests` → the tracked artifact
  *"measures no ghcr.io/…:RELEASE.2025-04-22T22-12-26Z, so it cannot vouch for this digest"*,
- `test_lock_file_is_well_formed`, `test_every_external_compose_image_is_in_the_lock_and_vice_versa`,
  `test_losing_or_inventing_a_locked_compose_entry_fails_in_both_directions`.

The probe commit lives on no branch and was never pushed (`git worktree remove` after; the classifier
run against it is recorded in §M). The drift checker's own negative control did *not* fire in that
state, and reading it explains why without flattering it: it compares each surface against
`entry["image_ref"]` verbatim, so the guard keeping `image_ref` honest is the composition test above —
worth knowing when writing the migration, because it means the lock's fields must be edited as one
object.

So the migration is **one commit**, in this order inside it: (1) generate and commit the evidence
artifact for the new coordinate, (2) rewrite the lock entry as a whole including `image_ref`,
`previous_ref`, `previous_ref_status`, `provenance`, `policy.minio_object_store`, (3) re-point the
six workflow/service sites plus `docker-compose.yml` plus `scripts/certification/setup.sh`, (4) update
the two literal-bearing test files (§B rows 12-13) — keeping `STALE_MINIO_REF`'s retired Docker Hub form
as a stale ref and *adding* the quay coordinate as the newly-retired one so a future reader cannot
re-adopt it — (5) update `docs/known-issues.md:107-119` and F-56, and (6) leave the dated historical
reports and CHANGELOG entries alone, adding a new CHANGELOG entry instead. A single
`git grep 'a1ea29fa\|quay.io/minio'` after the commit must return only historical and "retired" sites;
that grep is the no-half-migration check a human can run, and §L makes it a gate step.

## L. Validation matrix

Pre-candidate-freeze, in dependency order. V-1 is the only step that can retire this whole design.

| # | check | command / shape | passes when |
| - | ----- | --------------- | ----------- |
| V-1 | source recovered? (decides B vs D) | `docker pull <new-coordinate>` from a **clean, unauthenticated** machine *and* from a GitHub runner | 200/digest matches on both; a cached-only copy fails this |
| V-2 | registry contract | `python scripts/release/third_party_registry_evidence.py --check` | no `Refusal`; digest arithmetic + refetch + exactly one `linux/amd64` child |
| V-3 | lock ↔ sites converged | the repo's own contract tests + `git grep 'quay.io/minio'` | `test_third_party_image_lock.py`, `test_third_party_registry_evidence.py`, `test_compose_env_contract.py` green and grep hits only historical/retired mentions |
| V-4 | compose validity | `docker compose config` (and `--images`) | renders; names the pinned coordinate, no mutable tag |
| V-5 | compose readiness | `docker compose up -d minio` + its healthcheck `/minio/health/live` | healthy within its own timeout, on a **fresh clone with no credentials** |
| V-6 | S3 API smoke | existing `pytest -m object_store` surface, plus explicit create-bucket / put / get / delete | all four, through the real client `store.py` uses |
| V-7 | versioning + multipart + DR | the acquisition evidence paths the certification already exercises (multipart upload, versioned bucket, restore via `mc`) | green, because these are where "S3-compatible" claims actually break |
| V-8 | Linux certification init | `cap-linux-certification.yml`, release layer | service containers start (this is where F-56 announced itself) |
| V-9 | K8s infra rollout | `cap-k8s-certification.yml` | `deployment/minio` becomes ready inside the 120 s budget **without the budget being changed** |
| V-10 | GA prerequisite | `cap-ga-certification.yml`, `cap-ga-reliability.yml` | cluster A deploy step passes; soak's MinIO outage-drill scenario runs |
| V-11 | fresh-clone reproducibility | clean VM: clone → `docker compose up` → run an incident end-to-end | works with no secrets and no prior image cache — the property F-56 removed |
| V-12 | only if the version changes | A/B data-compatibility run: write on the old server, read on the new (and vice versa) for versioned + multipart objects | either passes, or the migration document records that it cannot and requires a re-upload path |

V-11 is the line that separates "CI works again" from "the product is fixed", and it is the one a
credential-based shortcut cannot satisfy.

## M. Classifier / recertification impact

Measured, not assumed, using the throwaway migration commits (§K). With `dea8c6f` as the certified
SHA and a commit that changes only `docker-compose.yml` + `deployment/third-party-images.json` as the
candidate:

```
RESULT: RECERTIFICATION REQUIRED (runtime-affecting change found)   exit 2
  deployment/third-party-images.json  category=deployment  runtime_affecting=true
  docker-compose.yml                  category=deployment  runtime_affecting=true
```

Both files classify as `deployment` and runtime-affecting — so unlike the wording round, this is a case
where the classifier answers correctly, and **no classifier change is proposed or permitted** (Stage 8;
F-49's design work is the place where its path-keying is discussed). Consequences to carry into the
implementation round:

- A new candidate SHA is frozen **after** the migration commit and its evidence regeneration; `dea8c6f`
  is not reused, and its certification stays valid as history for the release it certified.
- The implementation touches `.github/workflows/` and `scripts/certification/` too, so F-54's and
  F-55's workflow-wording fixes can ride the same candidate at no extra recertification cost — that is
  the cheapest possible payment for those two items and the design says so explicitly (§N).
- The certification rounds cannot even be *attempted* until the source answers, so V-1/V-2 are the gate
  before the gate.

## N. F-55 diagnostic follow-up

Independent of which option wins, the K8s job should be able to say what happened. Designed change to
`cap-k8s-certification.yml`'s failure dump (currently `kubectl get pods -n cap`, `-n cap-sandbox`,
`get events -n cap` — never `cap-infra`, which is why F-56 took a hand-written registry probe to
identify):

```
kubectl -n cap-infra get pods -o wide
kubectl -n cap-infra describe pods
kubectl -n cap-infra get events --sort-by=.lastTimestamp | tail -40
# the pull itself, because `ImagePullBackOff` vs `ErrImagePull` vs a 401 in the
# pod events is the difference between "registry refused", "digest missing" and "network down"
kubectl -n cap-infra get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[*].image}{"\t"}{.status.containerStatuses[*].state.waiting.reason}{"\n"}{end}'
```

plus the same three additions in `cap-ga-certification.yml` and `cap-ga-reliability.yml`, whose cluster
manifests are inline and share the blind spot. This is **diagnostic hardening only**: it must not be
presented as progress on F-56, it changes no wait, and no threshold. It also deserves a *negative
control* of its own, in keeping with the rest of the programme — one test asserting that the dump block
names every namespace any job creates infrastructure in, so the next infra namespace added cannot be
forgotten the way `cap-infra` was.

## O. Decisions requiring authorization

1. **The byte-recovery hunt (V-1)** — cheap, read-only, but it means touching other people's machines
   or caches; name who may look where.
2. **Registry write** for Option B: creating a public `ghcr.io/laolaola278-dev/<mirror>` package and
   pushing the vendor's bytes. Explicitly forbidden under the present brief; needs an explicit yes.
3. **Legal/trademark sign-off** on §H: conveying an *unmodified* vendor image under a neutral name in
   our own namespace, given the vendor's policy speaks of binaries and is silent on images.
4. **Or the Option D decision**, if the bytes are unrecoverable: which replacement — a MinIO-lineage
   fork with an audit, or an independent S3 implementation (SeaweedFS / Garage) — accepting a
   compatibility certification cycle (V-6..V-12) either way.
5. **Whether to contact the vendor** for a one-time fetch or a public reopen, and who owns that
   communication; §C.5 says their public posture so far is to redirect users to a paid product.
6. **AIStor is a separate product decision** (licence key, redistribution limits) and is *not* implied
   by any option above.
7. **Credential scope if C is ever used as a bridge**: a short-lived pull secret used only to copy, never
   stored in the repository, never in CI as a standing secret. Not authorized by this design.

## P. FINAL STATUS

**F-56 REMEDIATION DESIGN READY — IMPLEMENTATION REQUIRES APPROVAL**

Ready because a workable path exists in both branches of the one unknown: if the exact bytes can be
recovered, Option B restores everything at the lowest cost and the design for it is complete (§E, §J,
§K, §L); if they cannot, Option D has measured, anonymously pullable, digest-pinnable candidates and a
compatibility matrix that makes its cost explicit (§G, §L). Not "blocked", because
`NO PUBLIC VENDOR SOURCE FOUND` closes Option A and nothing else.

Blocked on three answers only the project can give: **can we still obtain `sha256:a1ea29fa…`** (§O.1),
**may we re-host it** (§O.2-3), and if not both, **which replacement do we accept** (§O.4).

What this round deliberately did not do: no registry write, no mirror push, no release tag, no
publication, no certification dispatch to watch an outage, no mutable tag adopted, no timeout raised,
no credential invented or persisted, `dea8c6f` untouched, its certification described as valid history
rather than as a failure, and sealed `v1.0.6-rc1` unmodified and re-verified intact.
