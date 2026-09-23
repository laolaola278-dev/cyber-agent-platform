# CAP POST-RC HARDENING -- BATCH 3 PRODUCER AUTHORITY REPORT

Two-stage execution of the approved Step 2. **A2.1** (make producer execution deterministic in
non-publishing CI) is implemented, tested and observed here. **A2.2** (switch the release path,
make a mismatch blocking, recertify) is **not executed**: it is authorised only after A2.1's
remote evidence passes, and §H is where that gate is decided.

| | |
| --- | --- |
| Approved contract | Option A -- published images must ultimately be built by an explicitly selected, pinned and observed producer |
| Approved now | A2.1 only. The release build path is not switched; a producer mismatch does not yet block publication |
| Base / docs tip before this batch | `a6a4a9f43958f4631d3462b3ae5955a4eec60148` (Batch 3A's verdict: `BATCH 3A OBSERVATION COMPLETE -- STEP 2 READY FOR DECISION`) |
| Verdict | §H |
| Sealed release | untouched -- §R |

## A. A2.1 implementation

Three files carry the change, and none of them is a release file:

* `scripts/release/controlled_buildx.json` (**new**) -- the repository's declaration of the one
  buildx executable A2.1 is allowed to run: version, the git commit its release tag points at,
  the download URL, and the sha256 GitHub records for that asset.
* `scripts/release/record_build_producer.py` -- the instrument. It now observes that executable,
  hashes it, reads back the argv the build ran under, routes its own builder and registry reads
  through the pinned binary, resolves each image's base bindings, and scores a five-image set.
* `.github/workflows/ci.yml` → `producer-observation` -- installs, verifies, selects and builds
  with it, for all five shipped images, publishing nothing.

What A2.1 does **not** touch: `release.yml`, `scripts/release/build_release_image.sh`, any
Dockerfile, anything under `deployment/`, and any product runtime. `test_the_release_build_path_is_still_not_switched`
is the fence that keeps saying so, and §F records the classifier's answer.

## B. Controlled buildx identity

Batch 3A established the problem by measurement: five heads, the same job text, executing
buildx versions `v0.37.0 / v0.37.1 / v0.37.0`, and `docker buildx version` printing no install
path at all. A2.1 had to find out whether the action exposes something stable, because
"install our own binary" is the fallback, not the first choice.

It does not. Read from the pinned action at `8d2750c68a42422c14e847fe6c8ac0403b4cbd6f`
(v3.12.0):

* `action.yml` declares outputs `name`, `driver`, `platforms`, `nodes`, `endpoint`, `status`,
  `flags`. **Every one describes the builder it created.** There is no output for the buildx
  executable.
* `src/main.ts` installs buildx through `@docker/actions-toolkit`:
  `BuildxInstall.download(version)` → `Cache.save(...)`, then `installStandalone(toolPath)` or
  `installPlugin(toolPath)`. `installPlugin` copies to `${DOCKER_CONFIG:-~/.docker}/cli-plugins/docker-buildx`
  and returns the path to the caller, which writes it to the **job log** (`core.info(...)`).
  `installStandalone` copies to a `buildx-bin-standalone` directory and `core.addPath`s it.
  Neither path reaches the workflow as data.

So the fallback is the route, and it is taken literally: download from the pinned URL to a path
this repository names in the workflow's own `env:` block, verify the bytes, execute that path.

| pin | value | where it comes from |
| --- | --- | --- |
| version | `v0.37.1` | agrees with `deployment/third-party-images.json`'s `buildx` entry, and the recorder scores that agreement |
| release tag object | `363b7b083ea1a88098198d681f9d5054f1ca4629` | `GET /repos/docker/buildx/git/refs/tags/v0.37.1` |
| expected git commit | `0b265a9f62db554fa9aba6dd19e1bd5704bc7d8a` | `GET /repos/docker/buildx/git/tags/363b7b08…` -- the commit the annotated tag points at. This is also what CI's `v0.37.1` line printed at Batch 3A's `8610914`, so the derivation and the measurement agree without either one being the source of the other |
| download URL | `https://github.com/docker/buildx/releases/download/v0.37.1/buildx-v0.37.1.linux-amd64` | the release asset's `browser_download_url` |
| expected sha256 | `sha256:9447199cdb435f25880548343c128a4b6650e8891ee598905d8d29d39a8e359b` | GitHub's recorded `digest` for asset `557511570` (`buildx-v0.37.1.linux-amd64`, 65,798,306 bytes), read 2026-09-23 |
| install path | `/tmp/cap-a21-controlled-buildx/buildx` | declared twice on purpose -- as the pin's `install.path` and as `CAP_OBSERVE_BUILDX_PATH` in the job's `env:` -- and the two are compared against each other and against the binary's own answer |

Two decisions inside that table are load-bearing, so they are stated rather than implied:

1. **The checksum is pinned, not fetched.** GitHub's asset digest is the natural value, but a
   job that asks the same server what the bytes should be passes on whatever that server serves
   next. The repository carries the number; the job compares the file against it.
2. **The path is a literal in the YAML.** `${{ runner.temp }}/…` would have been tidier and
   would have made `configured.workflow` a claim about the runner instead of the repository -- the exact confusion Batch 3A's `expressions` guard exists to refuse. `/tmp/…` is
   job-controlled, absolute, and comparable.

**And one assumption in the first implementation was wrong, in the direction that matters.** The
design said: invoke the pinned binary by absolute path, and let its version line identify it,
because buildx prints its own path there -- a shape `parse_version_line` has parsed since Batch
3A. CI run `35866400091` answered differently:

```
$ /tmp/cap-a21-controlled-buildx/buildx version
github.com/docker/buildx v0.37.1 0b265a9f62db554fa9aba6dd19e1bd5704bc7d8a
```

no path at all. `workflow_vs_observed` had been made to *require* the reported path, so five
records that agreed on version, commit, digest and invocation all came back `UNKNOWN` and the job
went red. The stubbed suite had been green throughout, because the fixture printed the path the
recorder had just invoked -- it reproduced the assumption instead of the machine. That is the
failure mode this repo keeps paying for, in the opposite direction from the usual one: not a test
too weak to fail, but a fixture that made a wrong belief look tested.

The repair changes what the record claims, not what it prints. Path identity is carried by the
two reads that always answer:

* `controlled_buildx_integrity` -- the sha256 of the file at the declared path, compared against
  the repository's pinned digest. This identifies *the bytes at that path*, which is what a
  printed path was ever supposed to establish, and does so without trusting the tool to volunteer
  it.
* `build_invoked_controlled_executable` -- the declared path against `argv[0]` of the build, read
  back from the file the build step wrote. This establishes *that file built these images*.

`controlled_buildx_path` stays in the record and is compared whenever the binary does report a
path -- where it reports one and it differs, `workflow_vs_observed` reads MISMATCH, through a
`blocking` field list distinct from `required`: something may be unobservable without being
ignorable. Both arms are asserted, because "optional" degrades into "never checked" the moment it
is not tested in both directions.

The runner's plugin is not deleted, overwritten, or ignored: the job refuses to install into a
CLI-plugin path, `observed.docker_cli_plugin` still enumerates all four plugin directories plus
`which docker-buildx`, and `docker buildx version` is still executed and still recorded as
`cli_plugin_buildx_version` with `"scored": false`. F-44's disagreement is expected to remain
visible at every head, and a green A2.1 that stopped measuring it would be worth less, not more.

## C. Named builder identity

`buildx create --name "$CAP_OBSERVE_BUILDER" --driver docker-container --driver-opt
"image=$CAP_OBSERVE_BUILDKIT" --use` runs under the controlled executable, and the recorder
reads the builder back by name (`inspect --builder=…`), never from the default.
`configured.workflow` takes the builder name, the driver and the BuildKit reference out of the
job's `env:` literals; `observed.builder` is the read-back; `workflow_vs_observed` compares them
field by field (`builder_name`, `driver`). Batch 3A's measurements of what `buildx inspect`
actually prints on a runner (`Buildkit version:`, no per-node `Image:`, the declared reference
inside `Driver Options:`) are unchanged and still the reason the declared reference is kept as a
separate layer from the running bytes.

## D. BuildKit digest verification

Batch 3A's layering is kept, and one rule is strengthened. The seven values A2.1 is asked to
verify independently are all in the record, and no comparison crosses layers:

| layer | where it is read | what it is compared against |
| --- | --- | --- |
| configured lock digest | `configured.lock.buildkit_digest` (the lock) | -- |
| configured workflow digest | `configured.workflow.buildkit_digest`, plus the action's own `driver-opts` digest | the lock, in `lock_vs_workflow` |
| running image `RepoDigests` | `docker image inspect {{json .RepoDigests}}` of the image id the container reports | the pinned index digest and the index's children, in `digest_relation` |
| pinned index digest | the registry (`buildx imagetools inspect --raw`) | itself, as the parent of the child list |
| pinned platform child digest | the same index, filtered to the platform the **running image** reports | `platform_child.manifest_digest` |
| running config digest | the container's `.Image` | -- |
| pinned child config digest | one registry level down: the child manifest's `config.digest` | the running config digest, in `config_digest_relation` |

`lock_vs_observed.required` now names **both** digest layers
(`buildkit_digest`, `buildkit_child_config`) where Batch 3A required only the manifest one. That
is the stricter direction, and it is not the substitution 3A refused: each layer is scored
against its own pinned-side value, so a conforming config digest still cannot answer the
manifest question, and the test that pinned that refusal now asserts the new list and the
`UNKNOWN` verdict for a record whose manifest layer was never read.

One deliberate refusal, recorded here because it is the kind of shortcut this batch exists to
avoid: `running == platform_child_manifest` is **not** made a required field. CI measured
`digest_relation = same_digest` at the conforming heads -- a daemon that pulled
`name:tag@<index-digest>` records the *index* digest in `RepoDigests`, not the child's -- so a
rule demanding equality with the child would call the conforming case a mismatch. Membership of
the index's child set, plus config-layer equality, is what the evidence can carry.

## E. Five-image observation

`cap-backend`, `cap-frontend`, `cap-sandbox-http`, `cap-sandbox-browser`, `cap-egress-proxy` -- the same five the release publishes, staged the same way the release stages them
(`backend/docker/prepare_sandbox_context.sh`, the same script `build_release_image.sh` calls),
each built with `--builder "$CAP_OBSERVE_BUILDER"`, `--provenance=false --sbom=false`, and
`--output type=oci,dest=…` so the F-39 fields come out of the archive rather than out of a
registry. No push, no login, no image tag, no registry credential, and no `permissions` block.

**The dependency.** `cap-sandbox-browser`'s `Dockerfile` takes its base as
`ARG SANDBOX_HTTP_BASE` with **no default**, and the release binds it to
`ghcr.io/<owner>/cap-sandbox-http@<digest it just pushed>`. A non-publishing round has no pushed
digest, and a `docker-container` builder cannot see the host docker store -- which is precisely
why CI's existing dry build of this image goes through `docker build --local-docker`, and why
Batch 3A could say the local dry build "is not the producer and asking its container for a digest
reports a true answer to a wrong question".

A2.1 closes that with a named build context over the round's own archive:

```
--build-context cap-sandbox-http=oci-layout://$GITHUB_WORKSPACE/…/http-layout@sha256:<digest>
--build-arg SANDBOX_HTTP_BASE=cap-sandbox-http
```

where `http-layout` is `cap-sandbox-http.oci.tar` unpacked, and the digest is the single
non-attestation manifest in that layout's `index.json`. The scheme and its `@digest` form are
buildx v0.37.1's own (`util/ocilayout/parse.go`; `docs/reference/buildx_build.md`: *"the context
can be accessed when `FROM name` … is used"*, with *"local OCI layout compliant directory"* as an
allowed value). Nothing is pushed, nothing is pulled, and the fifth image's base is bound to
bytes this round produced. If the digest cannot be read the build is **refused**, not run
against a name.

