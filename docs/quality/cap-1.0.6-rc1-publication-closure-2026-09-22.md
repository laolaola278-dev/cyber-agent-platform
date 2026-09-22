# CAP v1.0.6-rc1 — PUBLICATION CLOSURE RECORD (plan item H / F-25)

Date 2026-09-22. This is the record plan item H asked for: a **new, dated file** quoting only
what live run `35553750674` and the registry/API themselves report, written after the reads became
possible. It **does not** restate, edit, or supersede the sealed release, its assets, its tag, or
any shipped file — the shipped `docs/known-issues.md` and `CHANGELOG.md` inside the tag describe the
world as it was at publication and stay that way. F-25 remains **CLOSED by that run**, which is a
statement about the run, not about this page.

Every number below came from a `GET`: run/job/artifact metadata and artifact bytes from
`api.github.com`, manifests and blobs from `ghcr.io`. Transcripts: `_tmp/h_recheck/`
(`evidence_summary.json`, per-artifact zips, `completeness.log`). Read scope only; nothing was
written to the remote, and no sealed digest moved (last column of §2).

## 1. The publication run

| Field | Value |
| --- | --- |
| Run | [`35553750674`](https://github.com/laolaola278-dev/cyber-agent-platform/actions/runs/35553750674) — workflow `Release`, `event: push`, `head_branch: v1.0.6-rc1` |
| Commit | `4d8f9c72b72dcea74e8588374fe8ef3a04564eb6` (the `RELEASE_SHA`; `release/1.0.6-rc1` still points at it) |
| Duration | `2026-09-21T02:18:37Z` → `02:39:02Z` (**20 m 25 s**) |
| Jobs | **25 of 25 completed/success**, none skipped, none failed |
| Shape of the graph | `quality-gates` (10 legs: frontend, backend, migration, packaging, image-and-security, and five `release-image-builds`) → `validate-tag` → `verify-certification` → `release-images` (4 matrix cells) → `release-sandbox-browser` → `release-image-security` (5) → `release-image-completeness` → `release-chart` → `publish-release` |

This is the fact F-25 was about: the release image graph — the path that builds, scans, checks
completeness, renders the chart values and publishes — **executed end to end, once, successfully, on
a real tag push**, at the commit the tag points to.

## 2. The five published images

Records: artifact `cap-1.0.6-rc1-release-images` → `outputs/release-images/release-images-1.0.6-rc1.json`
(`"verdict": "PASS"`, `"commit": 4d8f9c72…`, 5 images). "Served today" is the
`Docker-Content-Digest` the registry returns for the tag on re-reading, 2026-09-22.

| Image | Published ref | Index digest (recorded) | linux/amd64 platform manifest | Trivy | SBOM / provenance | Served today |
| --- | --- | --- | --- | --- | --- | --- |
| `cap-backend` | `ghcr.io/laolaola278-dev/cap-backend:1.0.6-rc1` | `sha256:a733b90c7a8417b172a59f91f739b85e8a463c732da674013d852cba30f9ac8e` | `sha256:1cc2ae83396dbdcc2a2ef658ed94151c9320319fbf49eed8ecfc6d05ffe8d375` | PASS, 0 findings | true / true | **equal** |
| `cap-frontend` | `…/cap-frontend:1.0.6-rc1` | `sha256:e1b1889a868c114f5be7df41de3f9c0baa252a84b4340eae84929054392ad761` | `sha256:4e9f64204f7fa0645dbc03f0f0d7de375e5ed1987ef996a1aba28d41e5c00dc5` | PASS, 0 findings | true / true | **equal** |
| `cap-sandbox-http` | `…/cap-sandbox-http:1.0.6-rc1` | `sha256:36bb2f7993ac8eb9ee8b7bdedfd521b8b847b5f75ef32fdbbb54bfb4b407e3ad` | `sha256:c8e22df7bef15315641f527dea68eda9162c0426b7c7c64dd3457924cbc4c09b` | PASS, 0 findings | true / true | **equal** |
| `cap-sandbox-browser` | `…/cap-sandbox-browser:1.0.6-rc1` | `sha256:b369618871bd3baeb4a6c4d8c9bbc7217a99d68f1591f2740e5e45b45779953f` | `sha256:2cf016f56d7864e6dd4c092ff76e117e6f296a279b321bddfda7e2fd9ab3f296` | PASS, 0 findings | true / true | **equal** |
| `cap-egress-proxy` | `…/cap-egress-proxy:1.0.6-rc1` | `sha256:8ab8c234f278436f8ffa2bcd2a38495b85b10bb587a287b0f4aa41cde0226689` | `sha256:dcddd5947fcc3127e6d8ec2354c5ae4efba2c85f8592f3fb69bc32e0766f5b63` | PASS, 0 findings | true / true | **equal** |

Shared fields: all five `pushed: true`, `build_driver: buildx`, `platform: linux/amd64`,
`source_revision: 4d8f9c72b…`. Trivy policy in every record: severities `HIGH,CRITICAL`,
`ignore_unfixed: true`, `exit_code: "1"`, `blocking_findings: []`.

Two details worth keeping rather than smoothing over:

- **`config_digest` is `null` in all five published records.** The release recorded the index digest
  and the `linux/amd64` platform manifest digest — which is what `values-release-1.0.6-rc1.yaml`
  pins and what a pull resolves — and not the OCI config digest. The completeness gate's required
  fields are satisfied by the two it asks for; this record notes the third is absent by design so no
  later reader mistakes silence for a failed field.
- **`cap-sandbox-browser`'s base is a digest, not a tag**:
  `ghcr.io/laolaola278-dev/cap-sandbox-http@sha256:36bb2f7993ac…` — exactly the `cap-sandbox-http`
  index digest above, which is the F-7/F-39 chain the browser image was rebuilt to express. The other
  bases are pinned too: `python:3.13-slim@sha256:8d9d0b8b…`, `python:3.13.12-slim-bookworm@sha256:a58daefb…`,
  `node:22-alpine@sha256:b6f26b36…`, `nginx:1.30.4-alpine@sha256:dc5069ad…`,
  `ghcr.io/astral-sh/uv:0.8.3@sha256:ef11ed81…`.

## 3. The completeness gate's own output

`release-image-completeness` (job `106196360043`, success) printed, at `02:38:37.728Z`:

```
release image set complete: cap-backend, cap-egress-proxy, cap-frontend, cap-sandbox-browser, cap-sandbox-http
```

Its rules, from the same log: it downloads the five `release-image-*` and five `security-*`
artifacts (verifying each download's expected digest), requires each image record to carry the
digest fields, requires the scan record to be `"verdict": "PASS"` with no `blocking_findings`, and
exits 1 with `PUBLICATION BLOCKED -- release image set incomplete:` otherwise. Nothing about this
gate is being paraphrased from the YAML: the line above is what the run wrote.

## 4. The rendered values file

`values-release-1.0.6-rc1.yaml`, 1563 bytes,
`sha256:bfcb2aa2d02e5d5a92c10e90383b042953211096ff525f90545262c830738879` — byte-identical between
the `cap-1.0.6-rc1-release-images` artifact and the `dist/` copy inside the
`cap-1.0.6-rc1-release-assets` asset bundle, i.e. the file attached to the Release is the file the
pipeline rendered. It pins **six** coordinates (backend, worker, `worker.sandbox.image`,
`worker.sandbox.browserImage`, frontend, egressProxy — `worker.image` being a second deployment of
the backend artifact), each as `repository` + `tag: "1.0.6-rc1"` + the `sha256:` index digest from §2.

The five Release assets, as served today (read-only, `GET /releases/tags/v1.0.6-rc1`, all
`updated_at: 2026-09-21T02:38:59Z` — the publication moment, unchanged by everything since):
`cap-1.0.6-rc1.tgz` 10 145 B, `CHANGELOG.md` 55 480 B, `known-issues.md` 25 919 B,
`v1.0.6-rc1.md` 10 199 B, `values-release-1.0.6-rc1.yaml` 1 563 B
(`sha256:bfcb2aa2…`, matching the pipeline copy).

## 5. What the certification evidence said at the moment of publication

The run's own gate artifact — `cap-1.0.6-rc1-certification-evidence` →
`release-certification-gate.json` — records `verdict: "PASS"`, `failures: []`,
`ancestors_considered: 80`, and these four resolutions:

| Workflow | Run selected | Commit | Distance | Job conclusions it read |
| --- | --- | --- | --- | --- |
| `cap-linux-certification.yml` | `35506716466` | `b671f5374…` | +17 | `cap-production-certification: [success]`, `postgres-version-matrix: [success ×3]` |
| `cap-k8s-certification.yml` | `35508689787` | `d30b4e7d8…` | +12 | `k8s-certification: [success]` |
| `cap-ga-certification.yml` | `35512844679` | `b671f5374…` | +17 | `ga-certification: [success]`, `supply-chain: [success]` |
| `cap-ga-reliability.yml` | `35506714369` | `b671f5374…` | +17 | `reliability: [success]` |

**That file contains no `authority` field at all** — the gate that produced it decided from run
conclusions and job sets, and from nothing else. This is not a criticism of the sealed release: the
four rounds it named really were release-scoped dispatches that certified, and the strict GA round
`35512844679` recorded `mode: final-strict`, `full_ga_certified: true`, 40/40. It is the record of
*why* F-33 was worth closing: the same code path could not tell those rounds apart from a
development-mode run, and batch 1 removed that possibility for every future tag.

Two HEAD-side changes since, neither of which touches this release: **F-33** (the gate now reads the
round's own verdict artifact, and batch 1's remote validation showed it refusing a green
development-mode GA round that the pre-F-33 gate would have accepted) and **F-42** (a required job
present with `conclusion: "skipped"` no longer counts as execution — the rule that would have read
this table the same way but with one fewer guarantee about what "ran" meant).

## 6. What this record does not claim

- **It does not verify a signature.** The pushed attestations are SLSA v1 statements with
  `runDetails.builder.id` empty on all five images, no `sha256-….sig`/`.att` tag on any package, and
  no reference-tool verification run (no `gh` on this host). Detail:
  `docs/quality/cap-provenance-identity-observation-2026-09-21.md` §6.
- **It does not re-run any certification.** Nothing here re-means the soak, the strict GA round, the
  K8s round or the PostgreSQL matrix; the evidence is read, not recreated.
- **It does not modify the publication.** No asset was re-uploaded or edited, no release body
  rewritten, no tag moved, no image retagged; §2's "Served today" column is the confirmation of that,
  read from the registry rather than asserted.
- **It does not establish reproducibility.** F-39's status is unchanged: no two independent builds of
  one commit exist, and the reproducibility model is still PROPOSED, not policy.
- **It is not a release note for `1.0.6-rc1`.** `docs/releases/v1.0.6-rc1.md` — as published — holds
  that role; this page is the audit-side record that the publication path ran and what it produced.

## 7. Register effect

- **Plan item H / F-25**: the closure record exists, so the *documentation* half of item H is done.
  F-25 itself stays closed by run `35553750674` and is not reopened or re-evidenced here.
- **Findings still open after this record**, each with its own price and none of them affected by it:
  F-24 (compose mutable tags), F-37 (evidence pointers into `outputs/`; guard transitional until E3),
  F-39 (reproducibility undetermined), F-41 (schema forbids a digest-only pin for two of six
  coordinates), and the two CI-only items batch 1.1 carries: F-42 implementation closed with remote
  validation pending, and the live Actions/API checks now declared-and-enforced in `ci.yml`, awaiting
  their first real CI execution.
