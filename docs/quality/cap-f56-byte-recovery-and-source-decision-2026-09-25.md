# CAP F-56 BYTE RECOVERY & SOURCE DECISION REPORT

Phase: **Q.1 — read-only byte/cache recovery hunt**, authorized 2026-09-25. Status at entry:
`F-56 REMEDIATION DESIGN READY — IMPLEMENTATION REQUIRES APPROVAL`.
Input coordinate: `quay.io/minio/minio@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e`.

The single question this phase answers: **can the exact original OCI graph rooted at that index digest
be recovered from an already-authorized cache or store?** Nothing else was decided, nothing was
implemented, and no other authorization was exercised: F-57, F-55 and F-52 were approved **for a future
implementation candidate only**, and nothing of them is implemented here.

Declarations required by the authorization: **no image was mirrored, nothing was pushed to any
registry, no release tag was created, no publication started, no certification was re-dispatched, no
credential was requested, enrolled, used or stored, no private source was probed with any credential,
no registry or package state was modified, and `dea8c6f`'s historical certification is treated as valid
history, not as publishability today.** Only this report was added to the repository; no other tracked
file was touched (§B.11).

---

## A. Expected historical graph

Taken from tracked evidence only — `deployment/third-party-images.json` and
`docs/quality/artifacts/registry-resolution/third-party-registries.json`, never from a re-query of the
now-closed source:

| field | recorded value |
| ----- | -------------- |
| root index digest | `sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e` |
| root media type | `application/vnd.docker.distribution.manifest.list.v2+json` (a Docker manifest **list**, not an OCI index) |
| `digest_kind` | `manifest-list (multi-arch index)` |
| child count | **3** |
| platform set | `linux/amd64`, `linux/arm64`, `linux/ppc64le` |
| linux/amd64 child | `sha256:3f97c5651cb6662b880c787a232b6b34fec8d8922e08d6617b25d241a21164bb`, size **2275** |
| historical image config | `image_config_created = 2025-04-22T22:35:01.339428192Z`, labels `release/version = RELEASE.2025-04-22T22-12-26Z` |
| attestation / `unknown` descriptors | none recorded |

One limitation of the historical record matters for this phase and is reported rather than papered
over: the tracked evidence records the amd64 child **only**. The arm64 and ppc64le child digests are
therefore not independently known from history; they can only come from the root index's own
descriptors. Any "recovered graph" must be checked against the root digest first, which pins all three.

Recovery criterion used (Stage 1, unmodified): bytes recovered means the complete graph — root bytes
hashing to the pinned digest, every descriptor it lists present, every child manifest hashing to its
descriptor, every transitively referenced config and layer present and hashing correctly, the platform
set equal to the recorded set, the amd64 child equal to the recorded child, and any attestation
descriptors preserved. A *similar* index is not an exact one; `docker image inspect`, a config digest
existing, one child present, or "a tar was produced" are explicitly **not** sufficiency.

## B. Locations searched

Read-only throughout, using only access this operator already has. Status per Stage 2.

