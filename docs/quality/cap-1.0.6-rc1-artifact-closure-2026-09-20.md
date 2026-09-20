# CAP 1.0.6-rc1 — Artifact Completeness & Reproducible Build Closure Report

Closure tip: `b447436` (branch `release/1.0.6-rc1`). Runtime certification anchor from the previous
round: `c52dcb9` — and this round **cannot inherit it** (§7), because closing F-7 and F-20 changed
build inputs. F-7 and F-20 landed in `3eadfc4`/`d6a7f77`; the closure then needed four code
follow-ups (`0b143ac`, `3cc6579`, `c69d960`, `b447436`), each one produced by this round's own CI or
re-certification run rather than by reading the code — see §5, §9 and §10. The commits between them
carry this report and the register updates.
Prepared: 2026-09-20, from a Windows audit host plus GitHub-hosted Linux runners.
Publication: **none performed** — no `v*` tag, no GitHub Release, no image pushed, no existing tag
or image overwritten.

> Scope of this document: F-7 (the release published two of the five images the production chart
> deploys) and F-20 (Dockerfile base images named by mutable tag). F-4, F-5, F-6, F-9 and F-19 are
> untouched by design and stay as recorded in
> `docs/quality/cap-1.0.6-rc1-release-certification-2026-09-19.md` §23.

---

## 1. The image graph, derived rather than asserted

Nothing below was typed in by hand: `backend/tests/test_release_image_completeness.py` walks
`deployment/helm/cap/templates/*.yaml`, resolves each `cap.imageRef` argument against
`values.yaml`, and additionally walks `values.yaml` for image-shaped coordinates that never appear
in a pod template — which is where the two sandbox images hid.

| CAP image | Source Dockerfile | Build context | Helm value path | Reference before | Reference now | Published by release.yml before |
| --- | --- | --- | --- | --- | --- | --- |
| `cap-backend` | `backend/Dockerfile` | `backend/` | `backend.image` | `ghcr.io/<owner>/cap-backend:1.0.6-rc1` | unchanged + optional `digest` | **yes** |
| `cap-backend` (worker) | `backend/Dockerfile` | `backend/` | `worker.image` | same | unchanged + optional `digest` | **yes** (same artifact) |
| `cap-frontend` | `frontend/Dockerfile` | `frontend/` | `frontend.image` | `ghcr.io/<owner>/cap-frontend:1.0.6-rc1` | unchanged + optional `digest` | **yes** |
| `cap-sandbox-http` | `backend/docker/sandbox-http/Dockerfile` | staged: `oci_protocol.py`, `oci_shim.py` | `worker.sandbox.image` | `cap-sandbox-http:latest` | `ghcr.io/example/cap-sandbox-http:1.0.6-rc1` | **no** |
| `cap-sandbox-browser` | `backend/docker/sandbox-browser/Dockerfile` | staged: same two files | `worker.sandbox.browserImage` | `cap-sandbox-browser:latest` | `ghcr.io/example/cap-sandbox-browser:1.0.6-rc1` | **no** |
| `cap-egress-proxy` | `backend/docker/egress-proxy/Dockerfile` | staged: `egress_proxy.py` | `egressProxy.image` *(new; the template hardcoded the image)* | `cap-egress-proxy:latest`, hard-coded in `templates/egress-proxy.yaml` | `ghcr.io/example/cap-egress-proxy:1.0.6-rc1` | **no** |

Set difference at the start of this round, measured by the same derivation (not narrated):

```
the production chart deploys CAP images that no release job publishes, so a fresh install
pulls refs that do not exist: ['cap-egress-proxy', 'cap-sandbox-browser', 'cap-sandbox-http']
release publishes: cap-backend, cap-frontend
```

Machine-readable form: `docs/quality/artifacts/cap-1.0.6-rc1-artifact-closure/image-graph.json` —
the derivation snapshot taken at the **start** of this round, so its `chart_references` still show the
`:latest` values the table above records as "before". Re-deriving it on today's tree is the gate
itself, and needs no trust in this sentence:

```
uv run --project backend pytest backend/tests/test_release_image_completeness.py \
  -k "same_image_set or reference_is_latest or references_are_all_templated" -q
```

which passes on the current tree -- all eleven chart coordinates resolve to `ghcr.io/…:1.0.6-rc1`,
including `egress-proxy.yaml:egressProxy`, templated where the snapshot still says `:literal`, and
five CAP images against a published set of five. The failing first run of the gate is preserved beside
the snapshot in `gate1-image-set-diff-before-fix.txt`. Both were moved out of `outputs/` into the
repository because a report that cites a gitignored path proves nothing to anyone without the machine
that wrote it (§10, F-37).

The chart's own `:latest` offenders were three, and one of them was not even retargetable: the
egress-proxy deployment spelled its image inside the template, so an operator setting
`egressProxy.image` would have had nothing to set.

## 2. Release workflow image matrix

`release.yml`'s publication jobs now sit behind two gates added in the previous round
(`quality-gates`, `verify-certification`) and one added here:

