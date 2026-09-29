# CAP F-56 — Option D compatibility bake-off (2026-09-29)

**Verdict: `OPTION D BAKE-OFF INCONCLUSIVE`**

Measured at commit `7ebc85d` (the `main` tip at execution time), on 2026-09-29 between 05:41Z and
07:00Z, on this operator workstation. No tracked file other than this report and the `CHANGELOG.md`
entry was touched; `docker-compose.yml`, `deployment/third-party-images.json`, every workflow, every
test and the classifier are byte-identical to `7ebc85d`. No image was mirrored or pushed, no release
tag was created, no certification round was dispatched, nothing was published, and no candidate was
frozen.

The commit that carries this report classifies as
`python scripts/release/classify_diff.py dea8c6f2f576bec667ee27dc8a9458673ca0982e HEAD` →
`RESULT: runtime certification INHERITED (release_metadata_only=True)`, `runtime_affecting: false`,
with both changed files in category `docs` — so these two files owe no re-certification and none was
requested. The *candidate* discussed in the report would classify differently: a compose + lock +
workflow migration measures `deployment` / `runtime_affecting=true` / `RECERTIFICATION_REQUIRED`, which
is why §Q puts a development-mode round, not a docs verdict, in front of any adoption.

The verdict is inconclusive **not because the candidates performed badly** — the one candidate this
host could execute passed every measurement put to it — but because three of the comparisons the
stage depends on could not be executed here at all:

1. there is **no vendor baseline**: the pinned MinIO coordinate is still closed to anonymous pulls
   (§G) and this machine has **no container runtime** (§C), so MinIO itself could not be run;
2. **Garage could not be executed** at all (§M): its image is Linux-only and the vendor publishes no
   Windows build, so on a host with no Linux runtime there is nothing to run;
3. the **deployment surfaces were never exercised** (§O): the shipped compose service, the two inline
   Kubernetes Deployments, the health probes and the certification jobs were all read and analysed,
   but nothing was run inside a container, because there is no container engine.

What *was* executed is recorded in enough detail that the next round can start from measurement
instead of from prose: **SeaweedFS 4.48 ran CAP's own object-store contract, unmodified, to
19/19 green, including the `mc` disaster-recovery path and an outage drill** (§I). Every failure seen
on the way to that result was diagnosed to a configuration cause in this harness, not to the
candidate, and each of those diagnoses is written down with its evidence rather than quietly
dropped (§H, §J, §K).

---

## §A What was authorized, and what this round deliberately did not do

The approved stage is a compatibility bake-off for F-56 Option D (replace the object store with a
different S3-compatible product). Explicitly not done, per the authorization:

- the formal object-store coordinate in `docker-compose.yml` and in
  `deployment/third-party-images.json` is unchanged (both still name
  `quay.io/minio/minio@sha256:a1ea29fa…`);
- no candidate commit was created and no certification was dispatched;
- no release tag, no publication, no promotion;
- no third-party image was pulled, mirrored or pushed; no vendor credentials were requested,
  created or used;
- the commit classifier, the publication gate and the existing S3/DR assertions were not modified,
  relaxed, or re-scoped to make a candidate fit;
- the sealed `v1.0.6-rc1` release was not touched, and was re-audited read-only at the end of this
  round: **`SEALED RELEASE INTACT`** — `v1.0.6-rc1` still `prerelease=True` published
  `2026-09-21T02:38:59Z`, all five `1.0.6-rc1` ghcr tags still present, each resolving to a digest
  and re-reading by digest to the same digest, all five digests byte-identical to the Batch 2 record
  (`cap-backend sha256:a733b90c…`, `cap-frontend sha256:e1b1889a…`, `cap-egress-proxy sha256:8ab8c234…`,
  `cap-sandbox-http sha256:36bb2f79…`, `cap-sandbox-browser sha256:b3696188…`), and `v1.0.5` still the
  newest non-prerelease release.

Everything that ran did so inside the gitignored `_tmp/` tree, on loopback interfaces, using the
credentials the repository already documents for local development (`capadmin`/`capadmin123`,
`docker-compose.yml` and `backend/tests/test_phase_28_4_object_store.py:20-21`).

## §B How the contract was derived (from code, not from documentation)

The contract below was read out of the product and its certification suite, not from
`docs/`. It is the list of operations a replacement must honour, with the source of each.

