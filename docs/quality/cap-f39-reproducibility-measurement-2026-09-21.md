# CAP IMAGE REPRODUCIBILITY — MEASUREMENT ONLY (batch 1, stage 4 / plan item G(i))

Date: 2026-09-21. Scope: measure what the existing build evidence can and cannot
compare. No `SOURCE_DATE_EPOCH`, no package-resolution change, no Dockerfile edit, no
new CI job was run, and **no cause of divergence is claimed** — the point of this file
is to establish what would have to be recorded before such a claim could be made.

## 1. Why the measurement is over captured evidence rather than two new builds

The acceptance criterion for F-39 is two independent clean builds of *one commit*
compared element by element. Producing that needed either a container engine on this
machine or a new CI job — and a CI job means pushing, which batch 1 holds until its
results are summarised for review. Checked rather than assumed on 2026-09-21: the CLI
is present (docker 29.6.1) but the Linux engine is not up, and `docker info` answers
`failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`.
Booting Desktop and waiting is an option for a later round; it is not a measurement.

What exists instead is 17 build-evidence records dropped by clean `ubuntu-latest`
runners during the closure and re-certification rounds, describing **15 distinct
builds**: two pairs among the 17 are byte-identical files downloaded twice from one
run. Each record is what `scripts/release/build_release_image.sh` writes beside the
image it just built.

## 2. Element coverage: what the evidence records at all

The comparison the criterion asks for names these elements. Column two is how many of
the 17 records state it.

| Element to compare | Recorded | Note |
| --- | --- | --- |
| OCI image config digest | 17/17 | the only content-level identity captured for every build |
| Index / manifest-list digest | 4/17 | only the buildx builds produce one; the `docker`-driver builds record `null` |
| Platform manifest digest (`linux/amd64`) | 0/17 | the field exists in the evidence schema and is empty in every record here |
| Compressed layer digests | 0/17 | never captured |
| Uncompressed layer diff IDs | 0/17 | never captured |
| Index / manifest-list composition (which manifests, which platforms) | 0/17 | never captured |
| SBOM descriptor (digest, media type) of the attached attestation | 0/17 | only `attestations.sbom: true/false`, and false in all 17 (none of these builds pushed) |
| Provenance descriptor, and the attestation's own content | 0/17 | same: `attestations.provenance` boolean only |
| buildx version | 0/17 | `docker/setup-buildx-action@v3` with no `buildx-version` input, and nothing records what that resolved to |
| BuildKit version / builder driver instance | 0/17 | `build_driver` records only which code path ran (`docker` or `buildx`) |
| Base image digest | 17/17 | `base_refs` names each `FROM` with its `@sha256:` |
| Dockerfile bytes | 17/17 | `dockerfile_sha256` |
| Build context bytes | 17/17 | `context_sha256` |
| Runner / environment metadata (image, region, kernel, mtime policy) | 0/17 | nothing records the machine the build ran on |
| Commit built | 17/17 | `source_revision` |

So of the fifteen elements the criterion names, this repository carries five in every
record and the index digest in the four buildx ones, and it records none of the layer,
manifest-composition, attestation-descriptor, toolchain or runner facts. A two-build comparison run today against this format would compare config
digests and conclude "different", with nothing else to say.

## 3. The comparisons the captured evidence does allow

Grouping by identical inputs — same Dockerfile bytes, same context bytes, same base
digest — gives four groups with more than one build. `source_revision` is listed
because it is the confound: every one of these groups spans *different commits*.

