# CAP POST-RC HARDENING — BATCH 3A PRODUCER OBSERVATION REPORT

Scope as approved: **Observation Step 1 only.** Option A ("published images must be
produced by an explicitly selected, pinned and observed producer") is recorded as the
*desired future contract*; the release build path is not switched, and no producer
mismatch blocks publication. Every number below is read from a named commit, CI run,
artifact or file. Two CI observation cycles ran while this work was in flight and each one
changed the recorder rather than the prose; both are reported, because the point of the
batch is what the instrument can be made to say, not how quickly it says something
comfortable.

Four cycles were needed, and the last of them is what makes this report's verdict possible:
CI's own record now reads the running BuildKit image's digest and the pinned index's own
config digest for that platform, and both agree.

## A. Base and docs tip

| Fact | Value |
| --- | --- |
| Runtime candidate certified by Batch 2 | `257ba18d8cb6c22d330701b69713544254e4f03a` |
| Docs tip before Batch 3A (pushed earlier) | `f23d1fd7ef07b63a1a8f4b8bd764d6f1b87ed938` |
| `origin/main` re-read at Stage 0 (API GET `/git/ref/heads/main`) | `f23d1fd…` → the post-Batch-2 reconciliation is **PUSH ALREADY COMPLETED**; nothing was re-pushed or rewritten |
| Batch 3A commit set (in order) | `313cf63` recorder observe mode + CI observation job + tests; `13930dc` docs (F-44 instrument note, CHANGELOG, F-39 §5.1, §M.8 correction); `8610914` fix forced by CI's first record; `c8a5d2b` fix forced by CI's second record; `7d3d6a5` fix forced by CI's third record -- the digest read moved to the image object and the registry read taken down to the platform child's config digest |
| Head whose CI record this report measures | `7d3d6a5b74262426b2409b177df10ea562ac3b00` (plain fast-forward, `c8a5d2b..7d3d6a5`) |
| This report's own commits | docs-only commits on top of `7d3d6a5`, ending at whatever tip carries this file -- a report cannot be evidence about its own SHA, so none below is. Each classifies INHERITED (§K); the CI their push triggers is listed in §L, and every measured field comes from `7d3d6a5`'s run |
| Release tag created | **none**; publication performed **none**; `v1.0.6-rc1` untouched (§M) |

Stage 8's STOP condition never fired: no commit in the set touches `deployment/`, any
`Dockerfile`, product runtime code, or the release build behaviour of
`build_release_image.sh` / `release.yml` (see §K).

## B. Evidence schema (`configured` / `observed` / `comparison`, kept apart)

`scripts/release/record_build_producer.py --mode observe` writes one JSON record:

```
mode, configured, observed, pinned_index_resolution, comparison,
producer_alignment, f39_measurement, runner, [incomplete], [contract_gaps]

configured.lock      buildx_version, buildkit_image, buildkit_repository, buildkit_tag,
                     buildkit_digest, entries, source          <- deployment/third-party-images.json
configured.workflow  buildx_version, builder_name, driver, buildkit_image,
                     buildkit_repository/tag/digest, driver_opts_image,
                     declared_env_keys, source, job            <- the workflow file's own text
observed.executing_buildx   path, path_status, module, version, commit, build_metadata,
                            resolved_path, identified_by, docker_cli_path
observed.docker_cli_plugin  candidates{path -> exists,status,path,version,commit},
                            any_installed, path_lookup{status,error}, resolves_to
observed.builder            builder, requested_builder, driver, builder_last_activity,
                            nodes[] (name/endpoint/status/buildkit version/Driver Options/
                            platforms/worker hostname/labels/gc policy),
                            node_image, node_driver_options_image, node_image_note,
                            container_lookup{worker_hostname, candidates, daemon_rows,
                            daemon_rows_status, matched_by, note},
                            container, container_config_image, container_image_id,
                            config_digest, repo_digests, repo_digests_read{command,status,
                            asked_of,note}, image_platform, image_platform_status,
                            running_image{repository,tag,digest,image_id,config_digest},
                            container_reference
observed.docker_engine / observed.engine / observed.buildx_version / observed.builders
                            (the verbatim `docker buildx ls` table)
pinned_index_resolution  ref, media_type, is_index, children[], attestation_descriptors[],
                         platform_child{platform,status,coordinate,manifest_digest,
                         config_digest,layers,media_type}   (status READ / ERROR / AMBIGUOUS
                         / NOT_NAMED / NOT_REQUESTED)
comparison  lock_vs_workflow, workflow_vs_observed, lock_vs_observed
            -- each {fields{declared,read_back,relation}, required, status}, plus
            lock_vs_observed.digest_relation (manifest layer), .config_digest_relation
            (config layer) and .layers, and comparison.buildx_binaries
```

Three distinctions the batch asked for and the record keeps:

1. **Two authorities, not one restatement.** `configured.workflow` is parsed out of
   `.github/workflows/ci.yml` (`with:` of the `setup-buildx-action` step plus the job's own
   `CAP_OBSERVE_BUILDER` / `CAP_OBSERVE_DRIVER` / `CAP_OBSERVE_BUILDKIT` `env:` literals).
   A value handed to the recorder on `--builder` is only a fallback and is recorded as such;
   a declaration containing `${{ … }}` is filed under `expressions` and never compared as a
   string. `lock_vs_workflow` therefore has three required fields -- buildx version, the
   BuildKit digest the *observation builder* declares, and the BuildKit digest the *setup
   action* declares -- so a workflow that pins one BuildKit for the action and another for
   its own builder is caught as a disagreement with itself.
2. **`observed` is never a copy of `configured`.** Every observed field comes from a
   command; a failed command keeps `ok: false`, a `status` and the tool's own complaint.
   `incomplete` lists a lost read (`ok` false), while a stated absence -- the docker driver
   has no BuildKit container, `--docker-cli-build` names the daemon as producer -- answers
   with `ok: true` plus a `reason`, so the two are distinguishable in the artifact.
3. **Three answers, not one boolean.** `producer_alignment.verdict` is *built from* the
   three component statuses and reports them beside it
   (`{"verdict": …, "components": {…}}`); no component is derived from the summary.

Digest layering, which is why string equality would have been wrong in both directions: the
lock pins a **manifest-list (index)** digest, the pull digest of a running image names the
**child manifest** the daemon fetched for its platform, and `docker inspect`'s `.Image` is the
**config** digest. `pinned_index_resolution` reads the index from the registry
(`docker buildx imagetools inspect --raw`, which asks the registry, not the daemon) and then
reads **the child for the platform the running image itself reports** -- `linux/amd64` on these
runners, taken from `docker image inspect {{.Os}}/{{.Architecture}}`, never from `RUNNER_ARCH`
or a tag -- so the record has a pinned-side string on each layer. Two relations follow:
`digest_relation` returns `same_digest`, `running_is_a_child_of_the_pinned_index`,
`running_digest_is_not_named_by_the_pinned_index` or `pinned_index_unresolved`, and
`config_digest_relation` returns `running_config_is_the_pinned_child_config` or its negation;
an unreadable side is `UNKNOWN` with a reason, never a match. `buildkit_digest` remains the
only *required* digest field, so a conforming config layer cannot stand in for a manifest layer
that was never read.

## C. Action-installed buildx

The job runs the same pinned action the release jobs run --
`docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f  # v3.12.0` with
`buildx-version: v0.37.1` and
`driver-opts: image=moby/buildkit:v0.33.0@sha256:6c2fa84a…` -- so the measurement is of the
same installation path, not a neighbouring one. What the runner answered:

| CI head | `docker buildx version` value | executing version | `resolved_path` | `identified_by` |
| --- | --- | --- | --- | --- |
| `13930dc` | `github.com/docker/buildx v0.37.0 ac30b249211430b85fb8f37b6e7154b5c47ba0b6` | **v0.37.0** | not resolved (contract gap) | -- |
| `8610914` | `github.com/docker/buildx v0.37.1 0b265a9f62db554fa9aba6dd19e1bd5704bc7d8a` | **v0.37.1** | `/usr/libexec/docker/cli-plugins/docker-buildx` | the only installed candidate whose version **and** commit equal the executing line's |
| `c8a5d2b` | `github.com/docker/buildx v0.37.0 ac30b249211430b85fb8f37b6e7154b5c47ba0b6` | **v0.37.0** | `/usr/libexec/docker/cli-plugins/docker-buildx` | the same labelled match |
| `7d3d6a5` | `github.com/docker/buildx v0.37.0 ac30b249211430b85fb8f37b6e7154b5c47ba0b6` | **v0.37.0** | `/usr/libexec/docker/cli-plugins/docker-buildx` | the only installed candidate whose version and commit equal the executing line's |

Five heads, three facts -- and all of them matter for Step 2.

* The runner **prints no install path** in the version line, so "which binary executed the
  build" cannot be answered by `docker buildx version` alone. Stage 2 now has a second,
  labelled route (match the executing version *and* commit against the binaries on disk);
  zero matches or two identical matches stay a gap rather than becoming a pick.
* The *same* job text, run four times, reported **two different executing versions**
  (v0.37.0, v0.37.1, v0.37.0, v0.37.0, v0.37.0), each time with exactly one buildx present
  in the scanned directories and `which docker-buildx` finding nothing. So the executed buildx
  version is
  **not deterministic** across GitHub-hosted runners: the pin is honoured on some runners and
  silently not on others, and F-44's mismatch is therefore a property of runner state rather
  than of the workflows. This batch does not know which action or image behaviour produced the
  difference, and no claim is made about the published images -- their build path still
  selects no builder (§H, §N.2).

## D. Docker CLI plugin buildx (what is on the machine)

`observed.docker_cli_plugin` scans the docker CLI's four plugin directories plus whatever
`which docker-buildx` resolves, and asks each candidate its own version. Measured at
`8610914`:

| Candidate | exists | version reported |
| --- | --- | --- |
| `/home/runner/.docker/cli-plugins/docker-buildx` | no | -- (`FileNotFoundError`) |
| `/usr/local/lib/docker/cli-plugins/docker-buildx` | no | -- |
| `/usr/libexec/docker/cli-plugins/docker-buildx` | **yes** | **v0.37.1** `0b265a9f…` |
| `/usr/lib/docker/cli-plugins/docker-buildx` | no | -- |
| `which docker-buildx` | not found | `path_lookup.status = ERROR`, `error = "exit 1"` |

At `7d3d6a5` the scan answered the same way -- exactly one existing candidate,
`/usr/libexec/docker/cli-plugins/docker-buildx`, reporting `v0.37.0`, with `which docker-buildx` still
not found -- so the fourth head's executing `v0.37.0` is the version that one
file reports there, not a second binary the recorder preferred.

That is the record of the action-installed buildx **and** the CLI-plugin buildx without
cherry-picking: on this runner the action's binary landed in a system plugin directory and
is the same file the CLI dispatches to; there was no second binary to prefer. Where a
PATH-resolved binary does exist, `which docker-buildx` is now scanned like any other
candidate (the first observation run showed all four plugin directories empty of the
action's install, which is why the lookup exists at all), and a failing lookup is recorded
as `path_lookup` instead of being dropped. `comparison.buildx_binaries` keeps
`executing`, `executing_version`, `identified_by`, `installed_on_disk` and a note saying
explicitly that the executing one is what built the bytes.

## E. The named, non-publishing builder

`docker buildx create --name "$CAP_OBSERVE_BUILDER" --driver "$CAP_OBSERVE_DRIVER"
--driver-opt "image=$CAP_OBSERVE_BUILDKIT" --use` then `inspect --bootstrap`, with the
three values declared as literals in the job's `env:` so the workflow file is what the
recorder reads. Measured (all four runs):

| Field | Value read back |
| --- | --- |
| `builder` / `requested_builder` | `cap3a-producer-observation` |
| `driver` | `docker-container` |
| node name / endpoint / status | `cap3a-producer-observation0` / `unix:///var/run/docker.sock` / `running` |
| BuildKit version | `v0.33.0` (label `Buildkit version:` on buildx v0.37; there is **no** per-node `Image:` line -- the declared reference appears as `Driver Options: image="moby/buildkit:v0.33.0@sha256:6c2f…"`) |
| platforms | `linux/amd64, linux/amd64/v2, linux/amd64/v3, linux/386` |
| worker attributes | `org.mobyproject.buildkit.worker.hostname` = `d613bc1212b9` (`13930dc`) / another id per run; snapshotter `overlayfs`, executor `oci`, network `host` |
| `docker buildx ls` | the action's `builder-<uuid>` **and** `cap3a-producer-observation*`, both `docker-container`, both `v0.33.0` -- the `*` marks the builder the CLI would use by default |

Publishing surface of the job: none. Its steps contain no `--push`, no `login-action`, no
`--tag`/`-t`, no `ghcr.io`, and no job-level `permissions` block (so the workflow-level
`contents: read` is all it has). That is asserted by
`test_the_observation_job_publishes_nothing`, which reads the job's own text -- and the claim
was also re-read straight from the file, independent of that test, so a bug in the test could
not hide a real token: the `producer-observation:` block is `.github/workflows/ci.yml` lines
484-607, and none of `--push`, `login-action`, `build-push-action`, `ghcr.io`, `--tag`,
` -t `, `docker push`, `push: true` or `permissions:` occurs anywhere in it.

## F. The actual BuildKit container identity

At `c8a5d2b` the container is **discovered**, and the record shows how. The daemon listed two
running buildx containers and the node's worker hostname named which one belongs to this
builder:

```
container_lookup.matched_by      = worker-hostname
container_lookup.worker_hostname = f5b5e0175115
container_lookup.daemon_rows     = two running buildx containers listed by the
                                  daemon: f5b5e0175115 /
                                  buildx_buildkit_cap3a-producer-observation0 /
                                  image moby/buildkit:v0.33.0 / running, and
                                  472cda9c7306 /
                                  buildx_buildkit_builder-b99e6536-...-0 /
                                  moby/buildkit:v0.33.0 / running  (the action's
                                  own builder -- which the join excludes)
container                        = buildx_buildkit_cap3a-producer-observation0     (node-named)
observed.builder.status / ok     = READ / true; driver docker-container; node status running
buildkit_version                 = v0.33.0
container_config_image           = moby/buildkit:v0.33.0@sha256:6c2fa84a6b61ccd72899dde4239f8d5717f05f9a8ca6f3cad185fb1a95a94de3   (the pinned reference)
container_image_id (.Image)      = sha256:41f915d3a122bca46b3da83160cd805697b0faa1bf30b0c0de851eb78f992c70
                                 -> reported as `config_digest`, the *config* layer
repo_digests                     = []  -> running_manifest_digest.status = UNKNOWN
record.exit = 0; contract_gaps absent; incomplete absent
```

The observation job is therefore **green** at this head: the instrument no longer refuses to
look, and `--self-check` is satisfied.

What that answers, and what it does not. Stage 3's identification half is answered exactly:
the running BuildKit container of the named builder is known through a join between two
independent readings, and its config digest, BuildKit version and the reference the daemon
recorded for the pull are all in the artifact. Stage 3's **verification** half was not answered
by that record, and the reason turned out to be a bug in the instrument rather than a property
of the runner: `RepoDigests` is an **image-object** field, so the container-level ask was
answered

```
repo_digests_status.error = template parsing error: … map has no entry for key "RepoDigests"
```

-- a question that can never be answered, recorded as an absence. Promoting the pinned digest
that appears inside `.Config.Image` into an equality claim would have been the inference the
batch forbids (no digest equality from a tag string, `buildx inspect` text or `driver-opts`),
because that string is the daemon's record of **what it was asked to pull**, not a read of what
it runs.

The like-for-like read the section asked for was then performed, read-only, from this box --
`_tmp/b3a_pinned_child_config.py`, which reuses the repository's own registry client so the
digest recomputation and the "exactly one linux/amd64 child" rule are the ones that produced
the locked claim:

```
pinned index                     sha256:6c2fa84a6b61ccd72899dde4239f8d5717f05f9a8ca6f3cad185fb1a95a94de3
  served Docker-Content-Digest == sha256 over the served bytes   True
the index's single linux/amd64 child   sha256:a461e7f0ce921972028acfbed628d45663d83e67ac1230722c2b34cf72760a0d
  served digest == recomputed                                   True   (7 layers)
child manifest .config.digest  sha256:41f915d3a122bca46b3da83160cd805697b0faa1bf30b0c0de851eb78f992c70
running container's .Image     sha256:41f915d3a122bca46b3da83160cd805697b0faa1bf30b0c0de851eb78f992c70
                               -> equal, on the config layer, with both manifests verified
```

So the answer to Stage 3's verification question, at the layer the evidence can support, is
**yes: the named builder runs the image the pinned index selects for `linux/amd64`**. Two
qualifications belong to that sentence. It is a config-layer identity, not a manifest-layer one
-- a config digest is never compared against the pin by string, and the record keeps the two
layers apart. And that read was made by the reviewer, not by the observation job, so CI's own
record at `c8a5d2b` still said `UNKNOWN`.

At `7d3d6a5` the instrument makes both reads itself, and CI's artifact answers. The pull digest
came back as soon as it was asked of the right object:

```
observed.builder.image_platform            = linux/amd64   (READ)
observed.builder.repo_digests_read.asked_of = sha256:41f915d3a122bca46b3da83160cd805697b0faa1bf30b0c0de851eb78f992c70  (the image, READ)
observed.builder.repo_digests              = moby/buildkit@sha256:6c2fa84a6b61ccd72899dde4239f8d5717f05f9a8ca6f3cad185fb1a95a94de3
comparison.lock_vs_observed.digest_relation = CONFORMING / same_digest
        pinned sha256:6c2fa84a6b61ccd72899…  ==  running sha256:6c2fa84a6b61ccd72899…
pinned_index_resolution.platform_child      = READ; moby/buildkit@sha256:a461e7f0ce921972028acfbed628d45663d83e67ac1230722c2b34cf72760a0d
        .config_digest = sha256:41f915d3a122bca46b3da83160cd805697b0faa1bf30b0c0de851eb78f992c70   (7 layers)
comparison.lock_vs_observed.config_digest_relation = CONFORMING / running_config_is_the_pinned_child_config
        running sha256:41f915d3a122bca46b3da83160cd805697b0faa1bf30b0c0de851eb78f992c70
comparison.lock_vs_observed.layers         = manifest scored, config scored
record.exit = 0; contract_gaps absent; incomplete absent
```

Both layers now agree, and they say different things. On the manifest layer the daemon's own
pull record for the running image is the pinned digest itself -- `same_digest`, the strongest
form of the answer Stage 3 asks for. On the config layer the content-addressed image id the
container reports, `41f915d3a122bca46b3d…`, equals the `config.digest` named inside the
`linux/amd64` child of the pinned index, which is a like-for-like check rather than a string
coincidence and does not depend on the daemon's bookkeeping. One residual is stated rather than
smoothed over: the observation job reads the child manifest from the registry without
recomputing that manifest's own digest, a verification the repository's
`third_party_registry_evidence.py` did perform for this index on 2026-09-22 (§I's provenance
field), and Step 2 can decide whether to move it into the job.

So Stage 3's verification half is answered by CI's record: **yes, the named `docker-container`
builder runs the pinned BuildKit image digest**, with the identity read at two layers.

## G. The scratch, non-publishing build through that builder

Answered affirmatively, and measured at all four heads:

| Fact | Value |
| --- | --- |
| `scratch-build.exit` | `0` at `13930dc`, `8610914`, `c8a5d2b` and `7d3d6a5` |
| Builder the build used (log line 1) | `#0 building with "cap3a-producer-observation" instance using docker-container driver` |
| Frontend resolved by the builder | `docker-image://docker.io/docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32` (the locked coordinate, resolved from the registry) |
| Exported platform manifest | `8610914`: `sha256:ffb1a5fe87bbc9bc768459798aa13e1de2b990d326d1813999feeab10cdec6d6`; `7d3d6a5`: `sha256:ba528f72ae1cb284501507feffa0567c886aade35d0a7bac0481afb558d52222` |
| Exported image config | `8610914`: `sha256:644d39ccb5cf74f2ad20c1a4a804a6432600fa64ce62b69f308e2412ce2d7e5b`; `7d3d6a5`: `sha256:b4bc6b023085c5262cf4967e17228e6d4ae18dd04e105e46d138e85d4df46907` |
| Layer count / diff IDs / history | at `7d3d6a5`: 11 layer digests, 11 diff IDs, 24 history entries, `linux/amd64`, 13 blobs in the archive |
| Same builder, same frontend, different bytes | the two exported manifests differ, which is F-39's subject. The producer behind them is now observed and identical at the two heads whose builder layer read cleanly (`c8a5d2b` and `7d3d6a5`, both `sha256:41f915d3a122bca…`), so a future two-build run can attribute a difference to builds rather than to an unwatched machine |
| Invocation | `docker buildx build --builder "$CAP_OBSERVE_BUILDER" --file backend/Dockerfile --build-arg VERSION=0.0.0-producer-observation --build-arg REVISION=$GITHUB_SHA --provenance=false --sbom=false --metadata-file … --output type=oci,dest=… backend` |

So the question Stage 4 asks -- *can the intended pinned producer build CAP successfully in
GitHub Actions?* -- is **yes for a representative image** (`cap-backend`), on the named
`docker-container` builder, with no registry involved. It is not a claim about the release
path (which still runs `build_release_image.sh` exactly as before) nor about the other four
images, and the OCI export deliberately runs without attestations, so the attestation
descriptors of a *pushed* build remain unmeasured (F-25's pending item).

## H. The three comparisons, kept apart

| Head | `lock_vs_workflow` | `workflow_vs_observed` | `lock_vs_observed` | `producer_alignment` |
| --- | --- | --- | --- | --- |
| `13930dc` | CONFORMING (all four fields equal) | **MISMATCH** -- declared buildx `v0.37.1`, executing `v0.37.0`; builder name and driver equal | **MISMATCH** -- buildx differs; digest `UNKNOWN` (running side unreadable) | MISMATCH |
| `8610914` | CONFORMING | **CONFORMING** -- `v0.37.1` declared and executing; builder name and driver equal | **UNKNOWN** -- buildx equal, digest unreadable | UNKNOWN |
| `f78eb2c` | CONFORMING | **MISMATCH** -- `v0.37.1` declared, `v0.37.0` executing | **MISMATCH on buildx alone** -- both digest relations `CONFORMING` (manifest `same_digest`, config `running_config_is_the_pinned_child_config`) | MISMATCH |
| `c8a5d2b` | CONFORMING | **MISMATCH** -- `v0.37.1` declared, `v0.37.0` executing | **MISMATCH** -- buildx differs, digest `UNKNOWN` (the read asked a container for an image field) | MISMATCH |
| `7d3d6a5` | CONFORMING (four fields equal) | **MISMATCH** -- `v0.37.1` declared, `v0.37.0` executing; builder name and driver equal | **MISMATCH, and now only because of buildx** -- `buildkit_digest` equal on the manifest layer, `buildkit_child_config` equal on the config layer, `pinned_index_resolution` resolved, `buildx_version` different | MISMATCH |

The published legacy booleans are still written beside these
(`comparison.buildx_version.matches`, `comparison.buildkit_image.matches`) for the evidence
`build_release_image.sh` already embeds, and they are not the source of any verdict.

Two things this table demonstrates. First, a *fully conforming* lock/workflow pair was
measured: the repository's two authorities agree, and at `8610914` the executing buildx
agreed with them too, so the observation is not structurally incapable of reaching
CONFORMING. Second, the batch's own rule held: a MISMATCH at `13930dc` did **not** fail the
job, while the unreadable builder layer at both heads **did** (`--self-check`, exit 1, on a
field the observation promised) -- a disagreement is data, a broken instrument is not.

## I. F-39 evidence readiness

The measurement format now has a home for each element §5 of
`docs/quality/cap-f39-reproducibility-measurement-2026-09-21.md` asked for, and §5.1 of
that file maps them field by field. From the CI record: producer identity (§C-§E), driver,
engine version `28.0.4`, runner metadata (`runner_environment github-hosted`, `imageos
ubuntu24`, `imageversion 20260907.300.1`, `runner_arch X64`, `runner_name`, `github_run_id`,
`github_sha`), the frontend directive, the **base index digest, its platform child and that
child's `config.digest` and layer count** (from `pinned_index_resolution.platform_child`, so a
future comparison can say which base layer moved), and from the OCI archive: index composition,
the platform manifest digest, config digest, layer digests and diff IDs, `created`, history
entries. `measurement_status` reads

> `PARTIAL -- one build measured; attributing a digest difference needs two independent
> builds of one commit`

with `builds_compared: 1`. The two-build experiment of §5 step 1 was **not** run in this
batch and **G(i) is not claimed complete**: F-39 remains measured-and-unattributed, and the
approval put that experiment after producer observation as its own step.

## J. Tests

`backend/tests/test_producer_observation_contract.py` (new) plus the existing
`test_build_producer_contract.py`. Coverage of the seven required controls, one test each,
and a field-level control for every new field:

1. lock ≠ workflow → `lock_vs_workflow` MISMATCH, on a throwaway copy of the workflow (the
   real file is never edited to make a test pass), while the other two layers keep their
   own answers;
2. workflow ≠ executing → `workflow_vs_observed` MISMATCH with `lock_vs_workflow` still
   CONFORMING -- F-44 as a field;
3. configured BuildKit digest ≠ running digest → MISMATCH with
   `relation = running_digest_is_not_named_by_the_pinned_index`, asserted *beside* the fact
   that the tag-level view of those bytes still looks like the pin; and its mirror case, a
   platform child of the resolved index → CONFORMING with `CHILD != pinned digest`;
4. driver ≠ `docker-container` → the driver field reads `different` and the container layer
   reports `NOT_APPLICABLE` with its reason, not a lost read;
5. builder unreadable → `status = ERROR`, the CLI's own sentence carried, both
   runner-dependent layers `UNKNOWN`, `contract_gaps` naming them, `--self-check` exit 1 and
   exit 0 **without** it;
6. action-installed ≠ executing → both versions kept, `resolves_to`/`executing` naming the
   one that ran, and the comparison computed from that one;
7. all aligned → three CONFORMING and no `contract_gaps`.

Extras beyond the seven: an all-reads-fail runner (`UNKNOWN` everywhere the runner is needed,
never CONFORMING); `_ref_parts` on tag+digest / digest-only / registry-port references;
`parse_version_line` on the two real shapes (with and without an install path, so the module
name cannot be filed as a path); `${{ … }}` declarations; the CI-measured `inspect` text;
the container-join test that replaced a falsified naming assertion; the OCI-archive test that
reads twelve F-39 fields out of a hand-built tar and still says PARTIAL; the non-publishing
and release-path-untouched fences; and build-mode's additive legacy shape.

8. **The two digest layers** (`7d3d6a5`), one test per field added: the pull digest is asked of
   the *image* the container names and never of the container (the argv itself is asserted, so
   the shape CI falsified cannot come back); a lost platform read leaves the child
   `NOT_REQUESTED` rather than guessing a counterpart, and does not erase the independent
   digest read; the pinned child is read down to its own `config.digest` and scored `CONFORMING`
   only against a running config that equals it; a different child config is `MISMATCH` on that
   layer; a child the registry refused to serve is a `contract_gaps` entry and never a match; an
   index naming two children for one platform is `AMBIGUOUS` and not compared; a platform the
   index does not name is `NOT_NAMED`; and the layering fence -- manifest `UNKNOWN` beside
   config `CONFORMING` leaves `buildkit_digest` unscored and `required` unmoved.

Controls over the controls (a test that cannot fail is not a control):

| Mutation of the recorder | Result |
| --- | --- |
| `_status_of`: nothing-readable ⇒ `CONFORMING` (the null-as-match rule inverted) | the all-reads-fail test fails |
| `contract_gaps`: always return `[]` | control 5 fails |
| `config_digest_relation`: always `CONFORMING` | the differing-child-config control fails |
| `_pinned_platform_child`: compare against the first child even when several are named | the ambiguity control fails |
| the pull-digest read moved back onto the container object | the object-of-the-read test fails |
| the child read's `ERROR` no longer counted as a gap | the lost-child-read control fails |
| restored | 54 passed (35 observation + 19 existing producer tests), ruff clean; recorder verified restored byte-for-byte |

Suite runs before each push, at `7d3d6a5`. The Stage 7 minimum set --
`test_producer_observation_contract`, `test_build_producer_contract`,
`test_release_build_script`, `test_release_image_completeness`,
`test_release_publication_gate`, `test_release_diff_classifier`,
`test_certification_workflow_contract` -- **254 passed, 4 skipped**. The wider 19-file
release-, certification-, registry- and workflow-contract set (the same files plus the phase
certification suites and `test_third_party_registry_evidence`) --
**380 passed, 51 skipped** in 241 s. Those 51 skips are the cluster- and `GH_TOKEN`-gated
checks that CI runs with a kind cluster and a token, and none of them belongs to this batch:
the only test files Batch 3A touches are the two producer suites, and between them they report
zero skips. No test was deleted, narrowed or marked skip to get there, and the F-33/F-42 gate
protections in `test_release_publication_gate.py` and
`test_certification_workflow_contract.py` passed unchanged.

A separate class of defect was caught locally, before any of this was pushed, by running
the recorder against the real docker CLI on this box: a version line without a path was
being parsed into a **fabricated** `path` (the Go module name filed as a filesystem path),
and `RepoDigests`-style digest-only references were being parsed with `tag` equal to the
repository name. A third bug -- `configured.workflow` exposing `digest` where the comparison
read `buildkit_digest` -- made `lock_vs_workflow` report UNKNOWN for a file that agreed
perfectly with the lock, and it was the real-CLI run that surfaced it.

## K. Classifier

`scripts/release/classify_diff.py` (unmodified, fail-closed) at each step:

| Range | inheritance | runtime_affecting | categories touched |
| --- | --- | --- | --- |
| `f23d1fd → 313cf63` | INHERITED | false | `ci_workflow`, `test_harness`, `certification_generator` |
| `313cf63 → 13930dc` | INHERITED | false | `docs` ×4 |
| `13930dc → 8610914` | INHERITED | false | `certification_generator`, `test_harness` |
| `8610914 → c8a5d2b` | INHERITED | false | `certification_generator`, `test_harness` |
| `c8a5d2b → 7d3d6a5` (the measured head) | INHERITED | false | `certification_generator`, `test_harness` |
| `7d3d6a5 →` the docs-only heads after it | INHERITED | false | `docs` |
| `f23d1fd → HEAD` (cumulative from the docs tip) | INHERITED | false | the union above |
| `257ba18 → HEAD` (cumulative from the certified candidate) | INHERITED | false | plus the `docs` of the earlier reconciliation |

All five cumulative and per-step ranges were re-run at the pushed docs tip, not only at the
code head: the classifier is unmodified and fail-closed, and nothing in the batch touches a
path that could change an image's bytes.

No `deployment/`, no `Dockerfile`, no product runtime, no release-build behaviour: the STOP
condition never applied, and Batch 3A's evidence inherits from `257ba18` rather than
demanding a re-certification round.

## L. Remote CI

| CI head | run | result | producer artifact |
| --- | --- | --- | --- |
| `13930dc` | `35826852945` (push) | 9 jobs success, `producer-observation` **failure** | `cap-3a-producer-observation`, artifact `10735771235` (17,250 B): `scratch-build.exit = 0`, `record.exit = 1`, `contract_gaps = [observed.executing_buildx.path, observed.docker_cli_plugin.resolves_to, observed.builder.error]`, `incomplete = [builder]` |
| `8610914` | `35828323456` (push) | `producer-observation` **failure** after the first fix; other jobs success | artifact `10735957267` (17,245 B): `scratch-build.exit = 0`, `record.exit = 1`, `contract_gaps = [observed.builder.error]`, executing buildx **v0.37.1** identified, `lock_vs_workflow`/`workflow_vs_observed` **CONFORMING**, `lock_vs_observed` UNKNOWN |
| `c8a5d2b` | `35830268377` (push) | run **success** -- 11/11 jobs, `producer-observation` included (container discovered by the hostname join, `record.exit = 0`, no gaps) | artifact `10736258866` (17,590 B): `scratch-build.exit = 0`, `container = buildx_buildkit_cap3a-producer-observation0`, `RepoDigests = []`, `digest_relation = UNKNOWN`, executing buildx **v0.37.0** vs declared `v0.37.1` |
| `7d3d6a5` | `35834970797` (push) | run **success** -- 11 jobs, `producer-observation` **success** with no contract gaps and all 11 jobs successful | artifact `10739115755` (17,855 B): `scratch-build.exit = 0`, `record.exit = 0`, `image_platform = linux/amd64`, `RepoDigests = [moby/buildkit@sha256:6c2fa84a…]`, `digest_relation = CONFORMING / same_digest`, `config_digest_relation = CONFORMING`, `platform_child` manifest `sha256:a461e7f0…` with `config.digest` `sha256:41f915d3…`; executing buildx **v0.37.0** vs declared `v0.37.1` |
| `f78eb2c` | `35840512203` (push) | run **success** -- `producer-observation` green on a docs-only head and a different runner, reproducing both digest relations | artifact `10741143423` (17,448 B): `scratch-build.exit = 0`, `record.exit = 0`, `RepoDigests = [moby/buildkit@sha256:6c2fa84a…]`, `digest_relation = CONFORMING / same_digest`, `config_digest_relation = CONFORMING`, executing buildx **v0.37.0** vs declared `v0.37.1` |

The first two failures were the instrument refusing to over-claim, not the build or the
comparisons: the scratch build succeeded in each, and each record carries the reason the
observation layer is missing. The third run answered every field except the one it could not
ask about, and the fourth closed that: its record reads the pull digest from the image, reads
the pinned index's child down to its config digest, scores both layers `CONFORMING`, and still
reports `lock_vs_observed: MISMATCH` for the single remaining reason -- `v0.37.0` executing
under a `v0.37.1` declaration. Those statuses are the measured state of the producer, not a
verdict about it; `--self-check` gates the instrument, and nothing on the release path reads
the answer. No certification workflow was dispatched, no tag was created, and no publication
was performed in any of the four runs.

The docs-only heads after `7d3d6a5` add no measurement, and their CI behaved the way a repository
with `cancel-in-progress: true` on its own concurrency group (`.github/workflows/ci.yml` lines
13-15) makes it behave: run `35839046835` at `c06dfe0` ended **cancelled**, `producer-observation`
already **success** and `backend` stopped mid-flight, because the next docs push superseded it. A
cancelled job says nothing about the code under test -- it was stopped, not failed.

The next head's run does say something, and it is the most useful sentence in this report. CI
`35840512203` at `f78eb2c` -- a docs-only commit, a different runner, 45 minutes after the record
above -- completed **success** with `producer-observation` green, `record.exit = 0`,
`scratch-build.exit = 0`, no `contract_gaps` and no `incomplete`, and it reproduced the digest
conclusion exactly: `digest_relation` **CONFORMING / same_digest** against
`moby/buildkit@sha256:6c2fa84a6b61ccd72899dde4239f8d5717f05f9a8ca6f3cad185fb1a95a94de3`, `config_digest_relation` **CONFORMING** for
`41f915d3a122bca46b3d…` against the pinned child `sha256:a461e7f0ce921…`, and
`workflow_vs_observed` **MISMATCH** for the one reason it always was -- executing buildx
`v0.37.0` under a `v0.37.1` declaration. So the instrument's answer is not a one-runner artefact:
what it reported about the BuildKit replicated, and what it reported about F-44 replicated. No
tag, no publication and no strict certification dispatch happened at any of these heads (§M).

## M. Sealed-release integrity (re-read after the push)

Read-only GETs through `_tmp/b2/sealed_release_integrity.py`, output kept at
`_tmp/b3a_sealed_integrity.txt` and re-run after the final push (`7d3d6a5`) to
`_tmp/b3a_sealed_integrity_after_7d3d6a5.txt` -- same verdict, same digests:

* `v1.0.6-rc1` tag object `d0c7c7dc14cb…`, peels to `4d8f9c72b72d…`, tagged
  `2026-09-21T02:18:13Z` → **unchanged**;
* **no tag created on or after the Batch 2 window**, so none by Batch 3A either ("tags
  created on/after Batch 2: NONE");
* Release `v1.0.6-rc1` (id 392662225): `updated_at == published_at == 2026-09-21T02:38:59Z`,
  all five assets created 2026-09-21 and none updated or replaced since;
* all five `1.0.6-rc1` image tags still resolve on ghcr and each re-read **by digest**
  returns the same digest (`cap-backend sha256:a733b90c…`, `cap-frontend sha256:e1b1889a…`,
  `cap-egress-proxy sha256:8ab8c234…`, `cap-sandbox-http sha256:36bb2f79…`,
  `cap-sandbox-browser sha256:b3696188…`);
* newest non-prerelease release is still **v1.0.5** → the RC was never promoted.

Verdict of that read: `SEALED RELEASE INTACT`.

## N. Recommendation for Step 2

Batch 3A was approved for Observation Step 1 only, and that is where this stops. What the
measurements now support, in the order they should be decided:

1. **A conforming producer path is demonstrated on the BuildKit side, and that is what M2
   asked for.** At `7d3d6a5` CI's own artifact reads the running builder image's digest and
   the pinned index's child config digest and finds both equal to the pin (§F), while the
   scratch build succeeds on the named builder (§G). The one layer that does not conform is
   the buildx CLI: `v0.37.0` executing against a `v0.37.1` declaration, on the runner's own
   system plugin path, in three of four cycles. Step 2 is therefore a decision about a named,
   specific gap -- how to make the *CLI* the pinned one -- and not about whether the
   instrument can ever observe conformance.
2. **If Option A becomes the contract, the switch has to be explicit and per-build, not
   inherited from a runner.** F-44's own shape changed between two CI runs of one workflow
   (v0.37.0 then v0.37.1 executing, both resolved to the same system plugin path), so
   "the pin was honoured" is not a property this repository controls unless the build names
   `--builder <created-by-name, image-pinned-by-digest>` and the record joins the running
   container back to that digest.
3. **Blocking on mismatch is a separate decision, and it should be made with the
   false-mismatch risk visible.** Two of the three layers at `13930dc` were MISMATCH/UNKNOWN
   because of the recorder's own bugs, not the machine's behaviour. A gate that failed on
   `producer_alignment.verdict` that day would have blocked a good build for a bad
   instrument -- which is why `--self-check` gates the instrument and Step 2 must decide the
   mismatch policy only after the instrument has been seen CONFORMING on a real runner.
4. **Sequencing.** The F-39 two-build experiment should follow Step 2, not precede it: the
   fields are ready (§I), and comparing two builds produced by an unobserved producer would
   attribute divergence to nothing.
5. **B3 stays deferred, B4 stays out of scope, F-46 stays an open policy question** -- the
   socket disclosure is still pinned by the Linux certification rather than isolated, and
   nothing in Batch 3A changed that.

## O. FINAL VERDICT

# BATCH 3A OBSERVATION COMPLETE — STEP 2 READY FOR DECISION

Stated plainly, with what is settled and what is not:

* Settled: the evidence schema keeps `configured.lock`, `configured.workflow` and `observed`
  apart and scores them three ways (§B, §H); the executing buildx binary is identified by a
  labelled route even where the CLI prints no path (§C, §D); the named non-publishing builder
  exists, is inspected, and **builds a representative CAP image successfully in Actions**
  (§E, §G); the F-39 field list has a home and stays PARTIAL (§I); the tests, controls and
  classifier are in place and inherited (§J, §K); the sealed release is untouched (§M).
* **Settled by the fourth run, at the head it was added to:** the builder's running image
  identity is verified at both digest layers from CI's own record -- the pull digest equals
  the pinned index digest (`same_digest`) and the running config digest equals the pinned
  child's `config.digest` (§F) -- so Stage 3's verification half is met and `digest_relation`
  is no longer `UNKNOWN` for a reason the instrument could not have answered. A fifth run at a
  docs-only head (`f78eb2c`, a different runner) reproduced both relations as `CONFORMING` over
  the same digests, and reproduced the single buildx disagreement unchanged (§L, §H) -- which is
  what separates an observation from one lucky machine.
* **Open, and this is the decision Step 2 must make rather than a defect of the observation:**
  F-44's buildx disagreement. `v0.37.0` executed against a `v0.37.1` declaration at three of
  four heads, `v0.37.1` at one, always from `/usr/libexec/docker/cli-plugins/docker-buildx`,
  and no `--builder` is named by the release path. A conforming *BuildKit* is reachable; a
  conforming *CLI* is not yet arranged, because the pinned `buildx-version:` is a hint to an
  action that may or may not be the binary the CLI dispatches.
* Deliberately not claimed: no two-build reproducibility measurement (F-39 stays PARTIAL, its
  experiment is a later step), no publication, no switch of the release build path, no gate
  that blocks on a producer mismatch, and no reading of `producer_alignment.verdict` as a
  release decision.
* What Step 2 has to decide, with the evidence in §N in front of it: whether to name the
  builder and the CLI explicitly per build, what to do about the version the runner supplies,
  and when a mismatch becomes publication-blocking.
* Held to the approval: no release tag, no publication, no strict certification dispatch, no
  sealed-release mutation, no change to the release build path, and no gate that blocks on
  a producer mismatch yet (§N is a recommendation, not an action).
