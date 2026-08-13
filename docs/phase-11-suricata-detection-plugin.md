# Phase 11 Architecture: Suricata Detection Plugin

## 1. Scope and acceptance boundary

Phase 11 integrates Suricata 8.0.6 as CAP's first real Detection Plugin. It validates the existing Detection Framework without modifying its Runtime, Planner, Registry, Service, Normalizer, SecurityEvent model or correlation engine.

The supported path is:

```text
controlled EVE JSONL source
  -> SuricataAdapter
  -> SuricataDetectionPlugin lifecycle
  -> SuricataResultNormalizer
  -> DetectionResult
  -> platform DetectionResultNormalizer
  -> SecurityEvent persistence
  -> RuleBasedCorrelationEngine
  -> IncidentCorrelation candidate
```

An Incident is not automatically created by this path.

## 2. GitHub and official reference analysis

The integration is based on Suricata's documented EVE JSON model and rule identity fields. The implementation uses:

- common envelope fields: `timestamp`, `event_type`, `flow_id`, protocol and endpoint fields;
- alert fields: action, signature, `signature_id`, GID, revision, category and severity;
- telemetry families: flow, stats, DNS, HTTP, TLS and fileinfo;
- rule metadata identifiers and external references for ATT&CK, CAPEC and CVE mapping.

The platform deliberately does not mirror every Suricata EVE field. The Adapter validates the envelope, while the Normalizer selects stable fields and emits the platform-neutral `RawSecurityEvent` contract.

## 3. Sigma, TheHive and Wazuh comparison

Sigma metadata reinforces the need to keep severity, tags and references as bounded metadata rather than making a vendor rule format part of the Detection Framework. TheHive's Alert, Observable and Case concepts support the CAP boundary that an incoming detection is a signal and an observable source for investigation, not itself an Incident. Wazuh's decoder and correlation pipeline demonstrates why parsing and multi-event correlation are separate responsibilities; CAP keeps parsing in the Tool Adapter, cross-event grouping in the platform, and Incident lifecycle in the Incident context.

These projects are reference points only. No Sigma, TheHive, Wazuh, Elastic or Splunk integration is included in Phase 11.

## 4. Tool integration analysis

`SuricataAdapter` owns:

- configured source resolution;
- filesystem existence and extension checks;
- bounded reads;
- JSONL parsing;
- EVE event-type allowlisting;
- required timestamp and Alert envelope validation;
- operational status reporting.

`SuricataDetectionPlugin` owns only lifecycle coordination and identity checks. It receives a least-privilege `DetectionPluginContext`, accepts a source identifier, and returns a `DetectionResult`. It has no Session, Repository, JSON parser, file API or Incident service.

`SuricataResultNormalizer` maps rule identity (`gid:sid:rev`), severity, action, category, signature, flow, protocol, endpoints, bounded event details, ATT&CK/CAPEC/CVE identifiers and canonical references. The platform normalizer then applies the common attribute and fingerprint policy.

## 5. Security boundary analysis

### Input boundary

The client cannot submit `path` or `log_path`. Pydantic rejects extra fields, and the endpoint accepts only `data_source_id`. The configured source path is resolved by dependency injection from YAML.

### File and parser boundary

Only configured `.json` and `.jsonl` sources are readable. The Adapter rejects unknown sources, empty identifiers, missing files, oversized input, excessive records, invalid JSON, non-object records, missing event types, missing timestamps, unsupported event types and alert records without Alert metadata.

### Runtime boundary

The sandbox profile specifies CPU, memory, timeout, input bytes, record count, read-only filesystem policy, no network policy and the `eve.read` permission. The Detection Runtime separately enforces plugin permissions, timeout, maximum events, event size, metadata size, sampling and rate limits.

### Domain boundary

The Plugin cannot access the database, create an Incident, transition an Incident, invoke shell execution or write files. DetectionService alone owns persistence, Asset/Evidence/Knowledge validation, correlation and audit. IncidentCorrelation creates candidates only; IncidentService owns creation and lifecycle transitions.

### Data minimization

Raw nested EVE blocks are not placed in `SecurityEvent.attributes`. Only explicitly allowed scalar fields and standard references survive. Full raw telemetry, if operationally needed later, must be governed as Evidence/object storage and must not weaken the SecurityEvent contract.

## 6. API and operational behavior

- `POST /detection/suricata`: creates and optionally executes a DetectionTask against an allowlisted source.
- `GET /detection/suricata/status`: returns version, EVE format, source availability and sandbox limits without paths.
- `GET /detection/events/{id}`: existing platform event detail endpoint; no duplicate Suricata-specific event model is introduced.

The endpoint is intended for controlled ingestion and test fixtures in Phase 11. It does not start Suricata, open an interface, capture packets or scan a target.

## 7. Operational readiness

### Deployment

Run Suricata as a separately managed, read-only telemetry producer. Stage EVE JSONL into a platform-controlled source directory or fixture location. Mount only the required source path into the CAP worker with read-only permissions. Do not mount the host root, arbitrary log directories or credentials.

### Resource controls

The default profile is 0.5 CPU, 256 MB memory, 30 seconds, 5 MB input and 1,000 records. DetectionPolicy supplies a separate 60-second default task timeout and 1,000-event platform limit. Production values must be sized from event rate and deployment capacity rather than silently increased.

### Rotation and retention

Suricata EVE rotation should be performed by the producer or a dedicated staging process. CAP reads bounded, configured files and should not delete or rotate producer-owned files. Retention of normalized SecurityEvents follows the DetectionPolicy; raw Evidence retention requires a separate governed policy.

### Health and observability

Use the status endpoint for source availability and profile visibility. Audit events include DetectionTaskCreated, DetectionExecutionStarted, DetectionResultNormalized, SecurityEventCreated and SecurityEventsCorrelated. Operational logs must use request/trace identifiers and must not disclose source filesystem paths or raw event payloads.

### Versioning and rollback

The tool version is pinned to Suricata 8.0.6 in typed configuration and manifests. Upgrade by changing the deployment/tool manifest and running the fixture and compatibility suite first. Roll back by restoring the previous Suricata producer and Adapter/Normalizer version; the stable SecurityEvent contract remains the rollback boundary.

## 8. Architecture trade-offs

### EVE JSONL versus multiple ingestion transports

EVE JSONL has a smaller and more deterministic trust boundary than syslog, socket, pcap or downstream vendor APIs. The trade-off is that CAP does not consume those transports directly in Phase 11.

### Adapter versus Plugin parsing

Keeping parsing in the Adapter prevents tool schema and file access from leaking into Plugin lifecycle code. The trade-off is one additional integration layer, accepted as the anti-corruption boundary.

### Bounded projection versus raw payload preservation

Bounded projection protects the stable platform model and reduces payload risk. The trade-off is loss of tool-native detail in SecurityEvent; raw retention, if needed, must be implemented as governed Evidence later.

### Candidate versus automatic Incident creation

Generating an IncidentCandidate preserves analyst and policy control and prevents a detection tool from owning another domain's lifecycle. The trade-off is an explicit downstream correlation/approval step.

## 9. Known non-goals

- no Zeek, Elastic, Splunk, Wazuh or Sigma runtime integration;
- no live Suricata process management;
- no packet capture or active scanning;
- no automatic Incident creation;
- no arbitrary path ingestion;
- no database access from Plugin or Adapter;
- no new Suricata-native persistence tables in Phase 11.
