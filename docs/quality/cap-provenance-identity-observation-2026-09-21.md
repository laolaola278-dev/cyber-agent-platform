# CAP PROVENANCE IDENTITY — OBSERVATION RECORD (batch 1, stage 2)

Date: 2026-09-21. Scope: read-only. Nothing here pushed, pulled, retagged, rebuilt or
edited registry content, and no B3/B4 change was implemented — the stage's own stop
condition (below) fired.

## 1. What this stage was for

Plan item B asks whether the sealed `v1.0.6-rc1` images' build attestations identify
the workflow run that produced them, before any assertion is written about that
identity. The instruction was to *observe* first: for each sealed image, inspect the
pushed attestation by digest and record the predicate type, the subject digest, the
`buildType`, `runDetails.builder.id` exactly as stored, the invocation/source/revision
fields, and the buildx/BuildKit information where it is recoverable — without inferring
any of it from Docker documentation, and without defining a future builder-id pattern
before the real value has been seen.

**Verdict: UNVERIFIED. The pushed attestations could not be read from this environment,
so the stage stopped and B3 (assert the identity) and B4 (Sigstore-signed attestations)
were not started.**

## 2. What was attempted, and what each attempt answered

| Probe | Result |
| --- | --- |
| `gh` binary: `PATH`, `which`/`find` across the home directory, and the conventional install locations (`C:\Program Files\GitHub CLI`, chocolatey `bin`, WinGet links, `%LOCALAPPDATA%\Programs\GitHub CLI`) | not found; the GitHub CLI program directory does not exist on this machine, and `gh --version` → `command not found` |
| `GH_TOKEN`, `GITHUB_TOKEN`, `CR_PAT`, `GITHUB_PAT`, `REGISTRY_TOKEN`, `DOCKER_PASSWORD` in the environment | none present |
| `~/.config/gh/hosts.yml`, `~/.netrc` | both absent |
| `~/.docker/config.json` | exists; `"auths": {}` (no entries), `"credsStore": "desktop"`, and the `docker-credential-desktop` helper binary is not installed |
| `GET https://ghcr.io/token?scope=repository:laolaola278-dev/cap-backend:pull` | HTTP 200, a 68-character token — an anonymous token, which is what ghcr hands out before it refuses the repository |
| `GET https://ghcr.io/v2/laolaola278-dev/cap-backend/manifests/v1.0.6-rc1` with that token and an index/manifest-list `Accept` | **HTTP 404** |

A 404 with an anonymous token is ghcr's answer to both "this package does not exist"
and "you may not see it", so it does not even establish that the tag is unreadable by
policy — only that it is unreadable *from here*. The publication run's own artifacts
were checked too: no local capture of run `35553750674`'s image evidence exists (the
monitor log kept for that run records status transitions only, no digests).

## 3. What is observable in this repository, and what it does not prove

Recorded separately because none of it is the pushed attestation:

- `scripts/release/build_release_image.sh:137` builds the pushed images with
  `--provenance=true --sbom=true --push` and no `builder-id` attribute; the dry-build
  branch at `:139` uses `--provenance=false --sbom=false --load`. So buildx's own
  defaults decide what identity, if any, lands in the attestation — which is exactly
  the behaviour R1 refused to assert from documentation in either direction.
- The same script writes the evidence block's `attestations` field from *the flags it
  passed* (`:217-219`), not from what the registry stored.
- `release.yml`'s completeness gate reads that field back and refuses a release whose
  record says `sbom` or `provenance` is false (`release.yml:831-833`, "no
  `{kind}` attestation"). What that gate can therefore see is the builder's own claim
  about its command line. It cannot see whether an attestation exists, what its
  predicate says, or what produced it. That is the durable gap R1 identified, and it
  stands unchanged.
- All 17 captured build-evidence records in this checkout are dry builds
  (`"pushed": false`, `"ref": "*:dry-build"`), and each carries
  `"attestations": {"sbom": false, "provenance": false}` — the correct description of
  a build that was never pushed. Not one of them describes a published image, so none
  of them is evidence about what the registry holds.
- The GA rounds' own `cap.provenance.v1` artifact (`provenance.json` inside
  `ga-cert-artifacts`) records `builder: "github-actions"`, a `commit`, five subject
  name/digest pairs and two SBOM hashes. It has no predicate type, no `buildType`, no
  `runDetails`, and no invocation fields at all. This is CAP's own summary of a build,
  not an SLSA attestation, and it is not evidence about what the registry holds.

## 4. The contract deliberately NOT written yet

The plan's B3/B4 stay unbuilt. To keep them from being written from memory, the
preconditions are recorded here rather than as assertions:

