# CAP v1 Architecture

CAP is a platform-first security orchestration control plane. FastAPI exposes governed APIs; PostgreSQL is the durable source of truth; Redis supports coordination; Workers lease and execute tasks through Sandbox and Provider interfaces; Plugins implement declared capabilities; Playbooks orchestrate existing capabilities; React provides the Console.

## Planes and boundaries

- Platform governance: Registry, Capability, RBAC, Approval, Audit, Observability.
- Domain frameworks: Asset, Knowledge, Assessment, Detection, Incident/Case, Response, Notification/Ticket, Telemetry, Playbook.
- Execution: Worker, Lease/Fencing, Sandbox, Secret Provider, Provider Adapters.
- Presentation: Web Console and stable v1 API.

Security is fail closed. The trusted gateway authenticates users; Backend RBAC authorizes every protected request. Plugins do not own platform persistence. Database state is authoritative over Worker caches. High-impact response actions require approval and durable audit evidence.

Architecture decisions are recorded under `docs/adr/`. Phase 23 adds no Plane or domain component; its Helm, CI, Docker, and documentation assets package the existing architecture.
