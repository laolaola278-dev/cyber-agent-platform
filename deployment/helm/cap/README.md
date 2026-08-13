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
