# Security Policy

## Supported versions

`1.0.0-rc1` receives security fixes during release-candidate validation. It is not yet certified for unqualified production use.

## Reporting

Do not open a public issue for a suspected vulnerability. Report it privately to the repository security contact with the affected version, reproducible steps, impact, and any proposed mitigation. Do not include live credentials or production data.

## Deployment requirements

Production deployments must use TLS at the ingress or trusted gateway, external secret management, non-default independent secrets, fail-closed RBAC proxy headers, database backup verification, audit retention, and disabled API documentation unless explicitly approved. See `docs/deployment/production-checklist.md`.
