# Phase 8 OWASP ZAP Reference, Integration, Security and Operations Analysis

## Reference Analysis

Official references used: OWASP ZAP API, Active Scan, Passive Scan, Alerts and Scan Policy documentation; `zaproxy/zap-api-python`; OWASP Top 10; OWASP ASVS; DefectDojo parser/deduplication architecture. ZAP is treated as an external stateful DAST engine. `zap-api-python` is a transport client, not a platform domain dependency. DefectDojo reinforces preserving scanner identity while normalizing/deduplicating. ASVS and OWASP Top 10 are classification knowledge, not scanner-native platform schemas.

## Tool Integration Analysis

```text
Asset -> AssessmentService -> AssessmentRuntime -> ZapAssessmentPlugin
      -> ZapAdapter -> ZapApiClient -> isolated ZAP Daemon
      -> Alert -> ZapResultNormalizer -> AssessmentResult
      -> Finding / Evidence / Knowledge / AssessmentReport / Audit
```

- Daemon: maintains ZAP Session/Context/scanner state.
- API client: wraps official synchronous client behind an async typed port.
- Adapter: creates and cleans Sessions, constrains Context, selects an allowlisted Scan Policy, invokes passive/spider/active operations, obtains Alerts and converts transport failures.
- Plugin: implements the unchanged six-stage lifecycle and never imports `zapv2` or subprocess APIs.
- Platform: owns Asset authorization, Policy, Capability, persistence, fingerprinting, Knowledge, reports and audit.
- Alert is converted to `RawFinding`/`Finding`; raw Alert remains only bounded metadata/evidence because mutable vendor schemas cannot become CAP database contracts.

## Security Boundary Analysis

1. Active Scan is default-deny because ZAP defines it as an attack.
2. API input accepts `asset_id`; target URL is derived only from an existing, non-deleted WEBSITE/APPLICATION Asset.
3. Active mode requires explicit request, explicit policy capability, an allowlisted active profile, Planner approval and Asset authorization metadata.
4. Context include regex is derived from the exact Asset origin/path; optional exclusions, spider depth, max URLs, max scan time, max requests and concurrency prevent scope expansion.
5. API key, isolated bind, disabled file transfer and disabled dynamic add-ons reduce daemon control-plane exposure.
6. Plugin receives no database, session, shell or arbitrary API client; it can only call the Adapter.
7. Unique temporary Session/Context names prevent cross-task state reuse; cleanup executes even on failure.
8. Mock-only tests neither start ZAP nor connect to internet targets.

## Operational Readiness Analysis

- Deployment: pin `zaproxy/zap-stable:2.17.0` or an internally mirrored digest; run as non-root with a read-only base filesystem and writable ephemeral ZAP home.
- Daemon: bind to loopback or isolated service network; require API key; disable API file transfer and unapproved add-on installation.
- Versioning: pin daemon image and `zaproxy` Python client; validate version/status before promotion.
- Upgrade: stage new image/client, run mock contract tests and authorized lab smoke tests, then blue/green switch.
- Rollback: retain previous image digest/client lock and revert service routing; Sessions are non-persistent.
- Resources: default profile CPU 1.0, memory 1024 MB, timeout 600 s; provider enforces hard limits.
- Health: `GET /assessment/zap/status` checks API version and reports profile; orchestration should remove unhealthy workers.
- Logs: collect daemon stdout/stderr and CAP Audit Events; redact API keys, credentials, request bodies and cookies.
- Recovery: abandon failed ephemeral Session, restart unhealthy daemon, retry only through workflow policy; never widen scope automatically.

## Architecture Trade-offs

- Plugin rather than core module: ZAP is replaceable external capability, so tool details stay outside the Assessment Framework.
- No Runtime changes: existing initialize/plan/execute/validate/normalize/shutdown lifecycle is sufficient and proves framework compatibility across CLI/stateless Nuclei and API/stateful ZAP.
- Unified Finding: cross-tool deduplication, state machine, Knowledge and reports require stable CAP semantics even when scanner fields differ.
- Daemon + API costs more operationally than one-shot CLI, but correctly models stateful Session/Context behavior and enables health/version governance.
- Sandbox profile currently declares provider constraints; production hard enforcement depends on Docker/remote-worker provider completion.
- Burp Enterprise can reuse the same shape: `BurpAssessmentPlugin -> BurpAdapter -> typed API client -> RawFinding`, while retaining Asset/Policy/Finding/Knowledge/Report/Audit unchanged.