The recorder reads that binding out of the build's own recorded argv rather than being told it
again, and `set_acceptance` refuses a set in which any image's base is not named by digest -- a pinned producer on an unpinned base has still built something nobody can re-derive.

Per image the record carries: the executable that built it (the path invoked, plus whether the
binary corroborated it -- see §B), its version and commit, and the integrity comparison; the
builder name, driver, node and running BuildKit manifest/config digests with their two relations;
the frontend that parsed the build, stated rather than left blank; the base reference(s) with
binding kind and digest; the OCI archive's platform manifest, config digest, layer digests and
`diff_ids`; and the four named comparisons with `producer_alignment`. §G is what CI answered for
each of the five.

## F. A2.1 classifier

Run per commit (`classify_diff.py <parent> <commit>`) and cumulatively from the docs tip, at
the pushed head:

| commit | files | categories | verdict |
| --- | --- | --- | --- |
| `1b2af34` pin the controlled buildx | 1 | certification_generator | INHERITED, runtime_affecting=false |
| `3758076` five images under the pinned producer | 4 | certification_generator, ci_workflow, test_harness ×2 | INHERITED, runtime_affecting=false |
| `4d2d7d7` base bindings from the build's argv | 3 | certification_generator, ci_workflow, test_harness | INHERITED, runtime_affecting=false |
| `e18fb32` stated frontend, empty-set refusal | 2 | certification_generator, test_harness | INHERITED, runtime_affecting=false |
| `ecd7ac4` derived version, guard escape closed | 2 | ci_workflow, test_harness | INHERITED, runtime_affecting=false |
| **cumulative `a6a4a9f..ecd7ac4`** | **6** | -- | **INHERITED, runtime_affecting=false** |

