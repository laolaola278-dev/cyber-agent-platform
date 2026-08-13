# CAP Operations Guide

## Daily checks

- `/health` returns process liveness; `/ready` confirms database readiness.
- Prometheus targets are up; CAP latency, 5xx, queue depth, and Worker utilization alerts are evaluated.
- PostgreSQL backup jobs and restore verification are current.
- Worker heartbeats, expired leases, pending approvals, failed Playbooks, and audit ingestion are reviewed.

## Change control

Use immutable image and Chart versions. Back up before migrations. Deploy through staging, wait for migration Jobs and readiness, observe error/latency/queue metrics, then promote. Never run multiple ad hoc migration processes.

## Security operations

Rotate database, JWT, application, proxy, Grafana, and integration secrets independently. Restrict `/metrics`, database, Redis, Grafana, and admin endpoints at the network layer. Keep API docs disabled in production unless approved. Review audit retention and trusted proxy header overwrite rules.

Incident actions and diagnostic procedures are in `docs/runbook.md`; deployment procedures are in `docs/deployment/`.