| # | Operation the product actually performs | Where |
|---|---|---|
| 1 | build a MinIO-SDK client from `endpoint/access/secret/bucket`, `secure=False` | `backend/app/acquisition/store.py:190-211` |
| 2 | `make_bucket(bucket, location="us-east-1")`, tolerating only `BucketAlreadyOwnedByYou` / `BucketAlreadyExists` / `InvalidResponseError` | `store.py:221-234` |
| 3 | key layout `sha256/<digest[:2]>/<digest>`, content-addressed, never mutated | `store.py:213-219` |
| 4 | `stat_object` before `put_object(BytesIO, length=len(data), metadata={})`; existing key ⇒ no re-upload | `store.py:255-268` |
| 5 | **no user metadata on objects** — the deliberate zero-`x-amz-meta-*` workaround for a legacy MinIO signing quirk | `store.py:249-256` |
| 6 | `get_object`, read fully, then recompute `sha256` and **refuse the object if it differs from the key** | `store.py:283-299` |
| 7 | `stat_object` for existence, `stat.metadata` + `last_modified` for object age (the orphan GC's only clock) | `store.py:301-321` |
| 8 | `list_objects(bucket, prefix="sha256/", recursive=True)` — the GC's complete scan | `store.py:323-336` |
| 9 | `remove_object` (single key), treated as best-effort | `store.py:337-342` |
| 10 | 20 MiB ceiling per object (`max_object_bytes` default) | `store.py:198`, `store.py:239-242` |
| 11 | readiness gate in the suite is a **plain TCP connect** to `127.0.0.1:9000` | `backend/tests/conftest.py:200` (`minio_available`) |
| 12 | the suite's own skip-if-absent probe calls `store.health()` → `make_bucket` | `test_phase_28_4_object_store.py:24-38` |
| 13 | DR: `mc alias set`, `mc mb --ignore-existing`, `mc mirror --overwrite` (object-level export/import) | `scripts/certification/backup_cluster.sh:47,49`, `restore_cluster.sh:43-46` |
| 14 | outage drill: scale the object-store Deployment to 0 replicas, assert the gate fails, restore, assert data survives | `.github/workflows/cap-ga-reliability.yml` (the `minio` Deployment at :150-178) |

**Two things the previous design report got wrong, corrected by measurement here.**

*Multipart uploads are inside the contract.* The 2026-09-25 design report inferred from the source
that CAP "never calls multipart". No CAP code names the multipart API — but the pinned
`minio` SDK silently switches to it once an object is large enough, and the store's own ceiling is
20 MiB. Measured through `S3EvidenceStore.put()` with an adequate server configuration (§K):

| object size | write path observed | ETag returned |
|---|---|---|
| 1 MiB | single `PUT` | `7e79d9eddb942fbcf8868d2a1601b0d4` (content MD5 form) |
| 8 MiB | multipart, 2 parts | `bba7ac3bf13e50249bb8ab1324260597-2` |
| 20 MiB (×3 distinct payloads) | multipart, 4 parts | `…-4`, each byte-exact and sha256-verified on read-back |

So a replacement must implement `CreateMultipartUpload`/`UploadPart`/`CompleteMultipartUpload`
correctly. This matters to the option set beyond the bake-off: it is one more reason Option A
(rebuild the pinned MinIO release from the vendor's archived `Dockerfile.release`) is closed, and it
is a gate the `object_store` suite itself does **not** currently check (§R.3).

*Bucket name validation is client-side, not vendor-side.* One probe failed with
`ValueError: invalid bucket name cap-etagA` from `minio/helpers.py:237` before any request was sent.
Nothing about that transfers to a candidate; it is recorded so the next round does not re-read it as
an S3 incompatibility.

## §C What this host can and cannot execute (measured 2026-09-29)

| Component | State measured | Consequence |
|---|---|---|
| Docker CLI | present, `29.6.1`, API `1.55` — **client only** | `docker version` prints no Server block |
| Docker engine | `C:\Users\admin\AppData\Local\Docker\log\host\com.docker.backend.exe.log` ends with repeated `apiproxy] returning engine error` timestamped `2026-09-29T05:42:47Z` and `2026-09-29T05:43:22Z` (i.e. during this round's own `docker info`) | no container can be started |
| WSL | default version 2, kernel `5.10.16`; `wsl --status` reports the kernel **must be updated with `wsl --update`** and that automatic kernel update is disabled by system policy; `wsl -l -v` reports **no installed distributions** | Docker Desktop's WSL2 backend cannot start; no Linux userland |
| podman / nerdctl / containerd / ctr / kind / k3s / helm | none installed (`command -v` → MISSING) | no alternative runtime, no local kind cluster |
| kubectl | present only as `C:\Program Files\Docker\Docker\resources\bin\kubectl` | nothing to talk to |
| native Windows binaries | run fine (`weed.exe`, `mc.exe`) | the executable path for this round |
| PostgreSQL | none was running (`conftest.py:189 postgres_available(127.0.0.1,55432)` → False) | 7 tests skipped, 7 failed until scaffolding was added (§H) |

This is the blocker for the compose/K8s half of the bake-off. It is **not** worked around by
relaxing a gate: those rounds are reported as not executed (§O). The one action that would unlock
them is outside this stage's authorization and is listed for decision in §Q: repair the WSL kernel
(`wsl --update`, currently policy-blocked) and let Docker Desktop create its own distro, or provide
any other Linux container runtime.

## §D Candidate selection rules applied

Rules fixed *before* measuring, per the stage brief:

- a coordinate is admissible only if it is **immutable and anonymously resolvable** by the same
  handshake the E2 evidence generator uses; a `latest`-only candidate is incomplete, not scored;
- readiness must be **vendor-neutral**: `/minio/health/live` may not appear in any alternative
  candidate's PASS condition (`conftest.py:200`'s TCP probe is the neutral form, and it is what the
  suite already uses);
- a candidate is scored on the contract in §B only — no invented requirements (no versioning, no
  lifecycle, no object tagging: CAP's code never asks for them);
- result classes: `PASS`, `FAIL_COMPATIBILITY` (the candidate does not honour the contract),
  `FAIL_HARNESS_VENDOR_BINDING` (the harness demanded something MinIO-specific), and — added
  honestly, because it was needed — `NOT_MEASURABLE_IN_THIS_HARNESS`;
- the vendor's own baseline could not be run, so every performance figure in this report is
  **absolute, not a delta**.

## §E Candidate coordinates, resolved by the E2-equivalent algorithm

Probed 2026-09-29 through the proxy, anonymously, with no credentials: bearer challenge → token with
the scope named by us → index requested with an `Accept` naming only OCI index / Docker manifest-list
→ `Docker-Content-Digest` must equal `sha256(body)` → same bytes re-requested by digest → exactly one
`linux/amd64` child → config blob (following redirects, the way `urllib` does) for labels.

| coordinate | index digest | amd64 child | layers | `created` | OCI labels |
|---|---|---|---|---|---|
| `dxflrs/garage:v2.3.0` | `sha256:866bd13ed2038ba7e7190e840482bc27234c4afaf77be8cfa439ae088c1e4690` | `sha256:dac0c92add4f1a0b41035e94b41036a270ffbe88a37c7ac9c3f19e6dc5bdccf2` | 1 | `2026-04-16T20:15:36.502334Z` | **none** (`{}`) |
| `dxflrs/garage:v2.4.1` | `sha256:9c96caa2612d3411acc5b0e6701fb238dbfba33e533a6d7d3d811a4b12d0d020` | `sha256:0d7c74fc8ca6fef68a5a941c0e7558c8b1e92ba3588fa7505400e1350456c796` | 1 | `2026-09-08T08:55:29.774147Z` | **none** (`{}`) |
| `chrislusf/seaweedfs:4.47` | `sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882` | `sha256:f83509b0721dfd8e2e07faf76c0a899f67a8a889c89abe2fa0a5227ba1320362` | 10 | `2026-09-14T02:32:36.698794Z` | `…image.version=4.47`, `…image.revision=c5073360007d28385a33426a42ac3e4ec504c5a3`, `…image.vendor=Chris Lu`, `licenses=Apache-2.0` |
| `chrislusf/seaweedfs:4.48` | `sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d` | `sha256:aba492e2a4e4c90bff795745e8e660affa1f09e7650f5981bd7bccd1a06cd931` | 10 | `2026-09-28T18:41:21.744952Z` | `…image.version=4.48`, `…image.revision=530be3e37337488ecc34d58441e0bc476e121c93`, `…image.vendor=Chris Lu`, `licenses=Apache-2.0` |

All four: `sha256(body) == served digest` **True**, `refetch-by-digest byte-identical` **True**,
platform set `linux/{386,amd64,arm,arm64}` with **exactly one** `linux/amd64` child — which is the
E2 requirement (`scripts/release/third_party_registry_evidence.py:427-433`). Each carries a real tag,
which is mandatory: `collect_targets` resolves `{registry}/{repository}:{tag}` and asserts the digest
against it, so a mirror or a replacement must publish a tag, not just a digest.

Deployment-shape facts read from the same config blobs, because they decide what the manifests must
change to:

- `chrislusf/seaweedfs` `Cmd = ["mini","-dir=/data"]`, `Entrypoint = ["/entrypoint.sh"]`,
  `ExposedPorts` includes `8333` (its S3 default) and **not** `9000`; no `User` set.
- `dxflrs/garage` `Cmd = ["/garage","server"]`, **no** `ExposedPorts`, one layer, no labels — so the
  lock's current provenance recipe ("confirm the image config labels carry `release=<tag>` and
  `vendor=…`") is unsatisfiable for Garage: the digest and the tag round-trip are all it can offer.

## §F Provenance fidelity, per candidate, measured against the lock's own procedure

`deployment/third-party-images.json` `policy.minio_object_store.upgrade_procedure` demands four
things before a coordinate is adopted: resolve by tag with the index-only `Accept`; config labels
carry `release` and `vendor`; the matching **PGP-signed** vendor tag was created minutes before the
image build; then re-certify. Measured against that checklist:

| | MinIO (current) | SeaweedFS 4.48 | Garage v2.4.1 / v2.3.0 |
|---|---|---|---|
| anonymously resolvable by tag | **no** (§G) | yes | yes |
| labels carry version | yes (`release`, `version`, `vendor=MinIO Inc`) | yes (`org.opencontainers.image.version=4.48`) | **no labels at all** |
| signed release tag | yes — annotated `f19c534b…`, PGP-signed by `Minio Trusted <trusted@minio.io>` | **no**: `4.48`/`4.47` are lightweight refs pointing straight at commits `530be3e3…`/`c5073360…` — no tag object, so nothing to sign | annotated tag objects (`v2.4.1`→`cdc4ca791f…`), GitHub reports `verification.verified=False` |
| independent cross-anchor | `vcs-ref` **unresolvable** publicly (the lock's own `open_gap`) | **strong**: image label `revision=530be3e37337488ecc34d58441e0bc476e121c93` == the GitHub tag's commit == the commit string printed by the vendor's own Windows build (`version 30GB 4.48 530be3e37337488ecc34d58441e0bc476e121c93 windows amd64`) | tag→repo commit only; the image carries no revision label to cross-check |
| vendor namespace identity | official MinIO org on Quay (`is_organization=true`) | Docker Hub `chrislusf/seaweedfs`, description *"official seaweedfs docker builds"*, 27 387 016 pulls; namespace owner == image label `author/vendor` == GitHub repo owner | Docker Hub `dxflrs/garage`, 6 813 661 pulls, description matches the project's own tagline; **but** the code lives at `git.deuxfleurs.fr/Deuxfleurs/garage`, GitHub `deuxfleurs-org/garage` is the mirror, and the Docker namespace (`dxflrs`) matches neither by name |
| upstream still maintained | **no** — GitHub `minio/minio` reports `archived=True`, last release `RELEASE.2025-10-15T17-29-55Z`, last push `2026-04-24` | yes — releases `4.43`(08-21) `4.44`(08-22) `4.45`(08-31) `4.46`(09-08) `4.47`(09-14) `4.48`(09-28); pushed 2026-09-29 | yes — pushed 2026-09-28; **zero GitHub releases** (distribution is tags + their own site) |
| license | AGPL-3.0 | **Apache-2.0** (measured label + GitHub) | **AGPL-3.0** (GitHub) |

No candidate reproduces MinIO's signed-tag chain exactly; SeaweedFS replaces it with a *label-to-commit
to-binary* chain that this round actually verified end-to-end, and Garage offers the weakest anchor
(no labels, unverified signature, three-name namespace). None of this disqualifies a candidate under
the contract in §B — it changes what the lock's `provenance` block must say, which is §Q work for an
implementation candidate, not a claim to be written now.

## §G Vendor state re-probe (the standing rule, applied)

Probed at **2026-09-29T06:35:46Z**, anonymously, with an index-only `Accept` and a token minted for
the repository we name:

```
quay.io/minio/minio     RELEASE.2025-04-22T22-12-26Z -> 401 UNAUTHORIZED  {"errors":[{"code":"UNAUTHORIZED", …
quay.io/minio/mc        RELEASE.2025-04-22T22-12-26Z -> 401 UNAUTHORIZED
quay.io/minio/operator  RELEASE.2025-04-22T22-12-26Z -> 404 MANIFEST_UNKNOWN   ← control
hub.docker.com/v2/repositories/minio/minio/          -> empty object, namespace=None, no description
```

The 404-vs-401 contrast on the *same* org with the *same* non-existent tag is what keeps F-56
classified as "made private", not "deleted": a readable repository answers `MANIFEST_UNKNOWN` for an
unknown tag, a closed one answers `UNAUTHORIZED` before the tag is even considered. `minio/operator`
is still anonymously readable, so the org itself is not gone — `minio/minio` and `minio/mc`
individually are.

**Status: AVAILABILITY NOT RECOVERED.** The pinned coordinate has now been unreachable for 6 days
since the first symptom logged on 2026-09-13 (certification jobs) / 2026-09-24 (audit discovery). Per
the rule written into the design report, even if it flipped back to 200 this would be recorded as
`AVAILABILITY RECOVERED TEMPORARILY` and would **not** close F-56: what the outage demonstrated is
that an immutable, digest-pinned coordinate can become unauthorized with no change on our side.
Nothing about this round changes F-56's status or the `dea8c6f` certification history, which remains
valid evidence of what was certified at that commit.

## §H The harness that was built, and what each part is trusted for

All artifacts live under gitignored `_tmp/bakeoff/`; all were fetched through the box's proxy and
verified before use.

| artifact | size | verification | trust note |
|---|---|---|---|
| `seaweedfs/windows_amd64.zip` (release `4.48`) | 47 000 874 B | published `.md5` `01b750286b6b15411be5e4e76e7831e6` **matched**; zip sha256 `fe90c04c0620ad1a…`; `weed.exe` 153 073 152 B sha256 `394a0154424f3d96…` | **md5 only** — the vendor publishes an md5 sidecar for this asset, not a sha256. Weaker than the `mc` pattern the lock uses, and it comes from the same TLS channel as the asset. Recorded as a harness-trust limit, not as a dependency claim. |
| `mc.exe` = `mc.windows-amd64.RELEASE.2025-08-13T08-35-41Z.exe` | 31 468 032 B | published `.exe.sha256sum` `c8db13ebeda31497f354c0e950809db0ae9b2a2a69b8afee68c128c37300c157` **matched** | the *same pinned release* the certification workflows pin, its Windows build. `deployment/third-party-images.json:447` pins the Linux asset of this release. |
| PostgreSQL `16.10` EDB Windows binaries | 322 530 154 B, sha256 `ebb3b6af4fa69dea…` | size matched `Content-Length`; **no published checksum found** (`…zip.sha256` → S3 `AccessDenied`) | pure scaffolding so the suite's PG-backed tests could run at all. It is not a candidate and never enters the lock. Fidelity gap recorded: the pinned compose coordinate is `postgres:16-alpine`, this is EDB 16.10 for Windows. |
| local SeaweedFS server | `-volume.max` 8 → 64 → 128 (see §J) | `weed version` commit string cross-checked against the image label (§F) | the S3 gateway code is the same Go package as the image's; the *image* itself was never run here. |

Two honest limits that follow from this table and are **not** hidden behind the green result:

1. **the OCI image was never executed.** Everything in §I runs the vendor's Windows binary of the
   same release/commit. The S3 API surface is the same code; what is *not* covered is the image's
   entrypoint, `/data` layout, `EXPOSE 8333` (compose would have to stop assuming 9000), its user
   context, and its behaviour inside the shipped manifests.
2. **there is no MinIO side of the comparison.** MinIO could not be run (no runtime, and its
   coordinate is closed), so nothing in this report is a measured delta against the incumbent. The
   closest available substitute is that the very same tests, unchanged, are what MinIO passed in the
   certification rounds that succeeded before 2026-09-13.

The Postgres scaffolding also required a real schema: `createdb cap283` and
`alembic upgrade head` → **104 tables, `alembic_version = 20260812_0021`**. Without it the suite
reported 7 skips and 7 failures, which is how the first two runs of §I came to look the way they did.

## §I SeaweedFS 4.48 against the product's own contract — executed results

`pytest -m object_store` (19 tests), unmodified, against `127.0.0.1:9000` with the identity file
configured so that SigV4 credentials are genuinely enforced:

| run | conditions | result |
|---|---|---|
| A | PG absent (not yet installed), `-volume.max=8` (vendor default) | **5 passed, 7 failed, 7 skipped** — six orphan-GC tests raising `ConnectionRefusedError` against `127.0.0.1:55432`, the evidence-fencing test failing behind the same absence and then masking its own cause in teardown (§R.4), and seven skips from `postgres_available()` being False |
| B | PG 16.10 up, schema at head, `-volume.max=8` still | **9 passed, 10 failed, 0 skipped** — every failure traced to `No writable volumes and no free volumes left` (§J), 630 occurrences in the server log |
| C | PG up, server restarted with `-volume.max=64` | **19 passed, 0 failed, 0 skipped in 318.81 s** |

Run C covers: content-addressed immutable put, duplicate-content idempotency, exact-bytes get with
digest verification, **tamper rejection** (overwrite the key with different bytes ⇒ `get()` raises
`sha256 mismatch`), list + delete, orphan GC grace-period protection / delete-after-grace /
never-delete-a-live-reference / shared-digest retention / GC-vs-attach race / idempotent
restart-safe sweep, stale-worker evidence fencing, crash-after-blob and crash-during-cancel fault
injection, two-worker HA at the scale the test actually hardcodes (`n = 24` queued runs, two worker
daemon subprocesses, `kill -9` of one mid-run, survivor recovers) and the durability benchmark at its
defaults (`CAP284_BENCH_N=150` runs across `CAP284_BENCH_WORKERS=8` daemons, zero loss). The HA job's
nominal scale is its own finding — see §R.8.

After run C the volume server reported **49 volumes across 7 collections** (each collection exactly
7) and 28 live files — the evidence that the suite genuinely wrote to the candidate rather than
quietly skipping (§J explains why 7×N matters).

Non-suite measurements, same configuration:

| measurement | result |
|---|---|
| auth positive control (`capadmin`) | `health()` True, put/get OK |
| auth negative controls | wrong secret ⇒ `SignatureDoesNotMatch`; unknown key ⇒ `InvalidAccessKeyId` — the candidate enforces SigV4, so the green run was not an anonymous-store artifact |
| listing completeness across the 1 000-key page boundary | 1 200 objects put, `list_keys()` returned **1 200**, `sorted(keys) == sorted(digests)` **True**, 1 200 unique digests — the GC's full-scan assumption holds |
| 20 MiB ceiling (3 distinct payloads, `-volume.max=128`) | all accepted, ETag `…-4` (4-part multipart), read-back **byte-exact**, `sha256(content) == key` **True** (§B, §K) |
| throughput / latency (absolute, no baseline) | 20 MiB put 0.26–0.54 s (≈37–78 MiB/s), get 0.14–0.21 s (≈94–147 MiB/s); small puts 165–178 obj/s single-stream; latency p50 4.0–6.1 ms, p95 5.4–11.1 ms, max 24.7 ms over 60 puts |
| delete sweep | 1 200 deletes in 1.1–1.4 s |
| two concurrent writers | 80 objects in 0.3–0.4 s; listing saw **141** = 61 pre-existing + 80, no loss and no phantom |
| DR path (`scripts/certification/backup_cluster.sh`'s own commands) | `mc alias set` exit 0; `mc mirror --overwrite` exported **13/13** objects; bucket emptied through the product store (`list_keys()` → 0); `mc mb --ignore-existing` exit 0; `mc mirror --overwrite` back ⇒ **13 restored, identical key set True**; every object re-read through `S3EvidenceStore.get()`'s digest verification OK |
| metadata after restore | `stored_at` (from `Last-Modified`) present — the GC's age source survives the DR round trip; `x-amz-meta-*` keys: **none**, i.e. the zero-metadata constraint the store depends on (`store.py:249-256`) is not violated by the candidate |
| outage drill (`cap-ga-reliability`'s scale-to-0, expressed as stopping the server) | down ⇒ `health()` False and `put()` raises; restart ⇒ ready True, all 13 objects present, byte-exact read OK |

Classification against §B: every one of the fourteen contract rows is **PASS** for SeaweedFS 4.48 as
executed natively. No `FAIL_COMPATIBILITY` was found in the executed surface.

## §J The volume-budget finding — the one thing that actually broke, and what it means

`weed server` ships `-volume.max` as a string with default **`"8"`** (`weed server -h`: *"maximum
numbers of volumes… If set to zero, the limit will be auto configured as free disk space divided by
volume size"*), and the master's growth policy creates **7 volumes for each new collection**
(`volume_growth.go:153 create 7 volume`), a bucket being a collection.

With the shipped default the volume id space is exhausted by **one** bucket. Run B failed exactly
there:

```
failed to find writable volumes for collection:cap-gc284 … No writable volumes and no free volumes left
create 7 volume, created 0: Not enough data nodes found!
```

…while `/status` showed 8 volumes, **none** of them `ReadOnly`, and 100 GB of the drive free — so it
was not capacity, not read-only marking, and not an S3 protocol defect. Setting `-volume.max=64`
turned run B's 10 failures into run C's 19 passes with no other change.

Consequences that belong in any future implementation candidate (not applied here):

- CAP's certification and production use needs **≥ 7 distinct buckets** (production `cap-evidence`,
  the suite's `cap-evidence284`/`cap-gc284`/`cap-fi284`/`cap-fence284`/`cap-bench284`, plus the DR
  target), i.e. ≥ ~49 volumes observed. A compose/K8s replacement must set an explicit volume budget
  with headroom; the vendor default makes the second bucket unwritable.
- `-volume.max=0` is *not* the safe answer here: the master logs `master_server.go:161 Volume Size
  Limit is 30720 MB`, so auto = free disk ÷ 30 GiB ≈ 3 on a host with this much free — worse than the
  default. The number must be explicit.
- **The candidate reports telemetry outward by default.** A fresh start with no configuration options
  other than data dir and ports logs, verbatim:
  `collector.go:84 Reporting anonymous cluster statistics to
  https://telemetry.seaweedfs.com/api/collect every 24h0m0s once 10 GiB are stored, use
  -telemetry=false to opt out`. For a product whose certification suite includes network-egress
  isolation and an egress proxy (`test_phase_28_5_linux_network.py`, the `cap-egress-proxy` image,
  `EGRESS_PROXY_URL` in the Linux workflow), an unrequested outbound call from the evidence store is a
  deployment requirement to pin down, not a footnote: the implementation candidate must set
  `-telemetry=false` in compose and in both Kubernetes Deployments, and the isolation round must
  observe that it does not try. Whether the incumbent's pinned 2025-04 release behaves the same way was
  **not measured** — it could not be run here (§C).
- Disk is not the constraint: after 2 844 live files the data directory was 276 MB.
- The classification of this event is **FAIL_HARNESS_VENDOR_BINDING + a candidate deployment-sizing
  requirement**, *not* FAIL_COMPATIBILITY. Recording it as a candidate failure would have been the
  easy misreading; recording it as nothing would have hidden a real operational requirement.

## §K ETag semantics differ, and CAP does not depend on them

The candidate does not return `md5(content)` for objects it splits into chunks: 8 MiB → `…-2`,
20 MiB → `…-4`, while 1 MiB → a plain content MD5. The same value is also exposed in the vendor-specific
`Seaweed-X-Amz-ETag` header. ETags are stable per content and per size (3 identical trials returned
the identical ETag each time).

Checked against the product: `grep` for ETag use across `backend/app`, `backend/tests`, `scripts`
finds only `app/acquisition/httpadapter.py:68,176,238,319`, `models.py:187` and `models_db.py:132` —
an ETag captured from the **fetched web page** for conditional re-fetch, never an S3 object ETag. CAP's
integrity gate is the sha256 in the key, verified on read (`store.py:292-299`), which the candidate
satisfies for every object written in this round. So this is a documented behavioural difference for
operator tooling, not a product defect and not a disqualifier — but a runbook that says "verify by
ETag" would be wrong on this candidate and should be written against the sha256 key instead.

One earlier observation is corrected rather than smoothed over: a size sweep that ran while the
server was volume-starved reported a dashless ETag for a 20 MiB write, which contradicted the
`-4` seen in §B. Re-measured in a controlled state (`-volume.max=128`, three distinct 20 MiB
payloads) the `…-4` multipart form reproduced every time; the dashless reading is attributed to the
starved server, and the report uses the controlled numbers.

## §L Readiness and health: what the harness demands, and what is MinIO-specific

| surface | expected on MinIO (**not measured this round** — no runtime, coordinate closed: §C, §G) | measured on SeaweedFS 4.48 | consequence |
|---|---|---|---|
| `conftest.py:200 minio_available()` (TCP connect to 9000) | works | **works** (True) | vendor-neutral, no change needed — and this is the gate the suite's own skipif uses |
| `docker-compose.yml:112` healthcheck `curl -f http://localhost:9000/minio/health/live` | the reason the line exists | **403 AccessDenied** (the S3 gateway treats the path as a bucket/key and denies it) | MinIO-specific; must be replaced, not asserted against |
| `scripts/certification/setup.sh:74` same `/minio/health/live` curl | as above | 403 | MinIO-specific |
| `GET :9000/status` | n/a | **200** | a candidate-neutral alternative exists, but it is candidate-specific, so a replacement's healthcheck belongs to the implementation design, not to this bake-off |
| `docker-compose.yml:102,110` + `setup.sh:47,52` + `cap-ga-reliability.yml:171` + `cap-k8s-certification.yml:150`: `--console-address :9001` and port `9001` | MinIO console | no equivalent route | five MinIO-only sites; a replacement changes them all, which is precisely why the F-57-style contract guard must be data-driven rather than presence-based |
| `cap-linux-certification.yml:91,173,263` service container `quay.io/minio/minio@sha256:a1ea…`, `command: server /data --console-address :9001` | — | not executed | the workflow's service block is a fourth surface a migration must move atomically — three jobs, not one |

Per the stage rule, **no PASS condition in this round used `/minio/health/live`**.

## §M Garage: not measurable in this harness, for measured reasons

| attempted | result |
|---|---|
| run the vendor's binary natively | **impossible** — the vendor's download index (`garagehq.deuxfleurs.fr/download/`, 27 510 B fetched) mentions `x86_64`/`aarch64` and 24 occurrences of `linux`, and **zero** occurrences of `windows`, `darwin`, `exe` |
| GitHub release assets as a binary source | `deuxfleurs-org/garage` has **0 releases** via the API — only tags |
| run its OCI image | impossible here: no Linux runtime (§C); the image's own platform list is `linux/{386,amd64,arm,arm64}`, no `windows` entry (§E) |
| prove the coordinate is admissible | **done** — both `v2.3.0` and `v2.4.1` resolve anonymously with digest round-trip and exactly one amd64 child (§E), so a runner *with* a Linux runtime can pull and test them |

Classification: `NOT_MEASURABLE_IN_THIS_HARNESS` for every contract row in §B. This is *not* a
compatibility verdict about Garage, and it must not be read as one: an unscored candidate cannot be
selected, which is one of the three reasons this bake-off is inconclusive. Its registry-side and
provenance-side posture is recorded (§E, §F): admissible coordinates, but the weakest provenance
anchor of the three products examined — no image labels, unverified tag signature, and a Docker
namespace that matches neither hosting org by name.

## §N Scoring against the fixed weights

Weights and hard disqualifiers were fixed by the brief: contract compliance is weighted highest and
is a hard gate; provenance fidelity and supply-chain control are hard gates; ops/deployment parity,
performance and license posture are weighted but not gates. Applying them to what was measured:

| dimension | MinIO (incumbent) | SeaweedFS 4.48 | Garage (v2.4.1/v2.3.0) |
|---|---|---|---|
| hard gate — anonymously resolvable immutable coordinate | **FAILED** (§G) | PASS (§E) | PASS (§E) |
| hard gate — honours the §B contract | **historically PASS, unmeasurable now** (it passed these tests before 2026-09-13; no baseline run exists here) | **PASS on every executed row** (§I) | **NOT MEASURED** (§M) |
| hard gate — reproducible provenance to the lock's standard | PASS but unusable | PASS-with-a-different-anchor; signed-tag step unsatisfiable, label↔commit↔binary chain verified (§F) | weakest: no labels, unverified signature (§F) |
| weighted — deployment surfaces (compose, 2 K8s manifests, service container, setup script) | n/a | **NOT MEASURED** — no runtime (§C); plus a real sizing requirement discovered (§J) and 5+2 vendor-specific sites (§L) | NOT MEASURED |
| weighted — DR tool-path (`mc`) | n/a | PASS (13/13, byte-exact, §I) | NOT MEASURED |
| weighted — outage/restore behaviour | n/a | PASS (§I) | NOT MEASURED |
| weighted — performance | no baseline measured | absolute numbers only (§I) | NOT MEASURED |
| weighted — outbound egress posture | not measured (§C) | **phones home by default** — `telemetry.seaweedfs.com` every 24 h past 10 GiB unless `-telemetry=false` (§J) | NOT MEASURED |
| weighted — licence posture | AGPL-3.0 | **Apache-2.0** (relaxes, does not tighten) | AGPL-3.0 (unchanged) |
| weighted — upstream maintenance | archived, no further fixes | active, ~2-week cadence, pushed today | active, pushed 2026-09-28 |
| **disqualified?** | yes, on availability | no | **cannot be scored** |

The scoring therefore has one candidate that is *partially* scored, one that is unscored, and no
baseline. A fixed-weights comparison across three products cannot be completed from that, which is
the mechanical reason for the verdict in §Q. What can be stated from evidence: **nothing measured
disqualifies SeaweedFS 4.48**, and it is the leading Option D candidate by executed evidence.

## §O Still unverified (the explicit list)

1. The OCI images have never run here — not SeaweedFS's, not Garage's. Entrypoint, `/data` layout,
   `EXPOSE 8333` vs compose's assumed 9000, user context, volume defaults *inside the image* (`mini`
   mode is the image's `Cmd`, and it was not the mode executed here).
2. `docker-compose.yml` as a whole was not started; no `depends_on`/health/ports/volumes behaviour
   was observed.
3. Neither inline Kubernetes Deployment (`cap-k8s-certification.yml:150-178`,
   `cap-ga-reliability.yml:150-178`) was applied; no kind cluster exists here; PVC/storage-class
   semantics, rollout-restart behaviour and the scale-to-0 drill in K8s were not exercised (§I's
   outage drill was a process kill, not a `kubectl scale`).
4. No certification workflow round was dispatched, so the four publication authorities have no
   opinion about any candidate. Strict/final certification is what turns "the S3 gateway is
   compatible" into "this candidate is certifiable at a sha".
5. No vendor baseline, hence no relative performance or resource-footprint claim.
6. Garage's contract behaviour in its entirety.
7. The registry-quota dimension: Docker Hub answers anonymous token requests from this path with
   `x-ratelimit-limit: 3000, 3000;w=60`, `x-ratelimit-remaining: 2999`, but an *anonymous pull* quota
   could not be measured without a runtime, and a candidate hosted on Docker Hub is exposed to a
   rate-limit class of failure that the Quay-hosted MinIO coordinate never had. Recorded as a risk,
   not as a measurement.
8. Long-run durability: `weed` was observed over ~1 hour, not over the soak the GA tier-2 job
   performs.

## §P Stage 11 — evidence-preservation decision

The Phase-1 read-only recovery left three objects in the **gitignored** `_tmp/minio/`:
`q_list.json` (969 B, the manifest list that hashes to the pinned index digest), `q_amd.json`
(2 275 B, the linux/amd64 child), `cfg.json` (8 559 B, the image config blob). Decision, recorded
here rather than silently enacted:

- **Preserve them, tracked, but not as loose files.** They are the only surviving evidence for a
  coordinate that the vendor can delete at any time, and Option B (exact relocation) is blocked
  precisely on the claim that some of that graph is unrecoverable. The right home is a new
  `docs/quality/artifacts/f56-minio-graph-2026-09-25/` directory with a manifest naming, per file,
  the bytes, the `sha256`, the registry URL and the `Accept` header that produced it — the same
  self-describing shape `scripts/release/third_party_registry_evidence.py` demands of every other
  piece of evidence. A JSON blob committed without that manifest would be exactly the kind of
  unverifiable artefact the publication gate exists to refuse.
- **Not done in this round**, and not because it is hard: committing artifacts that no test yet
  covers is how evidence rot starts, and this stage's authorization is a bake-off. The action belongs
  to the implementation candidate that also lands the F-57 guard, and it is ~12 kB.
- If the project instead decides on Option B (mirror the exact bytes), this evidence becomes the
  seed of the mirror and *must* be tracked first, because the mirror's provenance claim is "these
  bytes, from the vendor, by digest".
- Everything this round produced (`_tmp/bakeoff/`) stays scratch on purpose: candidate binaries, a
  local database, and mirrors of neither. The report above carries the hashes of what was fetched, so
  the claims are checkable even after the tree is cleaned.

Two carried-over items from the F-56 chain are also confirmed here rather than left implied:
the missing graph pieces are the arm64 child `sha256:54d3d6a0a58f…` (2 275 B), the ppc64le child
`sha256:106abffd1b57…` (2 274 B) and ten linux/amd64 layer blobs, so **Option B remains not yet
feasible**; and the E2 generator still has `pinned_digest_still_resolves: None` for every surface
that pins the retired coordinate, which is the shape of the F-57 gap (presence checked, absence not).

## §Q What would resolve this, and what is being asked for

The gap between "inconclusive" and "candidate selected" is one authorization and four rounds:

1. **A Linux container runtime on a host that can reach the registries.** On this box that means
   repairing the WSL2 kernel (`wsl --update` — currently refused by system policy, so it may need
   Windows Update enabled, or a different machine). This is a system-level change and is *not*
   performed on this stage's authority; it is the single decision that unblocks items 2–4.
2. With a runtime: run the same 19-test contract against **each candidate's OCI image** (SeaweedFS
   `4.48`, Garage `v2.4.1`) via a throwaway compose file inside `_tmp/`, including the image's own
   `mini` mode, and add the missing baseline by pulling *some* anonymously-available MinIO release for
   comparison — while recording that it is **not** the pinned coordinate and does not retro-certify it.
3. Apply the two inline Kubernetes Deployments in a kind cluster, with the volume budget from §J set
   explicitly, and re-run the scale-to-0 outage drill as `kubectl scale`, not as a process kill.
4. Only then dispatch a **development-mode** certification round at the migration commit to classify
   it (measured earlier: a compose+lock+workflow migration is `deployment` →
   `RECERTIFICATION_REQUIRED`), and gate on `mode: development` — not strict, and with no tag.
5. On the approved-for-a-future-candidate list, unchanged: the **F-57** data-driven
   `retired_refs` guard (with a must-fail mutation and a must-not-fail control for each guard, per
   the measured mutation matrix), the **F-55** diagnostics fix (the blind `cap-infra` dump and the
   three timeouts), the **F-52** durable release-asset producer evidence, and now a new sibling
   (§R.3): a contract test that writes at the store's own 20 MiB ceiling so multipart support is
   actually certified for whichever product ends up in the lock.

No part of that is implemented, dispatched, or partially applied by this report.

## §R Measurement-integrity notes (what went wrong in the harness, and how it was caught)

Recorded because a green table with these stripped out would overstate how directly the result was
reached.

1. **Two runs of the suite were re-run before trusting either.** Run A's 7 failures and 7 skips were
   absent PostgreSQL, not an object-store problem; run B's 10 failures were the volume budget (§J).
   Both were diagnosed from the server's own `/status` (0 read-only volumes, 100 GB free) and the
   assign log before any conclusion was drawn, and run C is the only result quoted as a compatibility
   outcome.
2. **A "candidate rejected" reading was retracted before it reached a claim.** The 500 on a 20 MiB
   multipart part upload looked like an incompatibility; the log line beneath it was
   `failed to find writable volumes … no free volumes left` with 64/64 volume ids consumed. Re-run
   with a larger budget, it passed three times. §K's ETag anomaly is handled the same way: the
   contradictory reading is named, attributed, and superseded by the controlled measurement.
3. **A real gap in CAP's certification surface, found by measuring rather than reading** (§B, §Q.5):
   no `-m object_store` test writes anything above a few hundred bytes
   (`test_phase_28_4_object_store.py:52-117`, `test_phase_28_4_orphan_gc.py:107-132`), so the store's
   own 20 MiB ceiling — the point where the pinned SDK silently switches to multipart uploads — has
   never been certified against *any* object store, MinIO included. Proposed as **F-58**; **not filed**
   in `docs/known-issues.md` by this round, because filing findings is not in this stage's
   authorization.
4. **A harness defect that masks causes:** `test_phase_28_4_evidence_fencing.py:175` raises
   `UnboundLocalError: cannot access local variable 'run_id'` in its `finally:` block, replacing the
   real failure (the DB connect that happened first). It is the same family as F-55's blind failure
   dump — a certification test whose teardown destroys the evidence of why it failed. Not fixed here
   (test edits are out of scope); recorded.
5. **Auth was proved, not assumed.** The first server start had no identity file, and an anonymous
   store would have let "19 passed" mean nothing. The identity config was rebuilt to the format the
   4.48 binary parses, then both negative controls were measured (`SignatureDoesNotMatch`,
   `InvalidAccessKeyId`) before run A.
6. **Environment artifacts, not product results:** pytest could not write
   `backend/.pytest_cache` (`WinError 5`), so `-p no:cacheprovider` was used; `wsl.exe` writes UTF-16,
   so its output was decoded rather than read as text; and the console's GBK codepage turned one
   traceback into mojibake until output was forced to UTF-8.
7. **Tree hygiene.** `git status --porcelain` was empty before and is empty apart from this report and
   the `CHANGELOG.md` entry; every artifact fetched or generated lives under `_tmp/`, which
   `.gitignore:77` excludes. The sealed `v1.0.6-rc1` was not referenced, modified, or re-run.
8. **`CAP284_HA_N` is inert, and the HA scale quoted in three older reports was never executed**
   (proposed **F-59**; not filed, same reason as F-58). Measured at `7ebc85d`:
   `.github/workflows/cap-linux-certification.yml:130` sets `CAP284_HA_N=20`, `:192` and `:282` set
   `CAP284_HA_N=100`, and `scripts/certification/run_ha.sh:9` exports it — but
   `backend/tests/test_phase_28_4_multi_worker_ha.py` reads only the DSN/endpoint names (`:33-48`) and
   hardcodes `n = 24` (`:191`). `git log -S 'CAP284_HA_N' -- <that test>` returns **no commits**: the
   read has never existed in this repository, and `n = 24` plus the export were both introduced by the
   same commit `1ccab56`. So `backend/docs/phase_28_5_rc2_final_blocker_closure_report.md:170`
   ("**100-run OCI HA** … `CAP284_HA_N=100`"), `phase_28_5_ci_pipeline_report.md:110` and
   `phase_28_5L_full_regression_closure.md:256` describe a scale the code did not run, and
   `phase_28_5L_linux_certification_manual.md:322` names a variable (`CAP285_HA_N`) that does not
   appear anywhere else in the tree. The neighbouring benchmark *does* honour its knob
   (`test_phase_28_4_benchmark.py:44,182`), which is what makes the HA asymmetry visible rather than a
   general assumption about env vars. Relevance to F-56: the bake-off's HA row is therefore a 24-run
   measurement, and any claim that a replacement survived "the 100-run HA certification" would be
   repeating a prose claim the gate does not enforce.
9. **A second HA assertion is weaker than its name.** `test_no_duplicate_owner_per_epoch` truncates
   the queue and then asserts `claim_token_hash IS NOT NULL` count is zero on the empty table; its own
   docstring says "with an empty queue the invariant trivially holds; the real duplicate-owner proof
   is exercised by claim CAS tests". It passed here, and it proves nothing about a candidate. Counted
   as a pass in run C's 19 only because the suite counts it; the load-bearing HA evidence from this
   round is the `kill -9` survivor test.