1. **Read the real thing first.** On a host with a credential that can pull the
   packages, for each of the five sealed images: resolve the tag to its index digest,
   read the attestation manifests referenced by that index (`In-Toto` predicate,
   `dsse`/`sigstore` bundles as served), and record verbatim:
   `predicateType`, `subject[].digest`, `predicate.buildType`,
   `predicate.runDetails.builder.id`, `predicate.invocation.*`,
   `predicate.materials[].{name,digest}`, plus `verifiedMaterial`/`subjectName`
   whichever the served form carries. `gh attestation verify` (or an equivalent
   reference-tool run) is the comparable second reading, because a hand-parsed
   attestation is a claim about bytes, not a verification.
2. **Then assert what was seen.** B3 becomes a gate on the *read-back* value of
   `runDetails.builder.id` for a published digest, not on the build's own flags; the
   regex or equality test comes from the observed string, and until then any pattern
   invented here would be a guess with a green test attached.
3. **Then decide about signing.** Whether `actions/attest-build-provenance` (and
   optionally `actions/attest-sbom`) is worth adding is a judgement about who has to
   be able to verify a release without trusting this repository's own evidence. It
   should be taken after step 1 shows what buildx already records.
4. **Pin the builder.** `docker/setup-buildx-action@v3` is referenced by minor tag in
   both `ci.yml` (two jobs) and `release.yml` (two jobs), with no `buildx-version`
   input, so the buildx and BuildKit versions that produce an attestation float with
   the runner image. Pinning is a CI-only change (`ci_workflow`), but it changes the
   producer of any digest people are pinning, so it belongs in a round that
   re-certifies anyway.

## 5. What would change this record's status

A machine with `gh` (or any registry credential for the package namespace) plus
outbound TLS to `ghcr.io` and `api.github.com`. With that, step 1 above is a read-only
afternoon; without it, B's premise stays "verify", and this file is the record that it
was attempted and not answered.

## 6. Follow-up, 2026-09-21 (batch-1 remote validation): §1's verdict is superseded — V1 was answered

**Status change: §1's `UNVERIFIED` no longer holds. The pushed attestations have now been read,
verbatim, from the registry.** Two corrections come with that, and both are stated here rather
than quietly applied:

