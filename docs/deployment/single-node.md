# Single-Node Deployment

Single-node deployment is for evaluation, integration, and controlled low-risk staging. It is not highly available and does not satisfy the production certification by itself.

## Prerequisites

- Docker Engine with Compose v2
- At least 4 CPU, 8 GiB RAM, and persistent disk capacity appropriate for PostgreSQL and observability retention
- An uncommitted `.env` created from `.env.example` with independent random credentials

## Procedure

```bash
copy .env.example .env
# Replace every replace-* value and verify APP_ENVIRONMENT=production.
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
```

Verify `/health`, `/ready`, Web Console, RBAC denial, audit, and metrics. Observability is optional for evaluation but required for production evidence:

```bash
docker compose --profile observability up -d
```

Do not expose PostgreSQL, Redis, Grafana, pgAdmin, metrics, or CAP directly to untrusted networks. Terminate TLS and verify identity at a trusted gateway.
