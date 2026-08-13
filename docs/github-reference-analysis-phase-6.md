# Phase 6 Official Architecture Reference Analysis

## Scope and safety

This analysis uses official project documentation only. CAP does not copy scanner engines, templates, payloads, exploit logic, or active-response behavior. No real scanner is integrated and no target is scanned in Phase 6.

## OWASP ZAP

Official references: ZAP Getting Started and Active Scan Rules documentation.

- ZAP separates proxy/session state, passive analysis, active scan rules, alerts, policies, API and add-ons.
- Scan rules have stable alert identifiers; risk and confidence are distinct dimensions.
- Active rules can mutate requests, inject payloads, perform time-based checks or use out-of-band callbacks. They therefore require explicit authorization and resource limits.
- Add-ons extend behavior independently of the core.

CAP adopts: plugin isolation, explicit policy, separate severity/confidence, stable rule identity, evidence-backed normalized results.

CAP rejects: embedding active scan logic, allowing a plugin to decide scope, and coupling scanner alerts directly to reports or databases.

## Nuclei

Official references: Template Introduction, Matchers, Extractors and Workflow documentation.

- YAML templates describe protocol requests and detection metadata.
- Matchers determine whether response conditions hold; extractors return data and may feed later steps.
- Extracted data is not inherently a vulnerability. It requires matcher context and normalization before becoming a Finding.
- Workflows support conditional execution and shared context, reducing unnecessary requests.
- Template flexibility is powerful but creates a trust boundary: templates can drive network activity and must not become unrestricted platform code.

CAP adopts: capability-driven plugin selection, conditional planning, explicit rule/tool metadata, validation before normalization.

CAP rejects: interpreting arbitrary templates in the control plane, treating extractor output as a Finding, and allowing templates to bypass policy.

## Greenbone Community Edition

Official reference: Greenbone Community Architecture.

- Scanner applications execute vulnerability tests.
- `gvmd` is the management plane: tasks, configuration, results, permissions, scheduling and PostgreSQL persistence.
- OSP separates manager and scanner; GMP separates user/API clients and manager.
- Results flow from scanner to manager rather than scanners owning the management database.

CAP adopts: manager/runtime/plugin separation, protocol-like SDK boundary, platform-owned persistence, scheduler port reservation.

CAP rejects: reproducing OpenVAS scanning and feed engines or placing scanner-specific concepts in CAP's core Finding model.

## DefectDojo

Official reference: Finding Deduplication documentation.

- Imported tool reports become Findings.
- Duplicate Findings retain context but point to one original Finding.
- Same-tool and reimport deduplication have different scopes and algorithms.
- Preferred identity is a tool unique ID; hash fields are the fallback and must be stable.
- Deduplication may be scoped by product/engagement/endpoint and may run asynchronously.

CAP adopts: normalized Finding as the platform boundary, tool ID plus stable hash fallback, `duplicate_of_id`, preservation of incoming context.

CAP rejects: discarding incoming matches before an auditable Finding exists and global cross-tool deduplication in Phase 6.

## Wazuh

Official reference: Vulnerability Detection — How it works.

- Endpoint agents collect OS and package inventory.
- The manager correlates inventory with normalized vulnerability intelligence.
- Vulnerability inventory is asset/agent scoped and supports Active/Solved state.
- Alerts are change events; they are not the same as current inventory.
- CTI normalization and endpoint association are separate responsibilities.

CAP adopts: Asset + Knowledge + Finding correlation, immutable knowledge-version provenance, distinct finding state and audit events.

CAP rejects: duplicating CTI ingestion in Assessment and coupling Findings to a Wazuh-specific agent schema.

## Consolidated CAP decisions

1. Assessment is a framework and governance layer, not a scanner.
2. Plugins return `AssessmentResult`; only the platform creates Findings.
3. AssessmentTask extends the generic Task lifecycle one-to-one.
4. AssessmentCapability references the platform Capability source of truth.
5. Finding is tool-neutral, evidence-backed, knowledge-version-pinned and asset-linked.
6. Policy is checked before plugin selection and again after execution limits are reported.
7. Fake Plugin is the only Phase 6 executable plugin and performs no I/O.
