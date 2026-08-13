# Phase 6 Security Assessment Framework

## Architecture

```text
Workflow -> AssessmentPlanner -> Platform Capability
         -> AssessmentRegistry -> AssessmentRuntime
         -> AssessmentPlugin -> AssessmentResult
         -> ResultNormalizer -> Finding
         -> Evidence + KnowledgeVersion + Asset -> Report
```

The control plane owns policy, persistence, audit, deduplication, risk and reporting. A plugin owns only tool-specific planning/execution/validation/normalization inside the SDK contract.

## Security Boundary Analysis

### Allowed plugin permissions

- `assessment.execute`
- `tool.invoke`
- `evidence.write`
- `report.generate` (request capability only; no direct ReportService is exposed)

### Denied plugin permissions

- `shell.execute`
- `filesystem.write`
- `firewall.write`
- `agent.register`
- `workflow.modify`

The runtime context exposes IDs, trace ID, requested capabilities, validated policy, input and granted permission names. It does not expose SQLAlchemy Session, repositories, dispatcher, shell, filesystem, registry mutation or ReportService.

Asset policy is fail-closed: denylist wins; a non-empty allowlist requires explicit membership. Capability policy is an allowlist. Runtime enforces timeout and reported request-count limits. Max concurrency, rate limit and depth are supplied to plugins as hard plan limits; process/sandbox enforcement is deferred until a real adapter is approved in a later phase.

## Assessment Policy

Defaults are loaded from `backend/config/assessment.yaml`:

- maximum concurrency
- maximum request count
- rate limit per second
- scan depth
- timeout
- asset allowlist
- asset denylist
- capability allowlist

Per-task overrides are validated by Pydantic. Policy snapshots are persisted in AssessmentTask for reproducibility.

## Finding and deduplication

Finding is the platform's normalized vulnerability/weakness observation. Raw plugin output is not persisted as a Finding until cross-domain references are validated and risk is calculated.

The SHA-256 fingerprint contains stable scope and rule identity: Asset ID, plugin, tool, rule, tool unique ID, normalized title and affected asset. An existing original with the same fingerprint causes the incoming Finding to retain its own record but set `duplicate_of_id`.

## Risk Engine

`RiskEngine` is a Protocol. The default `RuleBasedRiskEngine` combines:

- Finding severity baseline
- Asset criticality bonus
- Knowledge CVSS as a score floor
- known-exploited/KEV bonus

It emits LOW/MEDIUM/HIGH/CRITICAL, score 0–10 and reasons. No AI is used.

## Architecture Trade-off Analysis

### Domain extension versus duplicated Task

Chosen: one-to-one AssessmentTask -> Task. This preserves common orchestration/audit identity while allowing assessment-specific policy, plan, plugin and result summary. A separate unrelated task table would fork lifecycle semantics.

### Platform Capability versus assessment-only identity

Chosen: AssessmentCapability references Capability. Platform Capability remains the source of truth for governance; assessment projection records which plugin provides it. Duplicating capability strings would create routing drift.

### In-memory AssessmentResult versus persistence

Chosen: AssessmentResult is a DTO. Persisting tool-neutral Finding avoids retaining transient plugin formats as a second source of truth.

### Keep duplicates versus discard

Chosen: retain and link duplicates. This preserves task and temporal provenance while concentrating remediation on the original. Cross-tool global deduplication is deferred.

### Runtime in process versus external workers

Chosen for Phase 6: narrow in-process contract with Fake Plugin only. External workers/containers are required before approving real scanners.

## Data Model Evolution Analysis

Phase 6 adds a bounded Assessment context without changing existing primary entities:

- `assessment_tasks` extends `tasks` one-to-one.
- `assessment_plugins` stores governed plugin definitions.
- `assessment_capabilities` projects platform capabilities by plugin.
- `findings` stores normalized results and duplicate lineage.
- `finding_references` normalizes references.
- `finding_evidence` fixes Evidence provenance.
- `finding_knowledge` fixes both stable Knowledge and immutable KnowledgeVersion.
- `finding_assets` links one Finding to one or more canonical Assets.

Existing Evidence, Knowledge, Asset and Report remain sources of truth. ReportService reads Findings for the task; plugins never write reports.

## Technical Debt

- Real plugin isolation, process sandboxing, egress enforcement and operating-system resource limits are not implemented.
- Scheduler is an explicit reserved port only.
- Max concurrency/rate/depth cannot be independently measured by the platform until an adapter execution proxy exists.
- Cross-tool semantic deduplication and Finding lifecycle mutation APIs are deferred.
- Report rendering currently uses generic templates and only adds Finding JSON data.
