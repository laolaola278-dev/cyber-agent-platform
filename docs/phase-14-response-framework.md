# Phase 14 Response Framework Architecture and Assurance Analysis

## 1. Scope

Phase 14 establishes the platform framework for controlled response. It intentionally does not ship a
real WAF, firewall, Kubernetes, EDR, Linux or Windows response implementation. The only executable
plugin is synthetic and non-destructive.

## 2. GitHub and Official Reference Analysis

| Reference | Reusable idea | CAP decision | Not copied |
|---|---|---|---|
| Shuffle SOAR | Workflow connects triggers/actions; backend, apps and workers are separated | Response plugins are workflow-callable actions; Runtime remains execution authority | Shuffle deployment topology and app SDK |
| StackStorm | Action metadata is distinct from runner; executions have identity/results; inquiries support human input; immutable parameters protect security scope | Plugin/Runtime separation, persisted Plan/Execution, platform approval, immutable context | StackStorm pack/runner implementation |
| TheHive/Cortex | Responder consumes typed JSON, performs an action and emits a result; flavors declare accepted datatypes; TLP/PAP can refuse execution; reports preserve execution status | Typed plugin contract, object/capability matching, policy gate, explicit result/evidence | Cortex command runner and TheHive operations protocol |
| Wazuh Active Response | High-impact endpoint actions are risky; stateless and stateful responses exist; stateful actions can be reverted | Approval for impact, explicit rollback declaration/token/verification, safety case | Wazuh agent command protocol |
| OPA | Policy decision is decoupled from enforcement; structured input produces structured decision; undefined decisions can fail closed | `ResponsePolicyEngine` is PDP; Planner/Runtime are enforcement points; configuration-backed structured policy | Rego engine embedding in Phase 14 |

Verified sources:

- https://github.com/Shuffle/Shuffle
- https://docs.stackstorm.com/actions.html
- https://docs.stackstorm.com/inquiries.html
- https://documentation.wazuh.com/current/user-manual/capabilities/active-response/index.html
- https://www.openpolicyagent.org/docs/latest/
- https://docs.strangebee.com/cortex/api/how-to-create-a-responder/
- https://docs.strangebee.com/thehive/user-guides/analyst-corner/cases/observables/run-responders-on-an-observable/

## 3. Security Boundary Analysis

### Trust boundaries

1. API boundary: strict Pydantic request models reject unknown fields.
2. Domain boundary: Incident and Asset are read-only references; closed/resolved or deleted scopes fail closed.
3. Policy boundary: capability, Incident type/severity, Asset type, risk, business hours, maintenance window and automatic threshold are evaluated before plugin resolution.
4. Approval boundary: plan-scoped, expiring, multi-level-capable and separation-of-duties-aware.
5. Runtime boundary: only Runtime invokes plugin lifecycle methods; identity, permissions, scope, timeout, result size, evidence count and JSON serializability are enforced.
6. Plugin boundary: recursive read-only mappings, no DB/repository/domain/report service, explicit permission allowlist.
7. Evidence boundary: plugin returns bounded evidence descriptors; platform persists lineage.
8. Rollback boundary: opaque token is server-side only; rollback requires successful prior execution.

Forbidden plugin permissions include database access, Incident/Asset mutation, report writing, approval
decision, workflow mutation, shell execution and filesystem write. Missing policy, denied capability,
uncertified plugin, permission mismatch, mutated scope or invalid result all fail closed.

## 4. Safety Case Analysis

| Hazard | Preventive controls | Detection/verification | Recovery |
|---|---|---|---|
| False block | Incident/Asset scope validation, policy allowlist, approval, immutable parameters | plugin verify + platform identity/scope checks + audit | declared rollback with token and evidence |
| False isolation | high-impact capability approval, distinct approver, TTL | verification status and bounded result | verified rollback where supported |
| Accidental deletion | no delete capability in Phase 14; shell/filesystem permissions forbidden | certification and runtime permission checks | fail closed; no destructive synthetic behavior |
| Unsafe rule deployment | WAF/firewall/EDR denied until certified real plugins exist | manifest/certification, policy decision, health status | plugin-specific rollback required before enablement |
| Privilege escalation | minimal plugin context, no DB/services, runtime-exclusive invocation | malicious-scope and permission-mismatch tests | execution failure persistence and audit |
| Silent failure | explicit Result/Verification state; failed execution is committed before error propagation | FAILED execution plus `ResponseExecutionFailed` audit | operator replans or rolls back prior verified execution |