| Image | Driver | df / ctx / base identical | builds | `source_revision` | config digest | index digest |
| --- | --- | --- | --- | --- | --- | --- |
| `cap-sandbox-http` | `docker` | yes | 2 | `a79d29c5` / `b671f537` | **differ** (`7d4eb213…` / `6070b644…`) | both null |
| `cap-egress-proxy` | `docker` | yes | 2 | `a79d29c5` / `b671f537` | **differ** (`72353f9c…` / `645cae8f…`) | both null |
| `cap-frontend` | `docker` | yes | 2 | `02a81ebc` / `b671f537` | **differ** (`c77ad8de…` / `9432c4ba…`) | both null |
| `cap-sandbox-http` | `buildx` | yes | 4 | `a79d29c5` ×2 (one build, captured twice) / `8d2110d6` / `b671f537` | the two same-run copies match; across commits **differ** | `bd88c30a…` / `aced5c04…` / `c7d9ef3e…` — **differ** |
| `cap-sandbox-browser` | `docker` | yes (base `cap-sandbox-http:1.0.6-rc1`, a *tag*) | 4 | `a79d29c5` ×2 (one build, twice) / `8d2110d6` / `b671f537` | same-run copies match; across commits **differ** | all null |

Two readings of this table are worth keeping apart.

**What it supports.** For four of the five images, builds whose *recorded* inputs were
byte-identical produced different image configs and, where a buildx build made one,
different indexes. The recorded inputs therefore do not determine the recorded output.
That is a measurement of the evidence format, not of Docker.

**What it does not support.** Not one of these groups is two independent builds of the
same commit: the only same-commit pairs in the whole set are the same build's evidence
file downloaded from two jobs of one run (byte-identical: `6d6f5a6f…` and
`f7b19104…` respectively). Every differing pair also differs in `source_revision`, and
the candidate explanations — checkout mtimes, the state of the Debian/Alpine package
mirrors that day, the buildx and BuildKit versions the runner resolved, the runner image
itself — are exactly the ones §2 shows are never recorded. They remain candidates: this
evidence attributes the divergence to none of them, and plan item G keeps them listed
as hypotheses for the same reason.

One further measured fact: `cap-sandbox-browser`'s base is recorded as
`cap-sandbox-http:1.0.6-rc1`, a mutable tag rather than a digest, in all four of its
records. Whatever else is true of that build's reproducibility, its base was not pinned
in the way the other four images' bases are.

**The measurement gap, in two parts.** (a) There is no independent two-build pair at one
SHA anywhere in the captured evidence — every genuine cross-build comparison here crosses
commits, and the same-commit records are one build read twice. (b) Ten of the comparison
fields are missing from every record: layer compressed digests, layer diff IDs, the
platform manifest digest, index/manifest-list composition, the SBOM and provenance
descriptors, the buildx version, the BuildKit version, the builder endpoint, and runner
metadata. So the comparison the acceptance criterion asks for cannot be performed with
what is on file, at any commit, on any of the five images — and that, not a diff between
two builds, is the finding of this stage.

## 4. The reproducibility model is PROPOSED, not policy

Plan item G states this and it is repeated here because it is the part that cannot be
derived from the bytes: *which* digest is the reproducibility claim. **PROPOSED MODEL —
not an established policy, and not a gate contract:** nothing here fixes a digest rule for
CI to enforce, because the two-build structural comparison that would justify one has never
run. Proposed —
platform manifest digest plus OCI config digest, with attestation manifests excluded
(on the assumption that their own invocation timestamps can move an index digest while
every layer matches — the pushed attestations were never read, so this is a definitional
choice made in the absence of evidence, not a measured behaviour), and the index digest
kept as a "verify against the published value" check
rather than a rebuild comparison. Until that model is agreed **and** the comparison in §5
has run, a "reproducible" badge — or a release gate keyed to a timestamp-normalised config
digest — would be a statement about an undefined quantity, so neither is adopted here.

## 5. The measurement that would answer F-39 (spec only; nothing here implements it)

For the batch that can afford a round:

1. One commit, one runner image, two jobs that do not share a cache, each running
   `build_release_image.sh --tag repro-<n>` without `--push`, and each writing evidence
   that additionally records: the manifest media type and every layer's compressed
   digest and diff ID, the index composition (which manifests, which platforms), the SBOM
   and provenance descriptors if any, `buildx version` and the BuildKit version the
   builder reports, the builder driver and endpoint, the runner's image name and kernel,
   and `stat`-level mtime evidence for the context.
2. Compare **element by element**, not digest-to-digest, and publish the first field
   where the two disagree. A comparison that only prints "index digests differ" is what
   this round already has.
