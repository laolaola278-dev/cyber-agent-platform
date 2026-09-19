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
- Prometheus targets and alert rules load when the profile is enabled.