Safety claim for Phase 14: the included synthetic plugin cannot perform external network, shell,
filesystem, WAF, firewall, EDR or endpoint actions. Therefore tests validate governance mechanics without
creating a real operational side effect.

## 5. Interoperability Analysis

- Incident: supplies immutable incident identity, classification/source and severity; Response does not change Incident status.
- Asset: supplies immutable asset identities/types; Response does not change Asset fields.
- Detection/Telemetry: may lead to Incident creation or enrichment upstream; no direct plugin coupling.
- Workflow: can create/approve/execute through public APIs or service interfaces but cannot bypass Runtime.
- Capability Registry: stores the nine `response.*` capabilities using the existing platform registry.
- Knowledge: future policy/planner enrichment may consume knowledge through platform services, never from plugins directly.
- Evidence: Response owns `ResponseEvidence` lineage to execution/rollback and can later project references into platform reports.
- Report: Response does not let plugins write reports; report composition remains a platform responsibility.
- Audit: every plan decision, execution and rollback emits platform events.

## 6. Architecture Trade-off Analysis

- Separate Response domain vs Incident methods: chosen for lifecycle isolation and vendor neutrality; costs an extra aggregate and joins.
- Configuration policy vs embedded OPA: typed YAML is sufficient for Phase 14 and avoids a new runtime dependency; future OPA adapter can implement the same decision contract.
- In-process plugin vs worker/sandbox: in-process synthetic plugin makes framework tests deterministic; real high-impact plugins must use sandbox/worker isolation before certification.
- Plan snapshot vs live policy at execution: snapshot preserves what was approved; policy updates require a new plan to avoid scope drift.
- Opaque rollback token vs public token: server-side storage reduces leakage; operational backup and secret protection become deployment concerns.
- String actor vs identity object: preserves current platform compatibility; RBAC federation and cryptographic approval attribution remain future work.

## 7. Response Plugin Certification Checklist

A plugin is not production-certifiable unless every applicable item passes:

### Identity and manifest

- [ ] Stable unique name and pinned semantic version.
- [ ] Description, owner, license, source and operational documentation.
- [ ] Explicit capability list drawn only from registered `response.*` names.
- [ ] Health check and dependency readiness behavior.

### Permissions and isolation

- [ ] Permissions are a subset of `response.execute`, `response.verify`, `response.rollback`.
- [ ] No database, repository, Incident, Asset, Report, Approval or Workflow service reference.
- [ ] No undeclared shell, filesystem or network access.
- [ ] Compatible sandbox/worker profile for real external actions.
- [ ] Secrets are injected by the platform and never returned in results/evidence.

### Lifecycle and contract

- [ ] Implements `initialize/plan/validate/execute/verify/rollback/shutdown` correctly.
- [ ] Shutdown runs on success and failure.
- [ ] Does not mutate immutable Incident/Asset/capability/plugin/parameter scope.
- [ ] Returns only schema-valid, bounded, JSON-serializable `ResponseResult`.
- [ ] Result identity and capability match the approved plan.

### Approval and safety

- [ ] Declares approval support and action-specific risk.
- [ ] Documents false-positive/false-action hazards and blast radius.
- [ ] Defines idempotency, timeout, retry and partial-failure behavior.
- [ ] Provides a dry-run or synthetic fixture for certification.
- [ ] Denied/invalid/missing input fails closed with explicit errors.

### Verification, evidence and rollback

- [ ] Success requires independent verification evidence.
- [ ] Evidence has stable reference, SHA-256 and bounded metadata.
- [ ] Rollback support is declared truthfully.
- [ ] Rollback-capable actions return an opaque restoration token.
- [ ] Rollback is verified and produces separate evidence/audit.
- [ ] Non-reversible actions clearly return `NOT_SUPPORTED` and require enhanced approval.

### Testing and operations

- [ ] Unit, contract, lifecycle, timeout, permission and malicious-scope tests pass.
- [ ] Integration tests run only against an authorized non-production target.
- [ ] Monitoring, alerting, rate limits, kill switch and owner escalation are documented.
- [ ] Upgrade/downgrade and provider API compatibility are documented.
- [ ] Certification record is renewed after capability, permission, output or dependency changes.