The STOP condition never fired: no file in the range is a Dockerfile, under `deployment/`, or in
the product runtime, and `release.yml` and `build_release_image.sh` are not touched at all. The
four categories the batch expected -- `ci_workflow`, `certification_generator`, `test_harness`,
and (for the later docs commit) `docs` -- are the only ones that appear.

That reading is *about paths*, and it is worth stating what it does not say: installing and
verifying a buildx binary is a change to how a build is produced in CI even where the classifier
cannot see it, which is exactly why A2.2 is treated as producer-affecting by policy rather than
by path. A2.1 stays inside the observation job, so the inheritance claim here is the narrow one:
nothing shipped changes.

## G. A2.1 CI evidence

### Cycle 1 -- head `ecd7ac4`, run `35866400091`, job `107198977718`

The job went red, and the reason is the only thing that did. Every producer layer the batch
exists to establish measured conforming; the instrument demanded one more field than a runner
can give.

Install and verification, from the job log:

```
/tmp/cap-a21-controlled-buildx/buildx: OK                 ← sha256sum --check --strict
github.com/docker/buildx v0.37.1 0b265a9f62db554fa9aba6dd19e1bd5704bc7d8a      ← the pinned path
github.com/docker/buildx v0.37.1 0b265a9f…               ← `docker buildx version`, this runner
```