3. Acceptance: the two builds match on the digests chosen in §4. Anything less is
   reported as a divergence with its first differing field named, not as a failure of
   the test.

Steps G(ii)–(v) of the plan (`SOURCE_DATE_EPOCH`, digest-pinned syntax directive and
browser base, apt/apk resolution policy, pinned buildx, the comparison job) all touch
Dockerfiles or the build scripts, so they cost a full re-certification round and are
batch 4's, not this batch's.

## 5.1 Batch 3A extends the field list (2026-09-23)

Batch 3A did not run the two-build experiment; it built the record the experiment has to
fill in. `scripts/release/record_build_producer.py --mode observe --oci-tar <archive>` now
writes an `f39_measurement` block parsed from an `--output type=oci` archive with stdlib
`tarfile` (`index.json` → manifests → config blob), plus a producer identity beside it.
Element by element, against §5's list:

| §5 element | Where it is recorded now | Still missing |
| --- | --- | --- |
| layer compressed digests | `f39_measurement.platform_manifest.layer_digests` | — |
| layer diff IDs | `f39_measurement.image_config.diff_ids` | — |
| platform manifest digest | `f39_measurement.platform_manifest.digest` + `media_type` | — |
| image config digest | `f39_measurement.platform_manifest.config_digest` | — |
| index composition | `f39_measurement.index_composition` (digest, media type, platform, `attestation` flag) | — |
| attestation descriptors | `f39_measurement.attestation_descriptors` | a non-pushed scratch build runs `--provenance=false --sbom=false`, so the list is empty by construction; the SBOM/provenance descriptors of a *pushed* build are still unmeasured (F-25's pending item) |
| buildx version | `observed.executing_buildx.{path,version,commit}`, and every `docker-buildx` on disk under `observed.docker_cli_plugin.candidates` | — |
| BuildKit version / driver / endpoint | `observed.builder.{driver,nodes[].buildkit,nodes[].endpoint,nodes[].status}`, `observed.engine.version` | — |
| builder instance | `observed.builder.builder` + `requested_builder` + `observed.builders` (`docker buildx ls`, verbatim) | — |
| base image digest | `observed.builder.running_image.*` for the *builder's* base, with its pull digest read from the image object that carries `RepoDigests`; `pinned_index_resolution.children` plus `platform_child` (the child digest, **its own `config.digest`** and layer count) for the pinned BuildKit index; the image's own `base_refs` remain in the release evidence | the built image's base *platform child* is recorded only on a `--push` build (`platform_digest`), so a dry record still cannot say which child was pulled for the product's own `FROM` |
| config timestamps | `f39_measurement.image_config.created` and `history_entries` | `stat`-level mtimes of the context are recorded nowhere |
| runner metadata | `runner.{runner_environment,runner_name,imageos,imageversion,runner_arch,runner_temp,github_run_id,github_sha}` | kernel and region are not in the environment the runner exports |
| frontend | `f39_measurement.frontend`, read from the Dockerfile's own `# syntax=` line | — |
| commit built | `runner.github_sha`, and the release evidence's `source_revision` | — |

Two counters keep the claim inside its own boundary: `f39_measurement.builds_compared` is
`1`, and `measurement_status` reads `PARTIAL -- one build measured; attributing a digest
difference needs two independent builds of one commit`. Those two fields are what stop the
format from being mistaken for the measurement, and no amount of field coverage changes
that: until step 1 of §5 actually runs twice on one commit, F-39 stays open.

## 6. Status

F-39: **measured, unattributed, not closed.** The gap is now expressed as a field list
rather than as an observation that two builds differed, and the cost of answering it
properly is known: one commit, two clean builders, and the fields above in the evidence.
Nothing in this file should be quoted as the reason the images differ, because no
reason has been demonstrated.

After Batch 3A the field list is no longer hypothetical — the thirteen elements §5 asked
for have a named home in the evidence (see §5.1) and the recorder writes them on every CI
observation run -- but F-39 itself is **unchanged in status**: one build was measured, not
two, so the finding remains PARTIAL and the experiment of §5 step 1 is still a separate,
later step (approved as such, after producer observation, not instead of it).
