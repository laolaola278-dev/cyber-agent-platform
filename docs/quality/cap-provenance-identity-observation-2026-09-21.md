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