Five builds, one runner (`GitHub Actions 1000002868`), one round (run `35866400091`,
revision `ecd7ac4b3efbd3c6538932f7fc6752bfa206422c`), each recording its own argv before
running it:

| image | build exit | `argv[0]` | archive config digest | layers / diff_ids | frontend |
| --- | --- | --- | --- | --- | --- |
| `cap-backend` | 0 | `/tmp/cap-a21-controlled-buildx/buildx` | `sha256:2b719d779d6…` | 11 / 11 | pinned by directive |
| `cap-frontend` | 0 | same | `sha256:f80204d1ca7…` | 12 / 12 | pinned by directive |
| `cap-sandbox-http` | 0 | same | `sha256:aa9fd80131c…` | 13 / 13 | built-in of the running BuildKit |
| `cap-sandbox-browser` | 0 | same | `sha256:893365cb743…` | 15 / 15 | built-in of the running BuildKit |
| `cap-egress-proxy` | 0 | same | `sha256:d186b7cb8c1…` | 8 / 8 | built-in of the running BuildKit |

The fifth image's dependency is checkable rather than asserted: the base the browser was built on
resolved to `cap-sandbox-http` through the round's own OCI layout at
`sha256:c1a512f5422b90e51c743ea9d4962109fa28b5b129d960846230953710240f74`, which is
*byte-identical* to the manifest digest `cap-sandbox-http` produced in the same run. No registry
and no host docker store appears anywhere in that path, and the build succeeded -- which is the
question Batch 3A left open about the browser image.

Builder and BuildKit layers, identical across all five records:

```
observed.builder            cap3a-producer-observation / docker-container / BuildKit v0.33.0
                            platform linux/amd64 (read from the running image)
running manifest digest     sha256:6c2fa84a6b61ccd72899dde4239f8d5717f05f9a8ca6f3cad185fb1a95a94de3
running config digest       41f915d3a122bca46b3da83160cd805697b0faa1bf30b0c0de851eb78f992c70
digest_relation             CONFORMING / same_digest
config_digest_relation      CONFORMING / running_config_is_the_pinned_child_config
comparison.lock_vs_workflow    CONFORMING
comparison.controlled_pin_vs_lock  CONFORMING
comparison.lock_vs_observed    CONFORMING      (layers: manifest scored, config scored)
comparison.workflow_vs_observed  UNKNOWN       ← the defect
observed.controlled_buildx     v0.37.1 / 0b265a9f… / integrity CONFORMING / status READ
observed.buildx_reads_via      ['/tmp/cap-a21-controlled-buildx/buildx']
```

So the pinned executable *was* the producer, hashed, versioned, committed, invoked, and
observed with the pinned BuildKit behind it -- and `workflow_vs_observed` still refused to say
so, for this single reason, repeated as ten set problems (two per image: one `UNKNOWN` layer,
one contract gap):

```
contract_gaps = ['observed.controlled_buildx.path_reported_by_binary']
A2.1 acceptance: BATCH 3 A2.1 PRODUCER NOT CONFORMING   authorizes_a2_2: False
```

