# Docker Compose Deployment

Compose defines health-gated startup: PostgreSQL and Redis must become healthy before Backend starts; Backend `/ready` must pass before Frontend starts. Explicit Compose restarts cascade to dependents where connection re-establishment is required.

## Configuration

Required variables are `POSTGRES_PASSWORD`, `DATABASE_URL`, `SECRET_KEY`, `JWT_SECRET`, `RBAC_TRUSTED_PROXY_SECRET`, and observability/admin passwords when those profiles are enabled. Omitted required values cause Compose interpolation failure. Repository placeholders are rejected when `APP_ENVIRONMENT=production`.

`CAP_ZAP_API_KEY` is the exception rather than the rule: compose forwards it to the api container but defaults it to empty, meaning "ZAP not provisioned". Set it in `.env` before starting the stack if you want the assessment and incident planes to run scans; left unset they answer `SECRET_NOT_FOUND` rather than authenticate to ZAP with the placeholder text from `.env.example`.

## Lifecycle

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs backend
```

Use immutable RC/final image tags in controlled environments. `docker compose down` preserves named volumes; never use `down -v` as routine rollback or cleanup.

## Verification

- Backend health and readiness succeed.
- Frontend serves `/` and proxies `/api/` to Backend.
- Unknown/missing trusted identity receives 401; unauthorized role receives 403.
- Alembic has one head and upgrade is complete.
Redis runs in this stack because `settings.redis_url` names it, not because the request path
uses it: no module in `backend/app` imports a Redis client, and `GET /readiness`'s
`redis_configured` is `bool(redis_url)` -- it reports that a URL is configured, not that
anything connected. Nothing in v1 breaks if the `redis` service is removed.

Sandbox images are a separate build. `docker compose up --build` builds the
backend, the console and the egress proxy, but the acquisition worker defaults to
`SANDBOX_IMAGE=cap-sandbox-http:latest` and `SANDBOX_BROWSER_IMAGE=cap-sandbox-browser:latest`,
and compose builds neither -- the first acquisition would fail on a missing image.
Build them from the repository before starting a run:

```bash
bash backend/docker/build_sandbox_images.sh --with-browser
```

That script tags `:latest` locally, which is right for a single-node evaluation box
and wrong for anything you intend to upgrade later: set `CAP_SANDBOX_IMAGE_TAG` to the
release version instead, push the images to a registry you control from the same
Dockerfiles (`backend/docker/sandbox-http/Dockerfile`,
`backend/docker/sandbox-browser/Dockerfile`,
`backend/docker/egress-proxy/Dockerfile`), pin them by digest, and pass the coordinates in
(`SANDBOX_IMAGE`, `SANDBOX_BROWSER_IMAGE`, or the chart's `worker.sandbox.image` /
`worker.sandbox.browserImage` / `egressProxy.image` blocks). The browser image layers on the
HTTP one and takes it as `--build-arg SANDBOX_HTTP_BASE`, with no default -- build
`cap-sandbox-http` first and name it, because `:latest` would silently reuse whatever the
machine already had. `release.yml` publishes all five under the release version, so a
production install can pull them instead of building them.
- Prometheus targets and alert rules load when the profile is enabled.
