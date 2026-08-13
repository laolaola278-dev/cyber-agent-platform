# Backup and Restore

## Backup scope

Back up PostgreSQL logical data and required roles/settings, persistent attachment/evidence stores if configured, release values without secret exposure, image/Chart digests, and relevant audit/observability retention. Redis is coordination/cache unless a deployment explicitly assigns durable responsibility.

## PostgreSQL example

```bash
pg_dump --format=custom --no-owner --file=cap.dump "$DATABASE_URL"
pg_restore --list cap.dump > cap.dump.manifest
```

Encrypt backups, record checksums, restrict access, apply retention, and copy to an independent failure domain.

## Restore verification

1. Provision an isolated supported PostgreSQL instance.
2. Restore with `pg_restore --clean --if-exists --no-owner` into the isolated target.
3. Run Alembic head verification and CAP read-only integrity smoke tests.
4. Compare critical table counts, audit continuity, and sampled relationships.
5. Record duration and evidence against RPO/RTO.

A backup is not certified until restore has succeeded. Production restore requires change/incident approval, writer shutdown, target validation, and post-restore audit.