That gap is §B's corrected assumption: a required field no runner answers. It is recorded here
as the reason cycle 1 could not be accepted, and `b98eb3b` is the head that fixes it -- the
instrument changed, and no comparison rule was softened: the printed path is still compared and
still able to force MISMATCH when a binary does print one (§B).

### Cycle 2 -- head `b98eb3b`, run `35868200920`, artifact `10754130501` (119,238 B, 37 members)

Same job, one commit apart: the required field that no runner can answer is gone, and the
acceptance rule is now driven by what the evidence can establish.

```
A2.1 acceptance: BATCH 3 A2.1 PRODUCER CONFORMING   authorizes_a2_2: True
rounds: [{"run_id": "35868200920", "source_revision": "b98eb3b9…d61d27"}]
problems: []        set.exit 0        every *.build.exit 0        every *.record.exit 0
```

Every image, on one runner (`GitHub Actions 1000002890`), built by one executable, with all four
named comparisons and `producer_alignment` reading CONFORMING and no contract gap:

| image | `argv[0]` | buildx / commit | integrity | printed path | image manifest | layers | build | comparisons |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `cap-backend` | `/tmp/cap-a21-controlled-buildx/buildx` | v0.37.1 / `0b265a9f` | CONFORMING | none → unscored | `sha256:a58f362e64f…` | 11 | 0 | all CONFORMING |
| `cap-frontend` | same | same | CONFORMING | same | `sha256:1fdd644c9ed…` | 12 | 0 | all CONFORMING |
| `cap-sandbox-http` | same | same | CONFORMING | same | `sha256:23ac768c41c…` | 13 | 0 | all CONFORMING |
| `cap-sandbox-browser` | same | same | CONFORMING | same | `sha256:89adf4c5c40…` | 15 | 0 | all CONFORMING |
| `cap-egress-proxy` | same | same | CONFORMING | same | `sha256:3ec6c1e2a2b…` | 8 | 0 | all CONFORMING |

The path field behaved as §B describes: `controlled_buildx_path` carries
`"status": "NOT_REPORTED…"`, `relation: null`, `scored: false`, and the comparison is CONFORMING
because identity is carried by the digest of the file (`sha256:9447199c…`, matching the pin) and
by `argv[0]` of each build. Every buildx read -- `inspect`, `ls`, `imagetools` -- went through
the same path (`observed.buildx_reads_via`).

**The runner disagreed, and it no longer matters.** At this head the GitHub runner's own plugin
answered `docker buildx version` with **v0.37.0** -- the F-44 value, back again on a different
runner -- while `configured.lock`, `configured.workflow`, `configured.controlled`, the installed
bytes and the executable that built all five images say v0.37.1:

```
comparison.buildx_binaries.cli_plugin_version   v0.37.0     (recorded, `"scored": false`)
observed.controlled_buildx.version / .commit    v0.37.1 / 0b265a9f62db554fa9aba6dd19e1bd5704bc7d8a
observed.controlled_buildx.integrity            CONFORMING  (sha256:9447199c… == the pin)
comparison.workflow_vs_observed                 CONFORMING
```

That pairing is the batch's whole claim in six lines: the producer question no longer has a
runner-dependent answer, and the runner's answer is still on the page to prove the claim is about
selection rather than about luck.

Builder and BuildKit layers, identical across all five records:

```
observed.builder          cap3a-producer-observation / docker-container / BuildKit v0.33.0
                          platform linux/amd64 (read from the running image, not from runner env)
running manifest digest   sha256:6c2fa84a6b61ccd72899dde4239f8d5717f05f9a8ca6f3cad185fb1a95a94de3
running config digest     41f915d3a122bca46b3da83160cd805697b0faa1bf30b0c0de851eb78f992c70
digest_relation           CONFORMING / same_digest
config_digest_relation    CONFORMING / running_config_is_the_pinned_child_config
lock_vs_observed.layers   manifest scored / config scored
```

And the fifth image's base binds to this round by digest, checked across two records rather than
asserted in one: the browser's base is `cap-sandbox-http` at
`sha256:23ac768c41cbcb29c87bf42b612f4aa6c2b0de83b1cad904e6c79d41a7abb384`, which equals the
manifest digest `cap-sandbox-http` produced in the same run (§E's mechanism, with no registry
and no host store in the path).

### Cycle 3 -- live negative control (scratch branch, never merged)

Head `60d8e074182b40b56eefb6db7bcf180cc53402b8` on branch `release/a21-negative-control`, run
`35869468565`, artifact `10754322851`. One line of `ci.yml` changed: `cap-backend`'s build was
sent back through `docker buildx build` while everything else -- the install, the checksum, the
builder, the other four images -- stayed exactly as in cycle 2. The branch was pushed to trigger
the same CI on the same text; `main` was never at this head and the branch is not merged.

