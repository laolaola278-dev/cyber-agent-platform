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

Machine-readable form: `outputs/artifact-closure/image-graph.json`, with the failing first run of
the gate preserved in `outputs/artifact-closure/gate1-image-set-diff-before-fix.txt`.

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
manifest bytes**. Output: `outputs/artifact-closure/registry-base-digests.json`.

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
makes a difference visible instead of a matter of faith.

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

`--local-docker` therefore builds with `docker build`, which does share the store, and CI uses it for
the browser image. The evidence records `build_driver` and derives `attestations` from the flags the
builder was handed, so a docker-driver dry build can never be mistaken for an attested release
artifact; `release.yml` still builds every image with buildx and `--push`.

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

## 6. Helm contract and the fresh-install audit

- `templates/_helpers.tpl` gained `cap.imageRef`, which composes `{repository, tag, digest}` and
  prefers the digest; an empty tag falls back to `Chart.AppVersion`, so the chart carries one
  version literal (already a version carrier) instead of six that can disagree.
- `values.yaml` declares six image coordinates for the five CAP images (`backend.image` and
  `worker.image` name the same artifact); `values.schema.json` requires a repository
  everywhere and, through `anyOf`, one of tag or digest.
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
(`outputs/artifact-closure/diff-c52dcb9-to-final-tip.json`): 49 files, of which **16 are
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
*was* F-20 — and a tag resolved five days ago cannot be reconstructed from a registry that has
since served other bytes. Claiming equality here would have been the cheapest possible way to skip
a re-certification, so it is not claimed: the Dockerfile digest change is treated as
runtime-affecting, `c52dcb9` is **not** inherited, and a new certified SHA is established by
re-running CI, the Linux release layer with its PostgreSQL matrix, the Kubernetes suite (now 34
gates), the 7200 s reliability soak and the strict FULL GA round on the closure tip. §20's rule was
respected in the making: no "metadata-only" exception was added to the classifier for pinned
Dockerfiles, because a base image decides which OpenSSL ships in the artifact.

## 8. ARTIFACT-GATE matrix

