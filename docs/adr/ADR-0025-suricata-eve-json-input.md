# ADR-0025: EVE JSONL Is the Unified Suricata Input Boundary

- Status: Accepted
- Date: 2026-08-01

## Context

Suricata can expose information through several operational paths, including EVE JSON files and downstream log pipelines. Supporting syslog text, UNIX sockets, packet capture, Elasticsearch documents or vendor-specific transformed payloads in the first integration would expand the trust boundary, create parser ambiguity and couple CAP to deployment topology.

EVE JSON provides structured common fields such as timestamp, event type and flow identity, plus typed alert, flow, stats, DNS, HTTP, TLS and file information. It also preserves alert GID, SID, revision, signature, category, severity and rule metadata needed by CAP normalization.

## Decision

CAP accepts newline-delimited Suricata EVE JSON as the sole Phase 11 input format. The boundary is enforced as follows:

- clients submit only a platform-configured `data_source_id`;
- the Adapter resolves that identifier against an allowlist and permits only existing `.json` or `.jsonl` files;
- input bytes and record count are bounded before normalization;
- each non-empty line must be a JSON object with timestamp and an allowlisted event type;
- alert records must include an alert object;
- only alert, flow, stats, DNS, HTTP, TLS and fileinfo event families are accepted;
- nested tool-native payloads are projected to bounded scalar attributes and references rather than persisted wholesale;
- the Adapter status API reports source availability without disclosing filesystem paths.

## Consequences

- Input parsing is deterministic, auditable and fail-closed.
- CAP is independent from Suricata process lifecycle and live network capture.
- Arbitrary client filesystem access, socket access and network access are excluded.
- Downstream transports may stage validated EVE JSONL into an approved source, but CAP does not accept their native formats in Phase 11.
- Schema changes are contained in the Adapter and Suricata Normalizer instead of changing the Detection Framework.