The question is whether A2.1's acceptance can say *no*, because a rule that has only ever been
seen satisfied has not been tested:

```
cap-backend   MISMATCH   workflow_vs_observed=MISMATCH  producer_alignment=MISMATCH
                        built_with=docker
                        invocation dispatch = "docker CLI plugin", argv = [docker, buildx, build, …]
                        fields: buildx_version=equal  controlled_buildx_version=equal
                                controlled_buildx_commit=equal  controlled_buildx_integrity=equal
                                build_invoked_controlled_executable=DIFFERENT
                        build exit 0    contract_gaps []
cap-frontend / cap-sandbox-http / cap-sandbox-browser / cap-egress-proxy
               CONFORMING, built_with=/tmp/cap-a21-controlled-buildx/buildx

set: BATCH 3 A2.1 PRODUCER NOT CONFORMING      authorizes_a2_2: False
problems: ["cap-backend: a required comparison disagrees"]
```

Three things are worth the reading. The disagreement is *isolated*: the pinned binary was
installed, hashed and version-checked correctly, so four of five fields say "equal" and only the
invocation says otherwise -- which is the specific failure mode, detected by the specific field,
rather than a general red haze. The other four images stayed CONFORMING: one bad image does not
rewrite the records that agree, and the set refuses anyway. And the `producer-observation` job
**passed**, by design: the approval says a measured mismatch is evidence in this batch, so the
job fails only on a build that did not succeed or an instrument that could not complete, while
the verdict that gates A2.2 comes from `producer-set.json`. A green job and a refused set are
the intended pair, and this cycle is the only way to see them together without inventing the
expectation after the fact.

The same edit has a second, independent catch, and it was checked rather than predicted: with the
mutated job text in place, `test_the_observation_job_builds_with_the_controlled_executable_not_the_plugin`
fails on its first assertion --

```
AssertionError: the build must run through the controlled executable, not the CLI plugin
1 failed, 1 passed, 65 deselected
```

-- while `test_the_observation_job_publishes_nothing` still passes, correctly, because the
mutation made the build *less* controlled, not more publishing. That is the CI `backend` job's
check on this head, run against the same file; the repository's own
`test_control_2c_the_system_plugin_being_used_instead_is_a_mismatch` covers the recorder's half
under a stub. Three layers -- a workflow-text rule, a recorder control and a live runner record --
react to one mutation, which is the arrangement this project treats as the minimum for a control
that is meant to bite.

## H. A2.1 verdict

**BATCH 3 A2.1 PRODUCER CONFORMING.**

The acceptance rule is the one the approval specified, applied by
`record_build_producer.py --combine` to the five records at head `b98eb3b` (run
`35868200920`), not restated here:

| condition | measured |
| --- | --- |
| each image's `lock_vs_workflow` | CONFORMING ×5 |
| each image's `workflow_vs_observed` | CONFORMING ×5 |
| each image's `lock_vs_observed` | CONFORMING ×5, both digest layers scored |
| each image's `producer_alignment` | CONFORMING ×5 |
| the controlled pin against the release lock | CONFORMING ×5 |
| each non-publishing build succeeded | exit 0 ×5, and every recorder exited 0 |
| the records describe one round | one run, one revision; the scorer refuses otherwise |
| the observation was complete | no contract gap in any record; the set problem list is empty |

Cycle 1 (`ecd7ac4`) is part of this verdict rather than a contradiction of it: five records that
agreed on every producer layer and still read `UNKNOWN` because the instrument required an
install path that no runner's buildx prints. The repair removed a claim the evidence could not
support; it did not loosen a comparison -- §B's arms and cycle 3's live control are here to keep
that distinction checkable rather than assured.

**What this authorises.** Planning A2.2: the producer identity A2.1 made deterministic is now
something a release path can be switched onto and a gate can block on, and F-44's open half --
which Batch 3A localised to the CLI -- has an implementation that CI shows working for all five
images, including the one whose base dependency Batch 3A could not observe under a container
builder.

**What this does not authorise, and does not do.** A2.2 is not implemented and not run: the
release path still builds through `docker buildx` with no `--builder`, no mismatch blocks
publication, no new candidate is frozen, and no recertification has been attempted. No tag was
created and the sealed release is untouched (§R). F-44 stays **OPEN** until the release path is
switched and a mismatch is blocking -- the finding asks about *published* images, and A2.1
observes only non-publishing builds. F-47 is filed at §Q for the reason A2.1 exposes: recording
a producer and being obliged to read it are different features, and only the former exists.