1. **§2's 404 was a wrong ref, not an access verdict.** The published image tag is `1.0.6-rc1`;
   the `v` prefix belongs to the git tag only. `GET /v2/laolaola278-dev/cap-backend/manifests/v1.0.6-rc1`
   answers 404 because that ref does not exist — `manifests/1.0.6-rc1` and the recorded index
   digest both answer **200**. The reasoning in §2 ("404 means absent-or-invisible, so nothing is
   established") was sound; its premise was not.
2. **`gh` is still absent, and that was never the blocker.** The registry speaks plain HTTPS, and
   the credential this machine already has for `git push` (a `ghp_`-prefixed PAT in the Windows
   credential manager) is accepted by the ghcr token endpoint. Every call made here was a `GET`;
   no registry content was written, retagged or deleted, and the token was passed to `curl`
   through stdin config so it appears in no argument list, file or log line.

### 6.1 What the registry serves, read as stored

Per image, from the manifest the tag resolves to today, against the digests
`outputs/release-images/release-images-1.0.6-rc1.json` recorded at publication
(transcript: `_tmp/v1_observed_table.json`, `_tmp/v1_tag_digest_check.json`,
per-image predicates in `_tmp/v1_<image>_provenance.json`):

| Image | index digest served by `1.0.6-rc1` | platform manifest (the attestation `subject`) | predicates in the index's `unknown/unknown` manifest | `runDetails.builder.id` | `buildDefinition.resolvedDependencies` | `buildkit_metadata.layers` digest records | `buildkit_metadata.vcs.revision` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cap-backend` | `a733b90c7a84…` = recorded **yes** | `1cc2ae83396d…` | `spdx.dev/Document` + `slsa.dev/provenance/v1` | `""` | 4 | 9 | `4d8f9c72b…` |
| `cap-egress-proxy` | `8ab8c234f278…` = recorded **yes** | `dcddd5947fcc…` | `spdx.dev/Document` + `slsa.dev/provenance/v1` | `""` | 2 | 5 | **absent** |
| `cap-frontend` | `e1b1889a868c…` = recorded **yes** | `4e9f64204f7f…` | `spdx.dev/Document` + `slsa.dev/provenance/v1` | `""` | 4 | 6 | `4d8f9c72b…` |
| `cap-sandbox-browser` | `b369618871bd…` = recorded **yes** | `2cf016f56d78…` | `spdx.dev/Document` + `slsa.dev/provenance/v1` | `""` | 2 | 3 | **absent** |
| `cap-sandbox-http` | `36bb2f7993ac…` = recorded **yes** | `c8e22df7bef1…` | `spdx.dev/Document` + `slsa.dev/provenance/v1` | `""` | 2 | 10 | **absent** |

Identical across all five, quoted as stored:

- `predicateType`: `https://slsa.dev/provenance/v1`, inside an
  `_type: https://in-toto.io/Statement/v1` document.
- `predicate.buildDefinition.buildType`:
  `https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md`.
- `predicate.runDetails.builder`: `{"id": ""}`.
- `subject[0].name`: `pkg:docker/ghcr.io/laolaola278-dev/<image>@1.0.6-rc1?platform=linux%2Famd64`,
  with `subject[0].digest.sha256` equal to the **platform manifest** digest — not the index digest.
- `predicate.runDetails.metadata.buildkit_completeness`:
  `{"request": true, "resolvedDependencies": false}`.
- `runDetails.metadata.invocationId`: a BuildKit LLB id (`ofob92nrmxiz…` for `cap-backend`), not a
  GitHub run id; `startedOn`/`finishedOn` sit inside the publication window (e.g. `02:35:21Z`→`02:35:51Z`).
- `buildDefinition.externalParameters`: `configSource.path = "Dockerfile"` and a `request` carrying
  `build-arg:REVISION = 4d8f9c72…`, `build-arg:VERSION = 1.0.6-rc1`, `cmdline = docker/dockerfile:1`,
  and for `cap-backend` also `vcs:source = https://github.com/laolaola278-dev/cyber-agent-platform`.
- No `runDetails.version`, no `predicate.materials` (v1 replaced that field with
  `resolvedDependencies`), and `buildDefinition.invocation` is `{}`.

### 6.2 The three things the reading actually establishes

None of them is flattering, which is why they are recorded before any assertion is written:

1. **`runDetails.builder.id` is the empty string on every sealed image.** Plan item B3 was framed
   as "assert the builder identity in the pushed attestation"; the observed value says buildx
   recorded no builder id at all. An equality test written now would be an assertion that a field
   is blank — which is not the guarantee anyone wanted when B was drafted.
2. **Three of five attestations name no source revision.** `cap-egress-proxy`, `cap-sandbox-http`
   and `cap-sandbox-browser` have no `vcs.revision`/`vcs.source` in `buildkit_metadata`, and
   `resolvedDependencies` counts of 2 vs 4 track exactly which images passed the VCS build args.
   So "the pushed attestation ties the image to the commit" is true for two images and false for
   three — measured, not inferred.
3. **BuildKit itself flags the provenance as incomplete**: `resolvedDependencies: false` on all
   five, i.e. the registry copy of these attestations declares its own dependency list partial.
   The `resolvedDependencies` that *are* present do carry base-image digests (e.g.
   `pkg:docker/python@3.13-slim?digest=sha256:8d9d0b8b…`, `pkg:docker/ghcr.io/astral-sh/uv@0.8.3
   ?digest=sha256:ef11ed81…`) — the same coordinates F-37's evidence pointers were written about,
   now readable from the registry instead of from a gitignored file.

And one thing that is *not* in there: no BuildKit or buildx version string appears anywhere in the
predicate (the only buildType-ish text is the documentation URL, plus a
`github_runner_image_version` key inside `internalParameters`). So the question "which BuildKit
produced the sealed images" remains unanswered **by the attestations themselves**, which is a
result for B2, not a gap in this reading.

### 6.3 What this reading does not claim

- **Nothing here is a signature verification.** No `sha256-….sig`/`.att` tag exists for these
  packages (checked via `GET /v2/<name>/tags/list`), the attestation layers arrive as plain
  unsigned OCI artifacts, and `ghcr.io/v2/<name>/referrers/<index-digest>` answers **404**.
  `gh attestation verify` was not run — there is no `gh` here — so §4's insistence that a
  hand-parsed attestation is "a claim about bytes, not a verification" still applies to this
  section. It records what the registry serves, not that a trusted third party vouches for it.
- **The sealed release is still the sealed release.** The index digests served today equal the
  publication record, so no image moved; the read was pull-scoped (`repository:<name>:pull`) and
  nothing was written.
- **B2, B3 and B4 were still not implemented.** This stage observes; it does not change a
  workflow. What changed is the input to those decisions.

### 6.4 Consequence for plan item B, with its order intact

The chain `V1 → define the observed builder/provenance contract → B3` now has its first link
satisfied, so the second link is writable: a contract that says what buildx actually records —
SLSA v1, empty builder id, `resolvedDependencies` self-declared incomplete, VCS fields present
only where the build passes `vcs:*` build args, no signing — and *then* decides which of those
gaps are worth closing and which assertions would be honest. B4's premise is likewise no longer
speculative: signing is measurably absent, so B4 is now "do we want cosign/sigstore identity on
these artifacts", a design decision with a fact in front of it. Both stay unexecuted here: B2/B3
ride a re-certifying round, B4 rides its own decision.