| # | location | method | status |
| - | -------- | ------ | ------ |
| 1 | Local Docker daemon image store | `docker version`, `docker images` over the `dockerDesktopLinuxEngine` pipe | **NOT ACCESSIBLE** — daemon not running, pipe absent |
| 2 | Docker Desktop VM storage | recursive search for `*.vhdx` under `%LOCALAPPDATA%\Docker` and its `wsl\data` path; `%LOCALAPPDATA%\Docker` contains only `log/ run/ tasks/ *.lock` | **NO STORE EXISTS** — no disk file to inspect, so the daemon's absence is not merely a stopped state |
| 3 | WSL distributions (a common place for a containerd store) | `wsl.exe --list --verbose` | **NOT ACCESSIBLE** — no distribution installed |
| 4 | containerd / podman / buildx stores | `C:\ProgramData\containerd`, `%USERPROFILE%\.local\share\containers`, `.docker\buildx` (1.0 KB, no `cache`), `.docker\desktop-build` (lock file only) | **NO MATCH** |
| 5 | Session scratch actually on this machine | `cap/_tmp/minio/`, `cap/outputs/producer-observation/*`, `http-layout/index.json`, all `*.tar` and files >20 MB under `outputs/` and `_tmp/`, name-search for `a1ea29fa*` / `3f97c565*` | **PARTIAL** — three graph objects recovered (§C); the five `.oci.tar` files are 10 240 B each and belong to **our own** five release images, whose `index.json` carries a single amd64 child of `cap-sandbox-http` |
| 6 | Other volumes on this box | `E:\` (depth ≤4, names containing `minio`, `oci*.tar`, `image*.tar`), `D:\` (`*.vhdx`), `F:\` outside the workspace (`*.vhdx`, `*minio*.tar`, `*docker*.tar`) | **NO MATCH** |
| 7 | Retired-file area of this workspace | `_待删_回收区` name search | **NO MATCH** — only the Python `minio` **SDK** (`site-packages/minio`), which is a client library, not image bytes |
| 8 | Organization-controlled registry this account can already read | `/user/packages?package_type=container` and anonymous `tags/list` for the plausible mirror paths | **NO MATCH** — 5 visible container packages, exactly `cap-backend`, `cap-frontend`, `cap-egress-proxy`, `cap-sandbox-http`, `cap-sandbox-browser`; `laolaola278-dev/cap-object-store` and `…/minio` resolve to 401 (no such public repository) |
| 9 | Retained CI artifacts of the certified and recent rounds | artifact listings for runs `35971523354` (final-strict GA), `35967302293` (release-layer Linux), `35960848879` (soak), `35958562591` (K8s) and `36004166112` | **NO MATCH, by bound** — 12 artifacts, largest `ga-cert-artifacts` at **1 110 921 B**; the complete graph needs ten compressed layer blobs, so nothing here could contain it. Downloading them was not necessary to reach this conclusion, and the bound is stated so a reader can check it |
| 10 | Public mirrors of the vendor coordinate | results carried from the 2026-09-24/25 measurement pass, not re-polled: Docker Hub `library/minio`, `mirror.gcr.io`, `public.ecr.aws/upstream-mirror/…`, `public.ecr.aws/minio/minio`, `ghcr.io/minio/minio` | **NOT ACCESSIBLE / NO MATCH** — 401 for the two namespaces that exist, 404 for the mirrors, 403 for the ghcr namespace |
| 11 | Deliberately **not** searched | any private MinIO source with stored or guessed credentials; new or incidental credentials; SSH enrollment; third-party community mirrors as a byte source; runner caches that no longer exist; **other people's machines** | out of authorization — the last is the real limit of this phase (§K) |

## C. What was actually found

`cap/_tmp/minio/` — gitignored scratch from **2026-09-19T02:28–02:34Z**, the day the vendor
coordinate was investigated and *while it was still publicly readable* — holds three genuine graph
objects, and content addressing proves each one is byte-exact rather than a re-serialization:

| object | file | size | sha256 of the file's bytes | equals |
| ------ | ---- | ---- | --------------------------- | ------ |
| root manifest list | `_tmp/minio/q_list.json` | 969 B | `sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e` | **the pinned root** |
| linux/amd64 child manifest | `_tmp/minio/q_amd.json` | 2 275 B | `sha256:3f97c5651cb6662b880c787a232b6b34fec8d8922e08d6617b25d241a21164bb` | its descriptor, and the recorded historical child digest **and** size |
| amd64 image config blob | `_tmp/minio/cfg.json` | 8 559 B | `sha256:9d668e47f1fc60ea49af4203deee87a657eb1aa0e2761fee2c7c2d1df282c880` | the config descriptor inside that child manifest |

A re-serialized or reformatted JSON cannot hash to a registry digest, so these three are the served
bytes, not notes about them. The verifier that checked them is `_tmp/f56_graph_verify.py` (scratch,
not committed, per Stage 5), and it reads the expected values out of the tracked evidence file rather
than from anything typed in, so §H is a comparison and not an echo.

This is also a fragility finding worth stating plainly: **the only recovered objects in the world I
could reach live in a gitignored scratch directory**. Clearing `_tmp` deletes them.

## D. Graph completeness

Computed from the recovered root's own descriptors, not estimated:

| required element | total | present | missing |
| ---------------- | ----- | ------- | ------- |
| root index | 1 | 1 | 0 |
| child manifests | 3 | 1 | **2** — arm64 `sha256:54d3d6a0a58fb25b4e9943d1db3828d3b4de44666f911381b4fda57175488194` (2 275 B), ppc64le `sha256:106abffd1b575a2465443f8907c041e019fbed6475425bc40fffdf19a106962d` (2 274 B) |
| configs | 3 (1 known, 2 unreachable until their manifests exist) | 1 | ≥2, and the two unknowns are not even enumerable yet |
| layers | amd64: 10, enumerated below; other platforms: unknown | **0** | all 10 |

The ten amd64 layer blobs, in manifest order, none of which exists in any location searched:

```
d949f716ce773b6952762453dba423b055fd2ae51e80707cbbb44b80d49a508f
8c577d69454d55b6e5bffdebaaa4026898f417005e86dba1bca027935774c21f
546fbfcace6536ab2e96351ffe76b9431dfbaa742a5fe7a76e6920f1d8383b71
d0c8865a030236212964e817f56a5554cdebb8ecb04909756282ce5e2f2b41c4
cf67644788eb8e2f7556c241a8d1d5ff89079d85bfb2fb4555fd5fc794a857c2
df0d0d229f33f0af067b4345d8d577a83038467dfe6bd4832630cc292e8f66f9
62481e699c81d80a3df48f9a08a8bea10918ac8981ff00b5d09a8098156ac75e
68a66c893f7ce4098eac3f50758c8e2b6aa8750b979ca90345609eb3af0a95b9
d7ab9c0b242a9f5411ee1b71ab98134a0e7505cd0a632b0dcf446ad43e97c570
39c8ce9a80d2ca7f544f57a5a00617176a86e0760bc1aa46cf17d3f34bf3807d
```

`transitive_closure_complete = false`; the criteria satisfied are 1, 2, 4 (for the one child present),
6 (for the config), 7, 8 and 9; the criteria failed are **3** (two descriptors unresolvable), **5**
(no layer available) and 6-for-layers. The verdict of the verifier is
**`PARTIAL_GRAPH_ONLY`**.

## E. Digest verification

| check | result |
| ----- | ------ |
| `root_expected == root_computed` | **true**, and equal to the pinned digest in `docker-compose.yml`, the four workflows and `setup.sh` |
| root media type equals history | true |
| amd64 child digest recomputed from its bytes | matches its descriptor, matches the recorded historical child, and the file size equals the listed size (2 275) |
| config digest recomputed | matches the descriptor inside the child manifest |
| descriptor size mismatches | none |
| layers | not attempted — no layer bytes exist anywhere searched, so there is nothing to hash |

## F. Platform and attestation preservation

The recovered root lists exactly `linux/amd64`, `linux/arm64`, `linux/ppc64le` — equal to the
historical platform set, so the multi-arch property that lets a non-amd64 host resolve its own build
is *described* by bytes we hold. Zero attestation or `unknown`/`unknown` descriptors exist in this
index, so nothing was silently dropped — but two of the three platform children are absent, which
means the preserved graph covers **one** platform's *manifest and config* and **no** platform's data.
No replacement index was constructed, and none should be: a synthesized index would be a new digest
and would silently redefine what "the same bytes" means.

## G. Source provenance — and why it is a separate claim from identity

| aspect | finding |
| ------ | ------- |
| source location | `cap/_tmp/minio/` on this machine |
| owner / control boundary | this workspace's gitignored scratch, written by this project's own earlier session |
| read method | anonymous HTTPS `GET` against the vendor coordinate's registry endpoints, recorded 2026-09-19 — the same handshake `scripts/release/third_party_registry_evidence.py` performs |
| original ref | `quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z`, still public on that date (`tag_currently_serves_pin: true` in the tracked artifact) |
| timestamps | `q_amd.json` 2026-09-19T02:28:07Z · `q_list.json` 02:28:49Z · `cfg.json` 02:34:06Z |
| metadata tying bytes to the vendor | the config's own labels: `vendor = MinIO Inc <dev@min.io>`, `maintainer = MinIO Inc <dev@min.io>`, `release = version = RELEASE.2025-04-22T22-12-26Z`, `vcs-ref = 519a52b1bc2ef31e2633c20f34a0df840e159172`, `com.redhat.component = ubi9-micro-container`, `created = 2025-04-22T22:35:01.339428192Z`, 10 `rootfs.diff_ids` |
| blob count | 3 objects held, ≥14 required and absent |

**Digest equality proves content identity. It does not by itself prove vendor origin.** Here origin is
supported separately, and weakly-to-moderately so: the objects' labels name MinIO Inc and the exact
release tag, their mtimes sit inside the 2026-09-19 session that recorded the same values into the
tracked evidence, and `sha256(bytes)` of the root equals the digest the repository has pinned since
then. What cannot be shown from this evidence alone is the vendor's **signing chain** for these bytes
(the lock's own `open_gap` already admits `vcs-ref 519a52b1…` resolves in neither `minio/minio` nor
`minio/docker-minio`, so the signature-to-bytes link was never closed). Any Option B claim must carry
that admission forward rather than launder it.

## H. Comparison with the historical evidence

| value | tracked evidence | recovered bytes | verdict |
| ----- | ---------------- | --------------- | ------- |
| root index digest | `sha256:a1ea29fa…` | identical | match, and history is **not** being replaced by recovery — recovery confirms history |
| root media type | docker manifest list v2 | same | match |
| child count | 3 | 3 descriptors | match |
| platform set | amd64, arm64, ppc64le | same | match |
| linux/amd64 child | `sha256:3f97c565…`, 2 275 B | identical digest **and** size | match |
| `served_digest_verified_against_body` / `refetch_by_digest_byte_identical` | true / true (2026-09-19) | consistent | no contradiction |

Two historical-record observations come out of this comparison and are reported rather than fixed here.
First, `pinned_digest_still_resolves` is `null` in the tracked artifact: the field exists but the
outage has not been re-measured into it, so the artifact currently neither asserts nor denies
resolvability. Second, the tracked evidence and `docs/known-issues.md` both say certification exercises
**versioning**; grepping the shipped code and tests finds **no** versioning, `VersionId` or
`list_object_versions` use anywhere — the S3 surface actually used is `make_bucket(location="us-east-1")`,
`put_object`, `get_object`, `list_objects(prefix, recursive=True)` and `remove_object`, plus
`mc alias set` and `mc mirror` in the DR gates. That overstatement matters to Option D scoring, and
correcting it is a documentation change that belongs in the implementation candidate, not in this phase.

## I. Option B technical feasibility

**Not feasible as exact relocation.** Option B as designed means re-hosting *the same index digest* in a
namespace CAP controls. What is in hand is 3 of at least 17 graph objects: the root, one of three child
manifests, and that child's config. Ten layer blobs and two child manifests are absent from every
location reachable here, and the two absent children make their own configs and layers unreachable.
Per Stage 4's rule, the most likely place such bytes survive — a `docker` image store on a machine that
once ran the compose stack — would in any case likely be **PARTIAL, platform child only**: a single-node
Docker store keeps what it pulled for its own platform, so even a successful find there needs verifying
against the criteria in §A rather than being declared a recovery because `docker image inspect`
succeeded. No push was performed and no index was synthesized.

What the partial recovery is still good for: it is a **verification baseline**. Any candidate copy found
later — on a colleague's machine, in a CI cache, in a third-party registry — can be checked in seconds
against a root digest we already hold byte-exactly, instead of trusting a name.

## J. Option D shortlist (needed, because exact recovery failed)

At most two candidates, judged on the ten Stage 8 criteria; community MinIO forks are deliberately not
shortlisted, because the brief's preference (independently maintained over unofficial lineage) is not
outweighed by any compatibility evidence — `docker.io/pgsty/minio` imitates the `RELEASE.…` tag
cadence while its `vendor` label is the inherited `Red Hat, Inc.` base label, which is a lineage claim
rather than provenance. `quay.io/minio/aistor/minio` is excluded outright: licence key required,
redistribution limited.

**1. Garage — `docker.io/dxflrs/garage`, docs-named tag `v2.3.0` (primary).**
Anonymous availability ✔ (index `sha256:866bd13ed2038ba7e7190e840482bc27234c4afaf77be8cfa439ae088c1e4690`,
`sha256(body)==served`, refetch byte-identical, `linux/amd64` child `sha256:dac0c92add4f1a0b41035e94b41036a270ffbe88a37c7ac9c3f19e6dc5bdccf2`,
platforms 386/amd64/arm/arm64). Immutable digest pinning ✔. Maintainer authority: its own documented
domain (`garagehq.deuxfleurs.fr`) names the repository and says prefer a fixed tag ✔; but the image
carries **no config labels at all**, so provenance rests on the docs and the tag, not on the artifact —
weaker than what the MinIO pin had. Licence AGPL-3.0, same family as today ✔. API compatibility against
§H's real surface: multipart ✔, presigned ✔ (unused), **versioning ❌ missing — and CAP does not use
versioning**, so this is now a documented non-issue rather than a blocker; `mc mirror` behaviour needs a
real test. Operational complexity: single binary, designed for self-hosters ✔. Multi-arch ✔.

**2. SeaweedFS — `docker.io/chrislusf/seaweedfs`, `latest` (secondary, pending a compatibility read).**
Anonymous availability ✔ with digest round-trip (`index sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882`,
`linux/amd64 child sha256:f83509b0721dfd8e2e07faf76c0a899f67a8a889c89abe2fa0a5227ba1320362`, 4 platforms).
Licence stated **in the image's own labels** (`org.opencontainers.image.licenses=Apache-2.0`,
`vendor=Chris Lu`, `version=4.47`, built 2026-09-14) — the strongest provenance-per-artifact of any
candidate here, better than Garage's unlabelled image and far better than the fork's. Permissive licence
removes §H's trademark question entirely. Multi-arch ✔, operational complexity moderate (a volume
server plus a filer behind the S3 gateway). **Open:** its S3 API wiki page could not be retrieved this
session (fetch failed), so versioning/multipart/presigned posture is **unverified here** and must be
established from its documentation before it is preferred over Garage.

Deciding evidence, not more reading: run §N's V-8..V-12 against each in a real kind cluster and on
compose, including `mc mirror` for the DR gates and whatever `/minio/health/live` must be replaced with
(both candidates are MinIO-incompatible on that endpoint, so the compose healthcheck and the
certification readiness waits are migration work, not configuration colour).

## K. Legal / registry-write decisions still pending, and the limits of this phase

1. **LEGAL/TRADEMARK REVIEW: STILL REQUIRED.** Unchanged by this phase: the recovered objects'
   *identity* says nothing about whether CAP may re-host the vendor's image. The vendor's trademark
   policy covers unchanged official binaries and is silent on container images; that question is
   unanswered and blocking for Option B.
2. **REGISTRY WRITE: NOT AUTHORIZED** and not performed. Nothing was pushed anywhere.
3. **The hunt is bounded by access, not by effort.** Every location reachable from this machine was
   searched and none holds layer data; a container store that does exist here was shown not to exist
   at all (§B.2–3). The plausible remaining places are **other people's machines and caches**, which
   this phase cannot and must not reach for. If the project wants a second attempt, the ask is
   specific and cheap: run `_tmp/f56_graph_verify.py` against any machine that ever pulled the
   coordinate, and the criterion is already written.
4. **A preservation decision needs making**: the three recovered objects are the only recovered bytes
   in reach, and they sit in gitignored scratch. Committing them under
   `docs/quality/artifacts/registry-resolution/` would make the baseline durable — that is a
   repository change and therefore outside this phase's authorization.
5. **Two corrections belong in the implementation candidate**, not here: the versioning overstatement in
   `deployment/third-party-images.json`'s `open_gap` and `docs/known-issues.md` (§H), and the
   `pinned_digest_still_resolves` field that has never been filled (§H).

## L. Sealed-release integrity

Re-audited after all work in this phase: **`SEALED RELEASE INTACT`** on all ten checks —
`v1.0.6-rc1` unchanged, no tags created in the window, the Release never edited, all five assets at
their original timestamps with `download_count` 0, the values asset's bytes matching the `sha256` GitHub
records for it, and all five digests it names matching what `1.0.6-rc1` resolves to on ghcr today.
`v1.0.5` remains the newest non-prerelease, non-draft release. `dea8c6f` remains the only fully
certified commit, untouched, and is certified — not publishable today, which is exactly the distinction
F-56 is about.

## M. FINAL DECISION

**F-56 SOURCE DECISION: OPTION B EXACT RELOCATION NOT YET FEASIBLE**

Three graph objects (root index, `linux/amd64` child manifest, its config blob) are recovered and
proven byte-exact by digest equality against the pinned root; **two child manifests and all ten
enumerated amd64 layer blobs are absent from every authorized location searched**, and the two absent
children hide their own configs and layers. `EXACT_GRAPH_RECOVERY = NO`. No index was reconstructed,
no image was pushed, no credential was used.

Recommendation: **move the decision to Option D** (§J) as the executable path, while keeping two cheap
doors open — a targeted recovery attempt wherever a real container store may still exist, and the
standing re-probe of the vendor coordinate, since the repository is *private rather than deleted*, so
an ordinary vendor act (reopening public reads, or granting one pull) would restore Option B outright.
Per §A.5's rule, neither door closes F-56: the recorded risk is that an immutable coordinate changed
from public to unauthorized with no change on our side, and that is now demonstrated twice in one week.
