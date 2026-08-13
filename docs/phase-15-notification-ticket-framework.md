# Phase 15 Notification & Ticket Framework Architecture and Assurance Analysis

## 1. Scope

Phase 15 establishes platform-level notification and ticket governance. It is not an Email sender, Slack bot, Jira client, ServiceNow client, webhook utility, or template execution engine. External systems remain replaceable Notification Plugins behind platform policy, routing, runtime, verification, evidence and audit boundaries.

## 2. Official Reference Analysis

| Reference | Reusable idea | CAP decision | Deliberately not copied |
|---|---|---|---|
| TheHive | Alerts are triaged into cases; cases contain tasks and evidence-oriented investigation context | Incident remains authoritative; notification carries an immutable Incident reference; Ticket is a neutral follow-up object | TheHive Case, Alert and Task schemas and UI workflow |
| StackStorm | Trigger, Rule, Action and route are decoupled; completion notifications can use route-specific actions | Planner/policy/routing/runtime/plugin are separate; sending is a governed action | StackStorm runner, pack and ChatOps implementation |
| Grafana Alerting | Contact points define destinations; notification policies route labels through a policy tree; templates are reusable | Recipient groups resemble governed contact points; routes map severity/priority/capability to group/template | Grafana label matcher syntax and UI provisioning format |
| Prometheus Alertmanager | Grouping, deduplication, silences, routing and inhibition reduce alert storms | Deduplication, silence, rate-limit and route decisions happen before plugins | Alertmanager clustering and Prometheus alert wire format |
| Jira Cloud REST | Issue creation is separate from workflow transitions; transitions depend on workflow and permissions | CAP Ticket uses a stable neutral model; Jira remains an adapter that maps fields/transitions and external references | Jira Issue schema, transition IDs and workflow internals |

Verified official sources:

- https://docs.strangebee.com/thehive/user-guides/analyst-corner/alerts/alerts-description/actions/
- https://docs.stackstorm.com/chatops/notifications.html
- https://grafana.com/docs/grafana/latest/alerting/fundamentals/notifications/
- https://prometheus.io/docs/alerting/latest/alertmanager/
- https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/

## 3. Architecture

```text
Incident / optional Response Plan (read-only references)
  -> NotificationService
  -> NotificationPlanner
  -> RoutingEngine
  -> NotificationPolicyEngine
       - severity / priority
       - business hours
       - recipient group + allowlist
       - rate limit / deduplication / silence / escalation
  -> TemplateProvider (declarative scalar substitution only)
  -> NotificationRuntime
  -> Certified NotificationPlugin
  -> NotificationResult + Verification
  -> NotificationEvidence + Audit
```

Notification is independent because destination delivery has its own failure model, sensitive-data boundary, retry/storm controls, recipient governance and evidence requirements. Coupling it to Incident or Response would grant delivery adapters excessive authority and would make external outages mutate core security state.

Template is independent because content representation changes faster than routing or delivery. A provider contract permits future Jinja2/MJML adapters only if they preserve the no-code, allowlisted-variable boundary.

Ticket is unified because Jira, ServiceNow, TheHive tasks and other external systems have incompatible fields and transitions. CAP preserves a stable internal model and stores `external_reference`; plugins perform vendor mapping without leaking vendor schemas into Incident.

## 4. Security Boundary Analysis

1. API: strict Pydantic models reject unknown fields and direct provider controls.
2. Domain: Incident and Response Plan are read-only foreign-key references; Notification Plugin receives no domain service.
3. Recipient: callers select only a configured group/route; final recipients must be members of the policy allowlist.
4. Policy: capability, severity, priority, hours, silence, deduplication, rate and escalation are enforced before plugin invocation.
5. Template: only `{{ scalar_name }}` substitution is permitted. Blocks, comments, calls, brackets, dunder names and attribute traversal are rejected. No `eval`, `exec`, import, function, object or expression execution exists.
6. Runtime: only Runtime invokes lifecycle methods; permission identity, recipient immutability, result identity/capability, evidence count, result size, JSON serializability and verification are fail-closed.
7. Plugin: no AsyncSession, repository, IncidentService, ResponseService, ReportService, shell, filesystem write or arbitrary recipient authority.
8. Evidence/Audit: plugins return bounded descriptors; platform persists lineage and publishes events transactionally.