| # | Gate | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Helm CAP image set == release workflow image set | **PASS** (statically enforced) | `test_chart_and_release_publish_the_same_image_set`; `outputs/artifact-closure/image-graph.json`; the before-fix refusal quoted in §1 |
| 2 | No production CAP image uses `:latest` | **PASS** | `test_no_production_chart_reference_is_latest`, `test_no_dockerfile_layers_on_a_cap_image_by_mutable_name`, K8S-GATE 34 at runtime |
| 3 | All external Dockerfile FROM refs digest-pinned | **PASS** | `test_every_external_dockerfile_base_is_locked_and_digest_pinned` + §3 table |
| 4 | Third-party lock covers every external base | **PASS** | `test_lock_base_entries_are_actually_used_by_a_dockerfile`, `test_locked_base_digests_carry_their_provenance` |
| 5 | Clean runner builds all required images | **PASS** | CI run 35485625550: five `release-image-builds` cells green on `ubuntu-latest` with push=false, per-image evidence artifacts uploaded. It took two red runs to get here, both recorded in §5 |
| 6 | Trivy covers all release images | **PASS** | CI `release-image-builds` scans each matrix cell it builds (derived, cannot drift); `release-image-security` lists its five names by hand, so `test_every_published_image_is_scanned_in_the_release` now requires that list to equal both the published set and the chart-derived set, with a control that dropping a name breaks it. The two workflow scans use one policy (HIGH+CRITICAL, unfixed ignored, `exit-code: 1`); GA-GATE 22's `security_policy.json` blocks only fixable CRITICALs. That difference is deliberate and not a hole: for a release image the stricter workflow policy runs too, and the policy file governs the GA verdict, not publication |
| 7 | SBOM enabled for all release images | **PASS** | `release.yml` completeness gate refuses a record without `attestations.sbom`; script attaches it on push builds |
| 8 | Provenance enabled for all release images | **PASS** | as above, with `REVISION` bound to the tag target SHA |
| 9 | Browser image has an immutable internal base relationship | **PASS** | no-default `ARG SANDBOX_HTTP_BASE`; release passes `cap-sandbox-http@<index digest>` |
| 10 | Fresh kind deployment has no missing image | **PASS** | Kubernetes certification run 35489588677 on `b447436`: 34/34 gates green, including K8S-GATE 34 (every CAP image in the running pods and in the worker's sandbox coordinates is one of the five, at the tag the job built, with no `ImagePullBackOff`/`ErrImagePull`) |
| 11 | Publication graph blocks a partial image release | **PASS** (statically enforced) | `publish-release`/`release-chart` need `release-image-completeness`; the gate's refusal paths are executed by `test_completeness_gate_refuses_a_missing_image` and its siblings |
| 12 | Classifier / recertification decision truthful | **PASS** | §7: RECERTIFICATION_REQUIRED, no byte-identity claim, no classifier exception added |

## 9. Certification runs on the closure tip

Closure tip: **`86e105d`** on `release/1.0.6-rc1`. `c52dcb9`'s evidence is **not** inherited (§7), so
each round below has to be green at a SHA that reaches the tip through inheritable-only commits.

| Round | Attempt | SHA | Conclusion |
| --- | --- | --- | --- |
| CI | [35484377542](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35484377542) | `0b143ac` | **failure** — F-26, the build script aborted after building |
| CI | [35485625550](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35485625550) | `3cc6579` | cancelled by the next push; **all five `release-image-builds` cells success** (ARTIFACT-GATE 5/6) |
| CI | [35490251245](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35490251245) | `86e105d` | running |
| Linux layer=release + PostgreSQL matrix | [35485710392](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35485710392) | `3cc6579` | **success** |
| Kubernetes | [35485711757](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35485711757) | `3cc6579` | **failure** — F-27 |
| Kubernetes | [35488262094](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35488262094) | `c69d960` | **failure** — F-28 (GATE 34 itself passed) |
| Kubernetes | [35489588677](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35489588677) | `b447436` | **success** — 34/34 gates, K8S-GATE 34 included (ARTIFACT-GATE 10) |
| FULL GA, strict (`CAP_GA_STRICT=1`, 40/40) | [35485715411](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35485715411) | `3cc6579` | **failure** — F-27 |
| FULL GA, strict | [35488263281](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35488263281) | `c69d960` | running |
| Reliability soak (7200 s) | [35485713552](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35485713552) | `3cc6579` | running |

Three of the four rounds reached their green state on `3cc6579`/`b447436`, and the commits between
those SHAs and the tip are `test_harness`, `ci_workflow`, `certification_generator` and `docs` —
checked with `scripts/release/classify_diff.py`, which reports `INHERITED` for each hop. A gate that
errors is reported as a failure and re-run rather than read as close enough: the second Kubernetes
attempt is in that table because it failed, even though the new gate inside it passed.

_Filled in as each round finishes; nothing here is asserted before its run is green._

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
  works. The third attempt (run 35489588677) is green with all 34 gates.
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
  the new observed-image-set wrote it to `backend/outputs/cap-cert/k8s-image-set.json` while the
  generator read `outputs/cap-cert/`: `CAP_CERT_OUT` is a *relative* path and the two processes start
  in different directories, so the green run's artifact still said `not_observed`. The safe fallback
  did its job — it declined to invent coordinates — but the fix was incomplete, and only inspecting
  the published artifact (not the job's green tick) showed that. The gate now resolves the path
  against the repository root and stamps the record with its commit, and the generator refuses a
  record from another commit: searching several candidate directories is how this repository was once
  certified by a stale junit, so the answer is a stamp, not another fallback path.
- **F-32** (found and closed inside this round) — `values-release-<version>.yaml` pinned five image
  coordinates and the production chart reads six. `worker.image` — the deployment that runs
  acquisitions — was left at the chart's placeholder registry, `ghcr.io/example/cap-backend`, so
  installing a released chart with the file written to prevent F-7 would still have failed to pull
  the worker. The test that claimed to cover it listed the same five paths the renderer did, which is
  how both agreed: the check now enumerates the coordinates out of `values.yaml` itself, and fails on
  the pre-fix renderer with `release values leave chart coordinates unpinned: ['worker.image']`.
- Still true from the previous round: registry digests, SBOM and provenance **attestations** exist
  only once images are pushed; the rollback exercise cannot exist before 1.0.6 does.

## 11. Verdict

_Same rule as the previous round: the gates above are only worth what their runs say. Stated at the
end of §9's rounds._
