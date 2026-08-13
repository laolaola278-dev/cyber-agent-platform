# CAP v1 FAQ

## Is 1.0.0-rc1 production certified?

No. It is a controlled release candidate. Architect approval and the Production Entry Gates in the Phase 23 report are required.

## Does CAP include PostgreSQL or Redis in Helm?

No. Production PostgreSQL and Redis are external dependencies. Compose bundles them for single-node evaluation.

## Where are secrets stored?

Compose loads an uncommitted `.env`; Helm references a pre-existing Kubernetes Secret. Production should use an external secret manager.

## Why are API docs disabled in production?

The OpenAPI schema exposes operational surface information. Enable it only behind approved authentication and network controls.

## Can a Plugin access the CAP database?

No. Plugins use Platform interfaces, Sandbox, and Provider contracts.

## What is frozen in v1?

The 124-operation OpenAPI contract, SDK contracts, Plugin Manifest/capability contracts, and Playbook DSL `v1` semantics.

## Where is the complete v1 documentation map?

Start with [`docs/v1-documentation-index.md`](v1-documentation-index.md). It maps the recommended reading path for architects, developers, integrators, operations, and local evaluation.

## Can rc1 be overwritten?

No. Any correction after publication requires a new version such as `1.0.0-rc2`.