Plugin cannot modify Incident because notification transport success does not prove incident resolution. A compromised or buggy transport must never close, reclassify, assign or otherwise alter a security record. The platform owns domain transitions through their dedicated services.

Recipient allowlisting is mandatory because a free-form address or endpoint turns a notification API into a data-exfiltration primitive. Policy-controlled groups support review, rotation and audit while keeping raw recipients outside untrusted plugin choice.

## 5. Safety Case

| Hazard | Control | Evidence |
|---|---|---|
| Incorrect notification | Strict route/template/capability validation; deterministic plan snapshot | Plan and audit event |
| Duplicate notification | Deduplication key and bounded time window | `SUPPRESSED` plan and reason |
| Notification storm | Global rate window, grouping-ready route layer, silence rules | Policy snapshot and suppression event |
| Sensitive data leakage | Recipient allowlist, scalar-only template variables, no arbitrary endpoint | Read-only context and route snapshot |
| Template code execution | Restricted parser; no expression runtime; malicious syntax tests | Template security tests |
| Plugin changes Incident/Response | No service/session/repository capabilities and immutable IDs | Certification rejection matrix and invariance tests |
| False success | Verification required for every certified plugin and successful result | Execution verification status and receipt evidence |
| External outage | Failure persisted and audited without mutating Incident/Response | `FAILED` execution and audit event |

## 6. Compatibility Matrix

| Plugin | Phase 15 status | Send acceptance | Verification rule | Network | Production-ready |
|---|---|---|---|---|---|
| Synthetic | Implemented/certified | Deterministic in-memory accepted receipt | Receipt exists and matches plan/recipient scope | None | No; framework certification only |
| Email | Contract-compatible stub target | Provider accepted/message ID | Provider API accepted state or delivery receipt when available | Allowlisted provider endpoint | No adapter shipped in Phase 15 |
| Webhook | Contract-compatible stub target | HTTP request completed | HTTP 2xx plus bounded response metadata | Allowlisted HTTPS endpoint | No adapter shipped in Phase 15 |

Future Chat, SMS, Jira, ServiceNow, Feishu, DingTalk and WeCom integrations must pass the same lifecycle and certification controls.

## 7. Architecture Trade-offs

- In-process registry is deterministic and testable, but distributed discovery and signed package loading remain future work.
- Rate limiting is database-query based and globally scoped; a production distributed deployment should use atomic Redis counters with tenant/route dimensions.
- Deduplication currently suppresses only after a verified/sent predecessor. Group aggregation and inhibition are extension points, not Phase 15 implementations.
- Verification means provider acceptance, not human reading or business outcome. Delivery/read receipts require provider-specific plugins.
- The template grammar is intentionally less expressive than Jinja2; safety and auditability take precedence over presentation power.
- Automatic send in `POST /notifications` provides the requested API while preserving a persisted Plan first; a future asynchronous worker can consume planned records without changing the domain model.

## 8. Notification Plugin Certification Checklist

- [ ] Unique non-empty name/version and supported `notification.*` capability.
- [ ] Implements `initialize/render/validate/send/verify/shutdown/health`.
- [ ] Declares exactly allowlisted lifecycle permissions.
- [ ] No DB/session/repository or Incident/Response/Report mutation service.
- [ ] No shell, filesystem write, dynamic import or template execution.
- [ ] Sandbox compatible and operational documentation supplied.
- [ ] Never changes plan identity, Incident/Response reference or recipients.
- [ ] Sends only to Runtime-provided allowlisted recipients.
- [ ] Returns bounded, JSON-serializable `NotificationResult`.
- [ ] Success requires verifiable provider acceptance.
- [ ] Evidence contains SHA-256, reference and non-secret metadata.
- [ ] Failure and timeout leave domain state unchanged and permit platform audit.
- [ ] Email mapping documents message-ID/acceptance semantics.
- [ ] Webhook mapping enforces allowlisted HTTPS destinations and HTTP 2xx verification.
- [ ] Ticket mapping documents field/status/priority and external-reference mapping.