Cycle 3 -- a build sent deliberately back through the runner's plugin, on a scratch branch --
is the live test that the acceptance rule can say *no*. Its result belongs in §G before this
verdict is read as the end of the story: A2.1's claim is that the record detects selection
failure, not that selection happens to be correct.

## I.–P. A2.2 -- planned, and not executed

Everything below is the shape A2.2 takes if approved. None of it is implemented in this round: no
release build command changed, no gate blocks on a producer, no candidate was frozen, no
certification was re-run, and no tag exists. The plan is written out here rather than held in
conversation because A2.1's own approval says only a CONFORMING verdict authorises *planning*
A2.2 -- this is that plan, and it is frozen to the field names CI just measured, so that A2.2
blocks on what has already been observed rather than inventing a vocabulary at the moment it needs
a green answer.

### I. A2.2 implementation (planned)

The release image path switches to what `ci.yml`'s observation job runs today: the controlled
buildx executable, invoked by absolute path; an explicit `--builder <configured name>`; the
`docker-container` driver; the pinned BuildKit digest. `build_release_image.sh` would take the
builder name and executable from the same declarations the recorder reads, so a release cannot
record a pin it did not use. CI's release-image dry builds would exercise the same producer path
as publication; where they cannot, the reason is written into the record rather than left as a
silence -- which is how `cap-sandbox-browser` got observed at all here.

### J. Producer blocking contract (planned)

Publication or build fails when any required field of any published image is `MISMATCH`,
`UNKNOWN`, `ERROR` or absent. The fields are the ones A2.1 already measures and measures green:

| required field | where it is scored today |
| --- | --- |
| controlled buildx identity (`argv[0]` of the build) | `workflow_vs_observed.build_invoked_controlled_executable` |
| buildx version | `workflow_vs_observed.controlled_buildx_version`, `lock_vs_observed.buildx_version` |
| buildx commit | `workflow_vs_observed.controlled_buildx_commit` |
| buildx integrity (sha256 against the pin) | `workflow_vs_observed.controlled_buildx_integrity` |
| builder name | `workflow_vs_observed.builder_name` |
| driver | `workflow_vs_observed.driver` |
| running BuildKit digest / pinned-index relationship | `lock_vs_observed.buildkit_digest`, `.digest_relation` |
| running config / pinned child config relationship | `lock_vs_observed.buildkit_child_config`, `.config_digest_relation` |

`MISMATCH` and `ERROR` both block and stay distinct: one says the producer disagrees with the
declaration, the other says no producer could be established, and an operator needs to know which
repair is being asked for. `builder.id` is not a shortcut and is not added -- four named
comparisons carry the answer, and a single opaque id is how F-44 became invisible in the first
place. The path field stays conditional exactly as A2.1 left it: required when a buildx reports a
path, never required when none does, and able to block on a contradiction either way.

### K. Release evidence binding (planned)

Each published image's record binds its image digest, its `source_revision` and its producer
record, and the completeness gate proves all five for **one run of one candidate** -- the rule
`--combine` already enforces in observation, moved into the publication path. A conforming
producer record from a friendlier run is not evidence about this one; that is the cherry-picking
A2.1's set scorer refuses, and F-47 is the reason the refusal has to live in the gate.

### L. Tests and controls (planned)

The negative controls A2.2 needs are one layer above the eleven A2.1 already has: one image
mismatching blocks the release; one image with no producer record blocks; a record from the wrong
SHA or run blocks; four conforming plus one unknown blocks; all five conforming passes. F-33's
and F-42's protections stay in place and green -- the producer authority is added beside the
certification authority in the same gate, and neither may be satisfied by the other.

### M. New candidate (planned, and none frozen here)

A candidate is frozen only after implementation, green CI, five CONFORMING producer records, green
tests and a clean tree -- and then no docs commit lands after it. This round's tip is a
docs/report commit by design; the A2.1 acceptance head `b98eb3b` is evidence about the observation
path, not a release candidate.

### N. Full recertification (planned)

Producer-affecting by policy even where the classifier reads `INHERITED`, so A2.2 re-runs at the
new candidate: CI, Linux production, K8s, the 7200 s reliability run, strict final GA, supply
chain, and the publication-gate dry-run -- each candidate-direct wherever the workflow supports
being pointed at a SHA.

### O. Gate dry-run (planned)

The dry-run must show two separate authorities, not one green light: **certification authority**
(F-33/F-42 PASS on candidate-direct evidence) and **producer authority** (five images CONFORMING
for that candidate). No tag, in this or any later round, without both.

