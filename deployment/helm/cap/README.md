# CAP Helm Chart

This application chart deploys the CAP Backend and Web Console. PostgreSQL and Redis are external production dependencies and are intentionally not bundled.

## Required Secret

Create `cap-runtime` before installation with these keys: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `JWT_SECRET`, and `RBAC_TRUSTED_PROXY_SECRET`.

```bash
helm lint deployment/helm/cap
helm template cap deployment/helm/cap --namespace cap
helm upgrade --install cap deployment/helm/cap --namespace cap --create-namespace --wait --wait-for-jobs --rollback-on-failure --timeout 10m
```

Never commit a rendered Secret or pass secret values through shared shell history. Use an external secret manager in production.

## Image coordinates

Every CAP image this chart can deploy is named in `values.yaml` as a
`{repository, tag, digest}` block, and the templates compose them through
`cap.imageRef`:

| value path | image |
| --- | --- |
| `backend.image` / `worker.image` | `cap-backend` |
| `frontend.image` | `cap-frontend` |
| `worker.sandbox.image` | `cap-sandbox-http` |
| `worker.sandbox.browserImage` | `cap-sandbox-browser` |
| `egressProxy.image` | `cap-egress-proxy` |

An empty `tag` falls back to this chart's `appVersion`, so installing the chart
as released pulls the version it was cut with. Setting `digest` wins over the
tag and is how you pin the exact manifest list -- `release.yml` records one per
image in `release-images-<version>.json`. `latest` is not a supported value
anywhere in this chart: until F-7 the three images above that the release did
not publish defaulted to `:latest`, so a default install could not survive its
first acquisition.

The `repository` default (`ghcr.io/example/…`) is a placeholder, not a pullable
coordinate: no organisation named `example` serves these images. Set the owner,
or install the release as it ships -- each release publishes
`values-release-<version>.yaml` beside the chart archive, and it carries all six
blocks with the publishing owner, the release tag and the digest the build
recorded:

```console
helm upgrade --install cap cap-<version>.tgz -f values-release-<version>.yaml
```

That is the installable form of the release. `helm install cap cap-<version>.tgz`
alone states the version correctly and the registry not at all.