| Job | Builds | Push | Attestations | Needs |
| --- | --- | --- | --- | --- |
| `release-images` | `cap-backend`, `cap-frontend`, `cap-sandbox-http`, `cap-egress-proxy` (matrix, `include` entries with explicit `dockerfile` + `context`/`role`) | yes | SBOM + provenance, per image | `validate-tag`, `verify-certification` |
| `release-sandbox-browser` | `cap-sandbox-browser` | yes | SBOM + provenance | `release-images` (takes the HTTP sandbox image's **index digest** as `SANDBOX_HTTP_BASE`) |
| `release-image-security` | — | — | Trivy HIGH/CRITICAL, unfixed ignored, exit 1, **plus a per-image scan record** derived from the Trivy JSON report | each of the five published refs |
| `release-image-completeness` | — | — | validates the five evidence records, renders `values-release-<version>.yaml` | all of the above |
| `release-chart` | chart package + the rendered values file | — | — | `release-image-completeness` |
| `publish-release` | GitHub Release | — | — | `release-image-completeness`, `release-chart` |

Each build passes `VERSION=<validated version>` and `REVISION=<tag target SHA>`, tags
`ghcr.io/<owner>/<image>:<version>`, and writes one evidence record with the index digest, the
linux/amd64 child digest, the Dockerfile SHA, the hash of the staged build context and the source
revision. `release-image-completeness` fails the release if any of those is missing, if a record is
tagged something other than the validated version, if an image was not pushed, if an attestation is
absent, if the published image was never scanned, or if evidence arrives for an image the release
does not declare.

The scan is evidence, not a green tick: `release-image-security` asks Trivy for a JSON report and
derives a record from it (`ref`, policy, `blocking_findings`, `verdict`), so a clean scan of some
other artifact cannot satisfy the gate and a step that produced no report fails rather than
recording a pass. The completeness gate merges that record into the release evidence, which is what
makes the §25 shape — tag, index digest, platform digest, SBOM, provenance, **trivy** — answerable
from one file after publication. All of it is executed in
`test_release_image_completeness.py`: `test_the_scan_record_is_derived_from_the_trivy_report`,
`test_the_scan_record_refuses_a_report_with_findings`,
`test_the_scan_record_cannot_be_written_without_a_report`, and three gate refusals
(`…_refuses_an_unscanned_published_image`, `…_refuses_a_scan_of_some_other_artifact`,
`…_refuses_a_scan_that_found_something`).

**No partial release:** `publish-release` does not depend on `release-images` at all — it depends on
the gate that requires all five. If `cap-sandbox-browser` fails while the other four publish, the
GitHub Release is not created; the tag stays (deleting and re-pushing a tag is exactly the
immutable-release violation this line exists to prevent) and the run reports publication
incomplete, to be retried forward from a new commit.

## 3. Third-party bases, digests and provenance

Every external `FROM` is now `name:tag@sha256:…`. The digests were read from the registries over
HTTPS — request the tag with an `Accept` header for the OCI index and Docker manifest-list media
types, take `Docker-Content-Digest` (or the SHA-256 of the returned manifest bytes, which is what
ghcr requires because it omits the header), then **re-fetch by digest and require byte-identical
manifest bytes**. Output:
`docs/quality/artifacts/cap-1.0.6-rc1-artifact-closure/registry-base-digests.json`, which carries per
base the registry, the index digest, the media type, the `linux/amd64` child, and the two flags that
say the re-fetch happened (`byte_identical_by_digest`, `digest_matches_recomputed`). It is tracked for
the same reason as §1's snapshot: `deployment/third-party-images.json` points at it, and a pointer into
a gitignored directory resolves only on the machine that ran the measurement (F-37).

| Base | Index digest (manifest list) | Media type | linux/amd64 child | Used by |
| --- | --- | --- | --- | --- |
| `python:3.13-slim` | `sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0` | `application/vnd.oci.image.index.v1+json` | `sha256:37134a49d21d2120e4c4d73bb76f8a4ab9aef31f096f7ec2ead48c2feead4332` | `backend/Dockerfile` (builder + runtime) |
| `python:3.13.12-slim-bookworm` | `sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6` | `application/vnd.oci.image.index.v1+json` | `sha256:3121f8b0804aa3698ab750d9a39ea4a42657a385c9b133722b915e55c51551a6` | `backend/docker/sandbox-http/Dockerfile`, `backend/docker/egress-proxy/Dockerfile` |
| `node:22-alpine` | `sha256:b6f26b36c8ff49624cfdac716b8ea1138d606df02586a77d364bb5536a634f85` | `application/vnd.oci.image.index.v1+json` | `sha256:b64da1de5a51067ab8e75f0bc8dbd0905d8894baa22261f439a4572f41291e50` | `frontend/Dockerfile` (builder) |
| `nginx:1.30.4-alpine` | `sha256:dc5069ad14f19660b141b21236140b91656bf89bbc3e2417c70ae650cd66104c` | `application/vnd.oci.image.index.v1+json` | `sha256:862dc06c359bfe5d3211e4106269f040d261e269e58ebf17060d8328c45067c0` | `frontend/Dockerfile` (runtime) |
| `ghcr.io/astral-sh/uv:0.8.3` | `sha256:ef11ed817e6a5385c02cd49fdcc99c23d02426088252a8eace6b6e6a2a511f36` | `application/vnd.oci.image.index.v1+json` | `sha256:88baae1f9fa298996f8313e44559163c535937406d217f1c8ac9d4b86a2020fd` | `backend/Dockerfile` (uv binary) |

**Why the index digest and not the platform digest** (§10): every one of these is a multi-arch
manifest list — `python:3.13-slim` carries linux/386, amd64, arm, arm64, ppc64le, riscv64 and
s390x; `astral-sh/uv:0.8.3` carries amd64 and arm64 plus attestation entries. Pinning the index
keeps a build for any of those platforms reproducible; pinning the amd64 child would make an
arm64 build either fail or silently re-resolve. The amd64 child is recorded *as well*, because
that is the platform the certification runners build and test, and an auditor comparing two
certified builds needs it. `test_dockerfile_base_images.py` requires `linux/amd64` to be present in
each record's platform set, so a single-arch base cannot be locked without saying so.

## 4. The browser image's internal base

`backend/docker/sandbox-browser/Dockerfile` used to open with `FROM cap-sandbox-http:latest`. On a
clean runner that build fails; on any machine that had built the HTTP image it succeeded against
unrecorded bytes — the worst kind of pass, because it looks identical either way. It is now:

```dockerfile
ARG SANDBOX_HTTP_BASE
FROM ${SANDBOX_HTTP_BASE}
```

with **no default**, so an omitted base is a build error rather than an accident. Three callers
name it:

- `backend/docker/build_sandbox_images.sh` builds `cap-sandbox-http:<tag>` first and passes it
  (local development and the certification jobs; the tag comes from `CAP_SANDBOX_IMAGE_TAG`);
- `release.yml`'s `release-sandbox-browser` job passes
  `ghcr.io/<owner>/cap-sandbox-http@sha256:…` — the digest it published minutes earlier in the same
  run, so the shipped browser image states exactly which bytes it extends (ARTIFACT-GATE 9);
- CI's `release-image-builds` matrix builds the prerequisite from the same commit first, which is
  what makes that path testable at all (ARTIFACT-GATE 5).

The staged context both sandbox images need (copies of `app/sandbox/oci_protocol.py` and
`oci_shim.py`) moved into `backend/docker/prepare_sandbox_context.sh`, called by both the
certification script and the release script, so a release image cannot be assembled from a
different file list than the certified one — and `context_sha256` in each evidence record is what
makes a difference visible instead of a matter of faith. That last clause is true only after F-34:
until that fix the hash covered the absolute path of the `mktemp -d` staging directory, so the same
staged content produced a different value in every cell and the field described the machine rather
than the input.

Naming the base is only half of it; the release has to be able to *say* which base it used. The
build script reads `FROM` lines out of the Dockerfile, and a `FROM ${VAR}` line resolved only
against the Dockerfile's own `ARG` default — which for this image does not exist. The evidence
therefore recorded `base_refs: []` for the browser image while printing `UNRESOLVED` to stderr and
carrying on. Now the caller's `--build-arg` values are part of the resolution, so the record carries
the exact `ghcr.io/…/cap-sandbox-http@sha256:…` the build consumed, and a base that resolves to
nothing **fails the build** instead of recording an empty list. `base_refs` joined the publication
gate's required fields for the same reason: an image that cannot name what it was built on cannot be
re-built or audited, which is precisely the question F-7 left unanswerable. Both halves are executed
by `test_a_named_base_is_recorded_verbatim` and `test_an_unresolvable_base_refuses_the_build`.

## 5. CI builds all five, without pushing

`ci.yml` gained `release-image-builds`: a matrix over the five images, each built through
`scripts/release/build_release_image.sh` with no `--push`, then scanned by Trivy with the same
policy the release job applies (HIGH/CRITICAL, unfixed ignored, exit 1). It replaces the job that
built and scanned two images, which is why "CI was green" never once meant "all five release images
build". The per-image evidence JSON is uploaded, so ARTIFACT-GATE 5 has an artifact behind it and
not just a green tick.

The driver differs from the release on purpose. A buildx build runs inside a container builder that
cannot see the host docker store, so `FROM cap-sandbox-http:1.0.6-rc1` — an image that exists
nowhere but that runner — would resolve against `docker.io/library`, which is exactly what it did:

    pull access denied for cap-sandbox-http, repository does not exist or may require 'docker login'

`--local-docker` therefore builds with `docker build`, which does share the store — and CI passes it
in **every** cell, not just the browser's: what the matrix proves is that each image's own Dockerfile
builds on a clean runner and lands where the scan step can find it. The one buildx dry build per CI
run is the browser cell's `cap-sandbox-http` prerequisite, which needs no store access and so takes
the release's own builder. Three drivers, three claims, and the evidence records which one produced which
record — `build_driver` alongside `attestations` read back out of the flags the builder was handed,
so a docker-driver dry build can never be mistaken for an attested release artifact:
`docker build` for CI's five cells, `buildx --load` for that prerequisite, and
`buildx --push` for the release itself — which is the only one of the three that has never executed
(F-25).

That run then failed a second time for a different reason, and this one is worth stating plainly:

    scripts/release/build_release_image.sh: line 127: PLATFORM_DIGEST: unbound variable

the image having built and loaded successfully. `PLATFORM_DIGEST` was assigned only by the
`--local-docker` branch, so under `set -u` the evidence block aborted on **every** buildx build —
including all five of `release.yml`'s. The release path had the same defect and could not show it,
because publishing is unauthorized: F-25's "never executed" half was not merely unproven, it was
broken, and the string-level assertions over the script all passed alongside it. Fixed at the cause
(the defaults are now unconditional, and `platform`/`platform_digest_linux_amd64` are separate
fields rather than one variable swapped behind the reader's back).

`backend/tests/test_release_build_script.py` runs the script itself against a stubbed `docker`, so
this class of failure no longer costs a clean-runner round trip to discover: the buildx dry build,
the push build and the local-driver build each have to emit evidence carrying the fields the
publication gate requires, and removing the default this fix added makes the suite fail the way CI
did.

F-36's repair belongs to the same family, because it reads image metadata *after* a successful build —
the exact spot where F-26 aborted. What says it is safe is not the reading of the diff but CI run
35506705907's prerequisite record: a buildx dry build on a clean runner, green, carrying
`platform: linux/amd64`.

## 6. Helm contract and the fresh-install audit

- `templates/_helpers.tpl` gained `cap.imageRef`, which composes `{repository, tag, digest}` and
  prefers the digest; an empty tag falls back to `Chart.AppVersion`, so the chart carries one
  version literal (already a version carrier) instead of six that can disagree.
- `values.yaml` declares six image coordinates for the five CAP images (`backend.image` and
  `worker.image` name the same artifact); `values.schema.json` requires a non-empty `repository` on
  all six, and on four of them an `anyOf` accepting a tag **or** a digest — while `backend.image` and
  `worker.image` additionally require `tag` outright, so a digest-only pin is legal for a sandbox and
  refused for the API and the worker (F-41). `test_the_released_values_satisfy_the_chart_schema`
  evaluates every coordinate the renderer writes against the node the schema declares for it, and
  declares which keywords it understands so a new construct fails the test rather than going
  unevaluated.
- `release-image-completeness` renders `values-release-<version>.yaml` from the **published
  digests** for every image coordinate the chart declares — six of them, because `worker.image` is a
  second deployment of the backend artifact — and `publish-release` attaches it to the GitHub Release
  beside the chart archive, so `helm upgrade -f values-release-<version>.yaml` installs the digests
  that were certified. Two defects lived in this line when it was first written and are F-30 and
  F-32: the file was uploaded but never attached, and it pinned five coordinates while the chart reads
  six, which left the acquisitions-running worker deployment on the chart's placeholder registry.
- K8S-GATE 34 (`test_gate34_deployed_image_set_is_the_released_set`) reads the images of every pod
  in the `cap` and `cap-sandbox` namespaces plus the worker's own sandbox coordinates, asserts no
  `:latest`, asserts the set equals the five released names at the tag the job built, and treats
  `ImagePullBackOff` / `ErrImagePull` as the failure it is (ARTIFACT-GATE 10). It also replaced the
  certification suite's own hard-coded `cap-sandbox-http:latest` probe image with the value read
  back from the live deployment — a test with its own image name cannot notice a chart/cluster
  disagreement, which is precisely how F-7 survived.

## 7. Diff classification and the recertification decision

`python scripts/release/classify_diff.py c52dcb9 b447436` → **RECERTIFICATION_REQUIRED**
(`docs/quality/artifacts/cap-1.0.6-rc1-artifact-closure/diff-c52dcb9-to-b447436.json`): 49 files, of which **16 are
runtime-affecting** —

| Category | Files | Runtime-affecting | What is in it |
| --- | --- | --- | --- |
| `deployment` | 14 | **14** | the five Dockerfiles, five chart templates plus `_helpers.tpl`, `values.yaml`, `values.schema.json`, `third-party-images.json` |
| `production_runtime` | 2 | **2** | `backend/docker/build_sandbox_images.sh`, `backend/docker/prepare_sandbox_context.sh` — the staged context a sandbox image is built from |
| `test_harness` | 14 | 0 | the completeness, build-script, base-image, doc-contract, publication-gate and certification test modules |
| `ci_workflow` | 6 | 0 | `ci.yml`, `release.yml` and the four certification workflows |
| `docs` | 9 | 0 | this report, the previous round's, the registers, chart and deployment docs |
| `certification_generator` | 3 | 0 | `scripts/release/build_release_image.sh`, the two report generators |
| `repo_tooling` | 1 | 0 | `scripts/quality/audit_junit.py` |

**Byte-identity was the alternative, and it is not available** (§18): to inherit, the base that the
certified build resolved at `c52dcb9` would have to be provably the same digest this round pins. The
round that certified it recorded `base_digest: null` for exactly that reason — that missing field
*was* F-20 — and a tag a registry resolved the previous morning cannot be reconstructed afterwards:
the registry answers "what is `python:3.13-slim` now?", and no log records what it was then. Claiming
equality here would have been the cheapest possible way to skip a re-certification, so it is not
claimed: the Dockerfile digest change is treated as runtime-affecting, `c52dcb9` is **not** inherited,
and a new certified SHA is established by re-running CI, the Linux release layer with its PostgreSQL
matrix, the Kubernetes suite (now 34 gates), the 7200 s reliability soak and the strict FULL GA round
along the closure line — the soak and its paired GA round at one commit, the rest of the rounds at
that commit or a later inheritable one, and CI at the delivered tip (§9 lists which run sits on which
SHA, and the classifier check at the end of §9 is what makes the ancestor's rounds the tip's evidence).
§20's rule was respected in the making: no "metadata-only" exception was added to
the classifier for pinned Dockerfiles, because a base image decides which OpenSSL ships in the
artifact.

## 8. ARTIFACT-GATE matrix

| # | Gate | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Helm CAP image set == release workflow image set | **PASS** (statically enforced) | `test_chart_and_release_publish_the_same_image_set`; `docs/quality/artifacts/cap-1.0.6-rc1-artifact-closure/image-graph.json` (start-of-round snapshot, §1); the before-fix refusal quoted in §1 and kept in `gate1-image-set-diff-before-fix.txt` |
| 2 | No production CAP image uses `:latest` | **PASS** | `test_no_production_chart_reference_is_latest`, `test_no_dockerfile_layers_on_a_cap_image_by_mutable_name`, K8S-GATE 34 at runtime |
| 3 | All external Dockerfile FROM refs digest-pinned | **PASS** | `test_every_external_dockerfile_base_is_locked_and_digest_pinned` + §3 table |
| 4 | Third-party lock covers every external base | **PASS** | `test_lock_base_entries_are_actually_used_by_a_dockerfile`, `test_locked_base_digests_carry_their_provenance`; §3's five rows compared field by field against the tracked `registry-base-digests.json` — 5/5 index digests, media types and amd64 children match, with `byte_identical_by_digest` and `digest_matches_recomputed` true for all five |
| 5 | Clean runner builds all required images | **PASS** | CI run 35508682453 on `d30b4e7`: five `release-image-builds` cells green inside a successful run on `ubuntu-latest`, push=false, one evidence artifact per image, and the six records read back below. The same was true at `a79d29c` (run 35502404774) and `b671f53` (run 35506705907); only the last two certify this line, because F-38 blocks inheritance below `b671f53`. It took two red runs and one aborted-script bug to get here, all recorded in §5 and §10 |
| 6 | Trivy covers all release images | **PASS** | CI `release-image-builds` scans each matrix cell it builds (derived, cannot drift); `release-image-security` lists its five names by hand, so `test_every_published_image_is_scanned_in_the_release` now requires that list to equal both the published set and the chart-derived set, with a control that dropping a name breaks it. The two workflow scans use one policy (HIGH+CRITICAL, unfixed ignored, `exit-code: 1`); GA-GATE 22's `security_policy.json` blocks only fixable CRITICALs. That difference is deliberate and not a hole: for a release image the stricter workflow policy runs too, and the policy file governs the GA verdict, not publication |
| 7 | SBOM enabled for all release images | **PASS** | `build_release_image.sh` adds `--sbom=true` only to a push build and records `attestations.sbom` from the flags it actually passed (`test_the_push_build_attests_and_records_the_platform_child`, `test_attestations_are_reported_from_the_build_that_ran`); `release-image-completeness` then refuses a record whose flag is false — executed by `test_completeness_gate_refuses_a_record_without_a_digest_or_attestation`, which sets `sbom: False` and expects the refusal |
| 8 | Provenance enabled for all release images | **PASS** | as 7, with `REVISION` bound to the tag's target SHA and asserted from the same evidence (`--build-arg` + `REVISION` in `test_every_release_image_is_built_with_version_revision_and_attestations`) |
| 9 | Browser image has an immutable internal base relationship | **PASS** | no-default `ARG SANDBOX_HTTP_BASE`; release passes `cap-sandbox-http@<index digest>`; `test_an_unresolvable_base_refuses_the_build` makes an unnamed base a build failure, and the browser record in §9 shows the field populated |
| 10 | Fresh kind deployment has no missing image | **PASS** | Run 35489588677 on `b447436` was the first to reach 34/34 with K8S-GATE 34 included, and run 35500709149 on `4aff814` the first whose artifact named what it observed; neither certifies the delivered tip, because F-38's fix at `b671f53` blocks inheritance from both. The round that does is 35508689787 on `d30b4e7`: `gates: {total: 34, passed: 34, failed: 0, not_run: 0}`, `source: K8S-GATE 34`, five images at the tag the job built, `worker_sandbox_coordinates` covering both sandboxes, `pull_errors: []` — and it is the same run that proves F-40, since the five gates the stale tunnel killed at `b671f53` are green here |
| 11 | Publication graph blocks a partial image release | **PASS** (statically enforced) | The gate's body is executed by `test_completeness_gate_refuses_a_missing_image` and its siblings; that the publication jobs must wait for it is a separate claim, and it is now executed too — `test_the_completeness_gate_is_in_front_of_everything_that_announces` walks `needs` transitively out of the parsed YAML (`release-chart` and `publish-release` both reach `release-image-completeness`, which reaches the two image jobs, the scan job, `validate-tag` and `verify-certification`), with `test_the_gate_graph_check_is_sensitive` cutting the edge to show the walk notices. A gate nobody is required to pass is a report |
| 12 | Classifier / recertification decision truthful | **PASS** | §7: RECERTIFICATION_REQUIRED, no byte-identity claim, no classifier exception added |

## 9. Certification runs on the closure line

`c52dcb9`'s evidence is **not** inherited (§7), so each round below has to be green at a SHA that
reaches the closure tip through inheritable-only commits — which is what the classifier check at the
end of this section is for. Every row cites the run, and where a round's meaning depends on what it
published, the row quotes its own artifact rather than the tick.

**CI** (`ci.yml`, cancels in flight):

| Attempt | SHA | Conclusion |
| --- | --- | --- |
| [35484377542](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35484377542) | `0b143ac` | **failure** — F-26: the build script aborted after a successful build |
| [35486431316](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35486431316) | `faaa4d1` | **failure** — `test_license_and_release_workflows_are_complete`, `assert 1 == 2`: an assertion left counting the two-image CI job this closure had already replaced. Now derived set equality with its own sensitivity test |
| [35485625550](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35485625550) | `3cc6579` | cancelled by a later push; every `release-image-builds` cell had already succeeded (ARTIFACT-GATE 5/6) |
| [35490251245](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35490251245) | `86e105d` | **success** |
| [35492342346](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35492342346) | `fdee042` | **success** |
| [35494453516](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35494453516) | `536827b` | **success** |
| [35496688015](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35496688015) | `9de9f0f` | **success** |
| [35498634857](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35498634857) | `3c523f4` | **success** |
| [35500219964](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35500219964) | `4aff814` | **success** — and reading its evidence artifacts back is what produced F-34 |
| [35502404774](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35502404774) | `a79d29c` | **success** — five `release-image-builds` cells green; its artifacts produced F-35 and F-36 and carried the F-34 verification (below) |
| [35505431617](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35505431617) | `8797a64` | **failure** — F-38: the `frontend` job's toast assertion raced antd's async mount on a commit that changed no frontend file. The other nine jobs, all five `release-image-builds` cells included, were green |
| [35506705907](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35506705907) | `b671f53` | **success** — all ten jobs, the console suite included; the six evidence records it uploaded are read below |
| [35508682453](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35508682453) | `d30b4e7` | **success** — with the F-40 harness repair and GATE 11's new graph walk in the suite, so the tests that certify the release contract are themselves certified |

**Linux certification** (`cap-linux-certification.yml`, `layer: release`; GA-GATE 33's evidence):

| Attempt | SHA | Conclusion |
| --- | --- | --- |
| [35485710392](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35485710392) | `3cc6579` | **success** |
| [35492389112](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35492389112) | `fdee042` | **success** |
| [35496753285](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35496753285) | `9de9f0f` | **success** |
| [35498707800](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35498707800) | `3c523f4` | **success** |
| [35500248467](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35500248467) | `4aff814` | **success** |
| [35500710588](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35500710588) | `4aff814` | **success** — re-dispatched to read the artifact back, which is how F-31's fix was confirmed rather than assumed |
| [35506716466](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35506716466) | `b671f53` | **success** — the round GA-GATE 33 resolves at the certified SHA |

**Kubernetes certification** (`cap-k8s-certification.yml`, 34 gates incl. the new K8S-GATE 34):

| Attempt | SHA | Conclusion |
| --- | --- | --- |
| [35485711757](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35485711757) | `3cc6579` | **failure** — F-27 |
| [35488262094](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35488262094) | `c69d960` | **failure** — F-28, though GATE 34 itself passed |
| [35489588677](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35489588677) | `b447436` | **success** — 34/34, ARTIFACT-GATE 10's first real round |
| [35492391209](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35492391209) | `fdee042` | **success** |
| [35496754931](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35496754931) | `9de9f0f` | **success**, `gates: 34/34` — and its artifact said `"source": "not_observed"`, i.e. F-31: a green round that could not name what it had deployed |
| [35498709533](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35498709533) | `3c523f4` | **success** — `"source": "K8S-GATE 34"`, five images, `worker_sandbox_coordinates` both sandboxes, `pull_errors: []` |
| [35500246776](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35500246776) | `4aff814` | **success** |
| [35500709149](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35500709149) | `4aff814` | **success**, 34/34, same observed set — history now: F-38's fix blocks inheritance from this SHA |
| [35506717889](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35506717889) | `b671f53` | **failure** — F-40. Gates 26/28/29/32 and pregate E died on `Server disconnected` / `ConnectError` while K8S-GATE 34, which reads the cluster through `kubectl`, passed with the expected image set: a dead port-forward, not a dead deployment |
| [35508689787](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35508689787) | `d30b4e7` | **success** — `gates: 34/34` with the observed image set in its artifact and `pull_errors: []`. The five gates F-40's stale tunnel killed at `b671f53` are green here, which is the repair proven on a cluster rather than in a unit test; this is the round ARTIFACT-GATE 10 rests on |

**Reliability soak** (`cap-ga-reliability.yml`, 7200 s — the only proof GA-GATE 24/25/26/34/35 get):

| Attempt | SHA | Conclusion |
| --- | --- | --- |
| [35485713552](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35485713552) | `3cc6579` | **success** |
| [35496756245](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35496756245) | `9de9f0f` | **success** — 07:23:52Z → 09:31:26Z (2 h 07 m) |
| [35498711108](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35498711108) | `3c523f4` | **cancelled by hand** a third of the way in, because a fix landed; a soak of a superseded commit produces no evidence, and `cancel-in-progress: false` means it had to be cancelled deliberately rather than by the push |
| [35500245071](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35500245071) | `4aff814` | **cancelled by hand at 2 h 23 m** (08:41:26Z → 11:04:42Z). It could no longer certify anything: F-38's fix landed at `b671f53`, which sits under `frontend/src/` and so blocks inheritance from `4aff814`, and the ref's soak group serialises — finishing that round would have delayed the one that counts by another 20 minutes. The two green soaks above are history; the tip's evidence is the one below it |
| [35506714369](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35506714369) | `b671f53` | **success** — 11:02:21Z → 13:12:05Z. Its own `soak-context.json`: `soak_seconds: 7200`, 480/480 healthy ticks, `availability: 1.0`, `downtime_seconds: 0`, `http_errors: 0`, 480 runs created / 48 cancelled / 1440 pagination requests, `non_terminal_runs: []` (the orphan gate's zero), worker pods killed on a 600 s cadence, and under-load transitions measured rather than asserted — upgrade 8.3 s and rollback 0.8 s, both with `http_errors_during: 0`. The reliability junit carries exactly the five cases GA consumes (24, 25, 26, 34, 35) with 0 failures and 0 skips. Its preview artifact says `mode: development`, `passed: 5, not_run: 35` — F-33's distinction, visible in the round's own output: a soak is not a GA verdict |

**FULL GA, strict** (`cap-ga-certification.yml` with `ga_strict=true`, so `CAP_GA_STRICT=1`,
`PLANNED == failure`, 40/40 required):

| Attempt | SHA | Conclusion |
| --- | --- | --- |
| [35485715411](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35485715411) | `3cc6579` | **failure** — F-27 |
| [35488263281](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35488263281) | `c69d960` | **failure** — `{total: 40, passed: 33, failed: 2, planned: 5}` in its own artifact. `GA-GATE 22` is F-29: 57 tests green, the Trivy target list in `security_policy.json` naming `cap-sandbox-http:latest`, so the gate died on a policy file rather than on an image. `GA-GATE 33` failed alongside it for a second, unrelated reason — no Linux release-layer run existed at `c69d960` for it to find |
| [35492392358](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35492392358) | `fdee042` | **failure**, and the artifact says why in its own fields: `mode: final-strict`, `full_ga_certified: false`, `gate_summary: {total: 40, passed: 35, planned: 5}` with `GA-GATE 24/25/26/34/35` the five. It was dispatched without a soak at its SHA, so the reliability evidence those five gates consume did not exist (`reliability soak run for fdee042…: <none>`), and the strict meta-gate went red instead of certifying on a partial record. That is the gate working — and it is why the soak and the GA round are ordered and matched below |
| pending | `b671f53` | to be dispatched from `cert/ga-strict-b671f53`, a ref pinned at that commit: `workflow_dispatch` always runs a ref's *tip*, the GA job resolves its soak by exact `head_sha`, and this line keeps moving forward while the soak runs |

One field in that artifact needs reading carefully: `baseline` is a *historical anchor* — Phase 28.6
as it stood when 28.7 was written, `{"run": "32565459369", "gates": "32/32 PASS", "commit":
"b905393…"}`, written as literals and asserted as literals by GA-GATE 1. It is not this round's
Kubernetes result, which has 34 gates, its own run id and its own artifact (§9's Kubernetes table);
reading `32/32` as the current 28.6 evidence would be the same mistake as F-21's unowned prose. What
the round did observe about images is derived separately into `cap-cert-ga/images.json`, stamped with
the commit it ran on.

A gate that errors is reported as a failure and re-run rather than read as close enough: the second
and third Kubernetes attempts and all three GA attempts are in these tables because they failed.
Seven CI runs were superseded mid-flight by the next push (`c69d960`, `fb5fc17`, `0ce3b93`, `706996f`,
`b447436`, `0c22e0e`, `d0ddf48`) and are left out for that reason; the certification workflows those
pushes triggered came back `skipped` by design — a `release/**` push runs no certification layer, which
is why every round in these tables is a dispatch — and `cancel-in-progress: false` there means no
certification round was ever lost to a push.

**What the tip's own artifacts said.** From CI run 35506705907 at `b671f53`, the six per-image records
read out of the uploaded files: every one carries `tag: 1.0.6-rc1`, `platform: linux/amd64`,
`pushed: false` and `attestations: {sbom: false, provenance: false}` — a dry build has no attestation
subject and the release gate refuses exactly that, so GATE 7/8 rest on the gate's executed refusal
plus the script's flags, not on CI's colour. Both halves of F-34's claim are visible in them at once:
`cap-sandbox-http`'s staged context hashes to `1da56fbc05b4…` in all three sandbox records, from two
different builders, and to the same value it had at `a79d29c` two commits back, because those two files
did not change; `cap-backend`'s context moved from `4775ddd6…` to `d620e4a3…` between the same two
commits, because two files under `backend/` did. Stable when nothing changed, different when something
did — that is the property the field exists to have, and it is now observed on clean-runner bytes rather
than asserted by a test. And because the browser cell's buildx prerequisite is finally named for what
it is (`cap-sandbox-http.prerequisite.json`, F-35), the two `cap-sandbox-http` records can sit in one
directory without lying about it: same inputs, one built by `docker` and one by `buildx`, which is why
their `config_digest`s differ at the same commit. Read that pair across the two commits instead — same
builder, same Dockerfile hash, same context hash, same base — and the digest still moves. That is
F-39.

**Inheritance.** `4aff814` would have been the certified SHA — `classify_diff.py 4aff814 <tip>` said
`INHERITED (release_metadata_only=True)`, and CI, Linux and Kubernetes were green there. F-38 took it
away: that fix lands in `frontend/src/hooks/`, which the classifier charges as `production_runtime`, so
nothing at or after `b671f53` can inherit a `4aff814` round. Two SHAs carry the delivered line now:
`b671f53` (CI, the Linux release layer, the 7200 s soak, and the strict FULL GA round dispatched from a
ref pinned at it) and `d30b4e7` — `b671f53` plus F-40's harness repair and GATE 11's graph walk — which
carries CI and the Kubernetes round. Both hops were measured rather than assumed:
`classify_diff.py b671f53 d30b4e7` and `classify_diff.py d30b4e7 <tip>` both return
`runtime certification INHERITED (release_metadata_only=True)`, the files between them classified
`docs` and `test_harness` and nothing else. The classifier was not modified for
this purpose, and no round in these tables is claimed green before its run finished.

## 10. Remaining findings after this closure

- F-4 migration/schema naming drift, F-5 `agents.runtime_image`, F-6 `SECRET_NOT_FOUND` → 404,
  F-9 MinIO vendor risk, F-19 single-connection lease edges: unchanged by design (§30 of the task),
  recorded with reasons in the previous report.
- F-7 and F-20 are **closed** by this round.
- New and open: **F-24** — compose-path third-party services (`redis:7-alpine`,
  `prom/prometheus:v2.55.1`, `grafana/grafana:11.3.1`, `dpage/pgadmin4:8`, `postgres:16-alpine`)
  are still pulled by mutable tag. They are not Dockerfile bases, so GATE 3/4 do not reach them,
  and the chart does not deploy them; a compose deployment on a single node is the affected surface.
- New and open: **F-25** — `release.yml`'s new image graph has never executed, because the workflow
  triggers only on a `v*` tag and publication is not authorized. What CI's dry build *did* reach is
  now known to have been broken rather than merely unproven (F-26 below), which is the argument for
  treating this as a finding instead of a footnote. What remains verified only by reading: `--push`
  itself, registry authentication against `ghcr.io`, the SBOM/provenance attachments the registry
  stores, and the index-digest hand-off between `release-images` and `release-sandbox-browser`. The
  first release run is where that half gets its proof, and it fails closed.
- **F-26** (found and closed inside this round) — `build_release_image.sh` aborted with
  `PLATFORM_DIGEST: unbound variable` after a successful buildx build, on every non-local build
  including all five of the release path's. The variable belonged to `--local-docker` alone; the
  evidence block read it unconditionally under `set -u`. Neither the workflow YAML checks nor the
  script-level string assertions could see it, so the round's own clean-runner build is what surfaced
  it, and `test_release_build_script.py` now executes the script against a stubbed `docker` to keep it
  local. It never reached a published artifact: the script was introduced by this closure, and the
  bug is fixed in the same round at `3cc6579`.
- **F-27** (found and closed inside this round) — re-certifying the closure tip failed the Kubernetes
  and GA rounds for two reasons, both in code this round had just written. The new
  `_deployment_env` reader passed the `CompletedProcess` that `_kubectl` returns into `json.loads`,
  so gates 6, 7, 10 and the brand-new gate 34 raised `TypeError` before asserting anything — a gate
  that cannot run reports nothing, which is the one outcome worse than a failure. And the DR fixture
  plus the supply-chain gates still named `cap-sandbox-http:latest`,
  `cap-sandbox-browser:latest`, `cap-egress-proxy:latest` while the jobs had moved to building them
  as `ci`: `kind load` refused (`ERROR: image "cap-sandbox-http:latest" not present locally`),
  GA-GATE 20/22/23 died on `No such image`, and the cluster-B install silently fell back to the
  chart's release defaults for images that cluster had never been given — after which everything
  downstream (rollout status, port 18080, the runbook gates) was cascade. Both sides now read one
  `CAP_CERT_IMAGE_TAG`, gate 34's reader has a cluster-free test of its own, and
  `test_certification_rounds_name_their_images_with_one_tag` refuses a literal CAP image tag in any
  certification module whose workflow builds a different one. Run against the pre-fix text it fails,
  so the guard is not decorative.
- **F-28** (found and closed inside this round) — the second Kubernetes attempt got gate 34 green and
  then failed GATE 25/26/28/29/32. GATE 25 force-deletes every backend pod and waits with
  `_ensure_api`, which returns the moment `/health` answers — and a pod that is already terminating
  answers. The wait ended on the endpoint the gate had itself deleted, the assertion fired about 0.3
  seconds later, and every later gate that needed the API hit `httpx.ConnectError` while the
  replacements were still starting. GATE 26's complaint was its own tell:
  `backend logs are not structured`, quoting
  `Error from server (BadRequest): container "backend" in pod "cap-cap-backend-…" is waiting to
  start: ContainerCreating` — the log helpers read `pods[0]`, and during a restart `pods[0]` is
  usually a pod that cannot serve. Fixed by waiting for a backend pod set that shares no name with
  the deleted one before accepting health, and by a ready-pod selector (`Running` with every
  container ready) in the log helpers. Both halves have cluster-free tests, because the difference
  only becomes visible during a restart — the worst possible moment to be discovering how the probe
  works. The third attempt (run 35489588677) is green with all 34 gates -- which, as F-40 turned out,
  meant the remaining half of the race had not been hit yet rather than that it had been closed.
- **F-29** (found and closed inside this round) — the strict GA round at `c69d960` ran 57 tests green
  and still refused to certify, over one line of JSON. `scripts/certification/security_policy.json`
  listed GA-GATE 22's Trivy targets with their own tags, three of them `:latest` — the fourth place
  that tag list was duplicated, and the one the F-27 guard did not look at. `trivy image` could not
  inspect a local image that did not exist, then tried `index.docker.io/library/cap-sandbox-http`,
  got `UNAUTHORIZED`, and gate 22 failed; the artifact step then reported
  `GA certification FAILED gates: ['GA-GATE 22']`, `total: 40, passed: 33`, and the verdict came from
  the policy file rather than from any image. The policy now names images only, the round composes
  the tag, and `test_certification_rounds_name_their_images_with_one_tag` compares the policy's
  targets with the chart-derived set (with `test_the_policy_scan_target_check_is_sensitive` proving
  the comparison can fail).
  The same shape sat one file away: the Kubernetes artifact's `images` block was a literal table in
  `generate_report_28_6.py` that still claimed `cap-sandbox-http:latest` after F-7 changed what the
  jobs build — a passing round's own evidence describing a deployment that had not happened, in the
  same family as F-21's unowned prose. K8S-GATE 34 now writes what it observed and the generator
  copies it, writing `not_observed` when the record is missing instead of recalling a table; running
  the generator as a process for those cases (`test_k8s_report_image_set.py`) also turned up that it
  died outright when `kind` was not on PATH, which is now a recorded field rather than a crash.
- **F-30** (found and closed inside this round) — the closure report says the digest-pinned
  `values-release-<version>.yaml` ships beside the chart, and `release-chart` did upload it into the
  release-assets artifact… but `publish-release`'s `gh release create` listed only the chart
  archive, the changelog, the notes and the known-issues file. The one file that turns a released
  chart into an installable pin was reachable only as a workflow artifact that expires. Uploading is
  not publishing: `test_every_uploaded_release_asset_is_attached_to_the_release` now compares every
  path `release-chart` uploads against the files the publish step attaches, and it fails on the
  pre-fix workflow (`MISSING: ['dist/values-release-*.yaml']`) — checked, not asserted.
- **F-31** (found and closed inside this round) — the first Kubernetes round whose gate passed for
  the new observed-image-set still reported `not_observed` in its own artifact. The cause is three
  lines of the same workflow: `CAP_CERT_OUT: outputs/cap-cert` is relative, the pytest step runs with
  `working-directory: backend` and therefore writes its junit out explicitly
  (`--junitxml=../outputs/cap-cert/junit-k8s.xml`), and the upload reads `path: outputs/cap-cert/`
  from the repository root. Gate 34 resolved the relative variable against *its* working directory,
  so the record landed in `backend/outputs/cap-cert/` — beside the tests, out of reach of the
  generator and of the
  upload. The safe fallback did its job (it declined to invent coordinates); the fix resolves the path
  against the repository root and stamps the record with its commit, and the generator refuses a
  record from another commit rather than searching candidate directories — this repository has already
  been certified once by a stale junit it found in its own `outputs/`.
- **F-34** (found and closed inside this round) — `context_sha256`, described in §4 as the field that
  makes a staging difference "visible instead of a matter of faith", was noise. CI run 35500219964
  recorded `cap-sandbox-http`'s context as `8310b248…` in one matrix cell and `febcb187…` in another:
  same commit, same four staged files. `find` on the absolute `mktemp -d` directory puts absolute
  paths into `sha256sum`'s output, and that text is what got hashed. Two builds that had to agree did
  not — so a build that genuinely differed in content could not be told apart from one that merely
  got a different temporary directory. The hash is now computed from inside the context; two tests
  hold both halves, because stability alone is not the claim (§4's sentence was written before
  anything ever compared the field against anything — which is why no number of green runs could
  have caught it).
- **F-35** (found and closed inside this round) — reading this round's own evidence back out of CI
  run 35502404774 took longer than writing it, and the reason was a naming collision. Every
  `release-image-builds` cell uploads the *whole* evidence directory, and the browser cell builds
  `cap-sandbox-http` a second time as its base, so two files arrived named `cap-sandbox-http.json`
  from two different builds: the http cell's own `docker`-driver record with
  `config_digest sha256:7d4eb213…`, and the browser cell's buildx prerequisite with
  `sha256:b22192b0…` plus an `index_digest` the other cannot have. Neither file said it was the
  second one. The prerequisite record is now written as `$BASE_IMAGE.prerequisite.json`, and
  `test_ci_s_prerequisite_build_does_not_sign_its_record_as_the_released_one` refuses a cell naming
  any record but its own image's under a released image's file name — with a
  `bases == {"cap-sandbox-http"}` precondition so the guard cannot go vacuous if the prerequisite
  build itself disappears. The same read carried the result §4 actually needed: after F-34 both
  records say `context_sha256 1da56fbc05b4…` — one value across two different build drivers, the
  first time that field was compared against anything on a clean runner rather than admired.
- **F-36** (found and closed inside this round) — `platform` was filled by the docker driver and
  left `null` by buildx on the same commit, because only one branch of the script read it, and the
  branch that omitted it is the one the release uses. A field whose presence depends on which
  builder ran is a field a reader cannot compare across records, which is the same defect shape as
  F-31's and F-32's one file over. The dry buildx build loads its image into the local store, so the
  platform is as knowable there as it was for `docker build`; the push path now names the platform of
  the child manifest it selected instead of recording only its digest. The stubbed registry manifest
  in `test_release_build_script.py` listed the amd64 child first, which let a parser that took
  `entries[0]` pass every assertion — it lists arm64 first now, and the three driver tests assert the
  values rather than the fields' presence (verified: an unfiltered pick produces
  `linux/arm64` and fails).
- **F-38** (found and closed inside this round) — CI went red on `8797a64`, whose diff against the
  previous green tip touches no file under `frontend/`. `usePageList`'s error test asserted the antd
  toast with a synchronous `getByText` immediately after awaiting the inline alert: the alert arrives
  with the state update that sets `error`, while the `App` message API mounts its DOM on a later tick,
  so the assertion passed exactly when the awaits before it happened to leave enough slack. Measured
  rate: one failure in the five completed `frontend` runs on this branch today. It is a release problem
  rather than a CI nuisance because `release.yml`'s first job is `quality-gates:
  uses: ./.github/workflows/ci.yml` — the same console suite stands between a tag and a publication.
  `usePageList.test.tsx` now awaits the same text, and the control is executed rather than argued: with
  `message.error` suppressed the test fails, so "await" is not a way to wait out an absence. The rest of
  the suite was swept for the shape — every other toast-shaped assertion goes through
  `findByText`/`waitFor` already, and the remaining synchronous `getByText("证据加载失败")` reads a
  table `emptyText` rendered in the same update as the error state, not a toast.
  What it cost is the reason to record it: the file lives under `frontend/src/`, the classifier charges
  `production_runtime`, `b671f53` therefore cannot inherit the `4aff814` rounds, and a 7200-second soak
  plus a strict FULL GA round had to be re-run for a change to nothing but a test. That is the rule
  working — a test inside the shipped tree sits beside shipped code — and the alternative is an
  exception claiming a runtime-affecting path is not, which this round declined twice already (F-33,
  F-37).
- **F-40** (found and closed inside this round) — the Kubernetes round at `b671f53` failed five gates
  (26, 28, 29, 32 and pregate E) with `httpx.RemoteProtocolError: Server disconnected without sending a
  response` and then plain `ConnectError`, on a cluster that was healthy throughout: `K8S-GATE 34`,
  which reads the pods with `kubectl` rather than through the API, passed in the same run and reported
  the expected image set with `pull_errors: []`. The cause sits one layer below F-28's fix.
  `kubectl port-forward svc/X` binds **one** endpoint for the life of the process; gate 25 force-deletes
  every backend pod, waits for a replacement pod set, and then asks `_ensure_api` whether the API came
  back — through the forward that was still bound to a pod it had just killed. A terminating pod answers
  `/health`. So gate 25 declared recovery over a socket with seconds to live, and every later gate that
  touched the API inherited the corpse. `_ensure_api` now requires two consecutive health answers a
  second apart, and an autouse fixture rebinds a stale tunnel before each gate: what gets repaired is
  the tunnel, never the verdict, so a gate whose subject really is an unavailable API still sees one.
  Both halves are exercised without a cluster, and the control is executed rather than claimed — with
  the two-answer rule deleted, the new test fails (`CONTROL OK: the single-answer rule is what the test
  refuses`). F-28's round had the same cluster and went green, because whether the forward picks a
  doomed endpoint is a race; that is the difference between a fixed bug and a bug that has not been hit
  yet, and it is why the fix is in the helper rather than in gate 25's next line. The change is under
  `backend/tests/`, so `classify_diff.py b671f53 <tip>` still reads `INHERITED` and the soak and Linux
  rounds at `b671f53` stand as the tip's evidence — a test harness repair costs a re-run of the suite
  that uses it, not another two-hour soak.
- **F-32** (found and closed inside this round) — `values-release-<version>.yaml` pinned five image
  coordinates and the production chart reads six. `worker.image` — the deployment that runs
  acquisitions — was left at the chart's placeholder registry, `ghcr.io/example/cap-backend`, so
  installing a released chart with the file written to prevent F-7 would still have failed to pull
  the worker. The test that claimed to cover it listed the same five paths the renderer did, which is
  how both agreed: the check now enumerates the coordinates out of `values.yaml` itself, and fails on
  the pre-fix renderer with `release values leave chart coordinates unpinned: ['worker.image']`.
- New and open: **F-33** — `verify-certification` requires a *successful run* of each certification
  workflow with the release job set, and that is the right shape for F-7's purposes, but it cannot
  tell a final-strict GA round from a development-mode one. Development mode exits 0 with `PLANNED`
  gates, so a green `ga-certification` job is not by itself "FULL GA 40/40". Checked rather than
  assumed: the Actions API this repository is served does **not** return a run's `inputs` — a
  `workflow_dispatch` run of `cap-ga-reliability.yml` carries `soak_seconds` in the URL used to
  start it and nothing in the response, whose key list has no `inputs` at all. So the gate cannot
  filter on "was this round asked to be strict". Closing it means reading the decision out of the
  run's own artifact — `ga-cert-artifacts/cap-cert-ga/cap-28.7-ga-certification.json` carries
  `mode`, `full_ga_certified`, `commit` and `gate_summary` — and requiring `full_ga_certified is
  true` with `commit` equal to the run's `head_sha`, in the publication gate. That is roughly
  forty lines of new machinery inside the most safety-critical gate in the repository and outside
  this round's declared scope (F-7 and F-20), so it is recorded here as a finding with a named
  remedy rather than folded in silently. What this round does guarantee is the *dispatch*: the
  strict verdict in §11 is quoted from a run of `cap-ga-certification.yml` with
  `ga_strict=true`, whose artifact is the source of the numbers, not from a green tick.
- **F-37** (new and open) — five `evidence` fields in `deployment/third-party-images.json`, and three
  citations in this report when §1 was written, point into `outputs/`, which `.gitignore` excludes. A
  pointer to a gitignored path resolves only inside a working tree where the measurement happened to
  run, and no test looks, so a fresh clone reports nothing. The captures this report depends on are now
  tracked under `docs/quality/artifacts/cap-1.0.6-rc1-artifact-closure/` and cited from there — which
  fixes the report, not the lock. The five pointers under `deployment/` were left alone deliberately:
  `classify_diff.py` puts every path starting `deployment/` in the runtime-affecting `deployment`
  category, so rewriting those strings costs a full re-certification of build inputs that did not
  change, and the way to avoid that cost is a classifier exception for a metadata-only edit — the exact
  manoeuvre §17–§20 forbids for a digest pin "only to inherit". Closing this for real means either
  generating the measurement inside the check that consumes it, or deciding explicitly that evidence
  lives at a path the lock may name; both are governance changes outside F-7/F-20.
- **F-41** (found, half closed, half open) — the sentence this entry first carried was **that nothing in
  this repository runs the chart's `values.schema.json`.** It was wrong, and wrong in the direction that
  makes a gap look bigger than it is: `ci.yml`'s "Validate Helm chart" step runs `helm lint
  deployment/helm/cap`, and `helm lint`/`helm install` apply the schema to the values they are given —
  which is what the three certification rounds do with their `--set` coordinates. The grep I should have
  done first took one command and contradicted the paragraph. What *was* uncovered, and is now closed:
  the file an operator is told to install with — `values-release-<version>.yaml` — reaches no helm at
  all. `release-chart` runs `helm lint` on the chart with its defaults and then `cp`s the rendered file
  into `dist/`, so the released values were validated by nothing but the test that checks their content.
  `test_the_released_values_satisfy_the_chart_schema` now evaluates every coordinate the renderer writes
  against the node the schema declares for it, reads the constraints out of the schema rather than
  restating them, and fails loudly if the schema grows a keyword the checker does not evaluate — the
  alternative was adding `jsonschema` to the dev extras, which `pyproject.toml`/`uv.lock` makes a
  `dependency` change and therefore a third re-certification, in the middle of the second.
  What remains open is the asymmetry that checker exposes: `backend.image` and `worker.image` require a
  non-empty `tag` with a pattern that rejects the empty string, while `worker.sandbox.image`,
  `worker.sandbox.browserImage`, `egressProxy.image` and `frontend.image` accept tag **or** digest
  through `anyOf`. So `cap.imageRef`, which prefers the digest, cannot be driven to a digest-only pin
  for the two workloads that matter most — the API and the acquisition worker — even though the chart
  advertises that mode for the other four. Fixing it is a `deployment/` edit, which the classifier
  charges as runtime-affecting, so it belongs at the head of the next round rather than in the middle
  of this one's re-certification; the test asserts the difference today so the day it is unified, the
  suite says so.
- **F-39** (new and open) — **CAP images are not reproducible by digest.** The round's own clean-runner
  builds measure it: `cap-sandbox-http` and `cap-egress-proxy`, built at `a79d29c` and again at
  `b671f53` with identical `dockerfile_sha256`, identical `context_sha256` and identical pinned bases,
  came out with different `config_digest`s and different `index_digest`s. Those two Dockerfiles carry no
  `VERSION`/`REVISION` label, so nothing but build metadata — timestamps in the image config and in the
  layer history — distinguishes the pairs. (`cap-sandbox-browser` is not a clean case: its base was
  rebuilt too, so its digest moving proves nothing either way; backend and console *do* carry
  `org.opencontainers.image.*` labels, and a differing digest there is the design working.)
  What the finding does not damage: the release contract pins the **published** index digest into
  `values-release-<version>.yaml`, so an operator runs the exact bytes that were scanned and certified,
  and the input hashes still prove what went into them. What it does prevent is the verification the
  phrase "reproducible build" invites — rebuild at the tag and compare digests — which fails for these
  images today. Closing it means `--timestamp`/`SOURCE_DATE_EPOCH` discipline across all five
  Dockerfiles, plus layer metadata and the file times of what those images copy in (the Playwright
  browser set in the sandbox image is the obvious one). It changes what every image *is*, so it costs a
  full re-certification — F-38 was the same rule on a much smaller scale — and belongs to a round of its
  own rather than to the last hour of this one.
- Still true from the previous round: registry digests, SBOM and provenance **attestations** exist
  only once images are pushed; the rollback exercise cannot exist before 1.0.6 does.

## 11. Verdict

_Same rule as the previous round: the gates above are only worth what their runs say. Stated at the
end of §9's rounds._