### P. F-44 disposition (planned)

F-44 closes when the release path is switched *and* a mismatch blocks *and* a published candidate
shows five CONFORMING producer records. Not before, and not by editing the entry: A2.1 moved the
open half from "we cannot tell which buildx ran" to "the release still does not use the one we
can tell".

## Q. Deferred: B3, B4, F-46, F-39 -- and the finding A2.1 exposes

* **B3 stays DEFERRED.** The Batch 2 design withheld it pending five answers; A2.1 answered the
  producer-execution one, and no B3 content assertion is made or enabled here. `builder.id` is
  still not a field anything may assert on -- this batch deliberately did not add one, and
  carries the four named comparisons instead.
* **B4 stays OUT OF SCOPE** -- content provenance, workflow identity and signature/verification
  are its contracts, and A2.1 touched none of them.
* **F-46 stays OPEN, policy review only.** A2.1 neither mounts nor unmounts anything:
  the host-root-equivalent compose worker is unchanged, so no release-blocking decision was
  taken and none was needed.
* **F-39 stays PARTIAL, and A2.1 is what makes its next step meaningful.** The two-build
  experiment was not run here; `f39_measurement.builds_compared` is still 1 in each of the ten
  records across the two main cycles. What changed is the precondition: a reproducibility pair
  can now attribute a difference to a producer at all, because the executable that built an
  image is named by digest and by argv rather than by whichever plugin a runner chose. Two
  builds of one SHA remain a separate, later step, and F-39 is not a prerequisite for A2.2.
* **F-47 -- the producer block in release evidence is read by no gate.** Filed during this batch
  (see `docs/known-issues.md` item 14). `build_release_image.sh` states at the point it writes
  the field that "the release gate below refuses a pushed image whose producer was never read";
  `release.yml` mentions the producer nowhere, and `release-image-completeness`'s
  `REQUIRED_FIELDS` does not contain it. A2.1 does not close this and must not be read as
  closing it: `--combine` is an observation instrument inside a non-publishing job. A2.2's
  blocking contract is where the refusal has to be *implemented*, which is why F-47 and A2.2 are
  the same piece of work and why this report names the required fields in §J rather than
  assuming the sentence in the script stands for them.

## R. Sealed-release integrity

Re-read from the API after the A2.1 pushes (`_tmp/a21_sealed.txt`), with the same read-only audit
used by Batch 3A:

| check | result |
| --- | --- |
| `refs/tags/v1.0.6-rc1` | `d0c7c7dc14cb075c095bd7970650b180a73afbf1` -- the sealed tag object, unchanged |
| tag count / list | 12 tags, newest still `v1.0.6-rc1`; no new tag created |
| releases | nine, `v1.0.6-rc1` and `v1.0.5` both non-draft; **latest stable remains `v1.0.5`** |
| published `1.0.6-rc1` image digests | all five (`cap-backend`, `cap-frontend`, `cap-sandbox-http`, `cap-sandbox-browser`, `cap-egress-proxy`) `unchanged_since_earlier_read: true` |
| `refs/heads/release/1.0.6-rc1` | unchanged |
| workflow runs with `event: release` | none |
| strict certification dispatch | none sent by this batch |

**SEALED RELEASE INTACT.** The lifecycle of `v1.0.6-rc1` stays closed: no move, no delete, no
recreation, no overwrite of a published tag, no edit of the published Release or its assets, no
RC re-run, no promotion to latest. `v1.0.5` remains stable. F-25's closed status is untouched.

A2.1's own surface is publish-free by construction and by check: the observation job contains no
`--push`, no login action, no `--tag`/`-t`, no `ghcr.io` reference, no registry credential and no
job-level `permissions` block, and
`test_the_observation_job_publishes_nothing` asserts all of that against the job text --
including the pipe-to-interpreter and `registry:` forms, so the pinned download cannot be
dressed up as a publication path. The five images it builds are exported to local OCI archives
and deleted with the runner.


## S. FINAL VERDICT

**BATCH 3 A2.1 PRODUCER CONFORMING.**

Five images, one pinned and verified buildx executable, one named `docker-container` builder, both
BuildKit digest layers scored, and the whole set from one round: every required comparison of
every record reads CONFORMING at head `b98eb3b` (run `35868200920`), the acceptance rule refusing
the set the moment one image disagrees -- which cycle 3 proved on a real runner rather than only
in a fixture. The release build path is unchanged, a producer mismatch blocks nothing, and no
release tag exists: F-44 stays OPEN, F-47 is filed, and §I–§P is the A2.2 work that would close
them, now planned from measured field names instead of proposed ones.
