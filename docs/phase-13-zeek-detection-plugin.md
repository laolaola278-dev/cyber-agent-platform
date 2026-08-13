# Phase 13 Zeek Detection Plugin

## Architecture references

| Reference | Adopted idea | CAP application |
|---|---|---|
| Zeek JSON logs | One JSON object per line; originator/responder fields | Bounded JSONL adapter and direction mapping |
| Zeek Package Manager | Packages/scripts may add fields and logs | Minimum required-field validation plus tolerant extra fields |
| Corelight open source conventions | Preserve Zeek-native `uid`, `fuid`, log identity and network roles | Stable tool identity and allowlisted field projection |
| Sigma | Rule metadata and field mapping remain decoupled from storage | `note`, ATT&CK, CAPEC, CVE and URLs mapped as metadata/references |
| Elastic Common Schema | Normalize source/destination/protocol/URL/hash concepts | CAP-neutral SecurityEvent attributes without changing core schemas |

## Compatibility matrix

| Zeek log | Phase 13 | Event type | Key preserved fields | Limits |
|---|---:|---|---|---|
| `conn.log` | Yes | `network.zeek.conn` | uid, endpoints, ports, proto, service, bytes, state | Observation severity INFO |
| `dns.log` | Yes | `network.zeek.dns` | uid, query, qtype, rcode, answers | Nested/extended fields excluded |
| `http.log` | Yes | `network.zeek.http` | method, host, uri, user agent, status, lengths | Bodies are never persisted |
| `ssl.log` | Yes | `network.zeek.ssl` | version, cipher, SNI, subject, issuer, validation | Certificate blob excluded |
| `files.log` | Yes | `network.zeek.files` | fuid, uid, MIME, filename, size, hashes | File content excluded |
| `notice.log` | Yes | `network.zeek.notice` | note, msg, src, dst, port, actions | Heuristic severity only |
| Zeek JSONL | Yes | Adapter input | Per-line object | Configured source only |
| Zeek TSV | Reserved | None | Explicit parser boundary | Fail-closed in Phase 13 |
| Package-added scalar fields | Tolerated | None by default | Schema fingerprint records drift | Not persisted until allowlisted |
| Package-added logs | No | None | Source remains available as Evidence | Must be explicitly allowlisted |

## Evidence preservation analysis

The configured source file remains the authoritative evidence object outside the Plugin. The Adapter
calculates source SHA-256 before parsing and per-line raw-record SHA-256 from the exact trimmed JSONL
line. Each TelemetryRecord carries source ID, line number, source hash, record hash and a schema
fingerprint. The Detection normalizer persists only this lineage plus bounded, allowlisted fields.
Raw lines, arbitrary nested maps, HTTP bodies, certificate blobs and file bodies are not copied into
SecurityEvent. Existing `evidence_ids` remain available when an Evidence object is created by an
application service; the Plugin never accesses the Evidence database.

## Security boundary analysis

- Client input is `data_source_id`; arbitrary paths are rejected by Pydantic and Adapter allowlist.
- Source access is read-only, byte/record bounded, extension constrained and network-free.
- Zeek Telemetry Plugin has only `telemetry.receive` and `telemetry.publish`.
- Zeek Detection Plugin has only `detection.execute` and `evidence.read`.
- Detection Plugin contains no Adapter, session, repository, DetectionService or IncidentService.
- DetectionService alone persists SecurityEvent; Incident remains candidate/correlation-only.
- Unexpected log types, missing required identities, invalid JSON and TSV fail closed.

## Operational readiness analysis

- Version pin: Zeek `7.0.0` in typed configuration and tool manifest.
- Rotation: a rotated file must be registered as a new/updated source identity; source SHA-256 makes
  replacement visible.
- Schema drift: every record contains a field-name fingerprint; additive package fields are tolerated
  but not persisted automatically.
- Backpressure: existing Telemetry limits validate record count and encoded record size. Future broker
  deployments can reuse Checkpoint/Replay without changing the Detection Plugin.
- Upgrade: validate fixtures and compatibility matrix against the target Zeek version/package set,
  then update the pin.
- Rollback: restore previous version/configuration; no migration rollback is needed.
- Health: `GET /detection/zeek/status` reports version, supported logs, source availability and limits
  without exposing paths.

## Architecture trade-offs

1. In-process bridge avoids framework changes and proves composition, but does not create a durable
   TelemetryTask for the convenience API. Broker-backed durable transfer is future work.
2. Tolerant extra fields support Zeek packages, while explicit projection prevents schema drift from
   silently expanding SecurityEvent storage.
3. One source may contain multiple log kinds for controlled fixtures. Production can register one
   source per rotated log file without changing contracts.
4. Notice severity uses deterministic heuristics; deployment-specific mappings should be delivered as
   configuration/package adapters rather than core Detection changes.
5. Hash lineage provides integrity and traceability, but source retention policy remains an operational
   responsibility of the Evidence subsystem/deployment.
