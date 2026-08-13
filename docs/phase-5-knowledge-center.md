# Phase 5 Knowledge Center

## Architecture

Knowledge Center is CAP's only trusted security knowledge write boundary. Assessment, Detection, Response, Sandbox, Evidence, and Report consumers query the center; Agents do not maintain private vulnerability databases.

Flow:

```text
Official/Vendor Source -> Provider -> Importer Plugin -> KnowledgeImporter
  -> KnowledgeSource + Knowledge + KnowledgeVersion + KnowledgeRelation
  -> Audit Events
  -> Asset/Evidence/Report version-pinned links
```

## Identity and version semantics

Stable identity is `(source_id, knowledge_type, external_id)`. Source namespaces prevent an unreviewed vendor object from silently replacing an authoritative object with the same textual identifier. `Knowledge.current_version` and `Knowledge.current_content_hash` materialize an unambiguous latest imported projection for fast reads. Every changed payload creates `KnowledgeVersion(version, content_hash, payload)`; existing snapshots are never updated or deleted by an import.

Idempotency is content-aware: importing the same source version and content hash is unchanged. If a publisher republishes the same version string with corrected content, CAP records a second immutable snapshot and updates the materialized projection.

## Supported types

CVE, CWE, CAPEC, CPE, ATTACK_TECHNIQUE, ATTACK_TACTIC, CISA_KEV, OWASP_CATEGORY, VENDOR_ADVISORY, IOC, and RULE_METADATA are built in. `KnowledgeRegistry.register_type()` permits extensions without modifying the persistence or importer core.

## Directed relationships

Built-in relations include `affects`, `maps_to`, `exploited_by`, `related_to`, `belongs_to`, and `derived_from`. The unique edge key is `(source_knowledge_id, target_knowledge_id, relation_type)`. Each edge stores `source_name` and properties. Self-edges are rejected.

Examples:

- CVE `affects` CPE
- CVE `maps_to` CWE
- CVE `exploited_by` CAPEC
- CVE `related_to` ATTACK_TECHNIQUE
- ATTACK_TECHNIQUE `belongs_to` ATTACK_TACTIC

## Provider and Importer boundaries

`KnowledgeProvider.records()` yields provider-neutral `KnowledgeRecord` objects. CVEProvider, AttackProvider, KEVProvider, and VendorProvider are protocols. Providers do not receive a database session.

Importer plugins decode formats. JSON is implemented; YAML, CSV, and ZIP can register the same `parse(payload, source)` contract. `KnowledgeImporter` owns validation, canonicalization, non-overwriting version persistence, relation resolution, transaction commit, and audit events.

## Search

Phase 5 uses portable case-normalized matching across external ID, title, and description with filters for type, source, and status. This behaves consistently in PostgreSQL and SQLite tests. PostgreSQL `tsvector`/GIN or OpenSearch can later become a rebuildable read projection without changing domain writes.

## API

- `GET /knowledge`
- `GET /knowledge/{id}`
- `POST /knowledge/import`
- `GET /knowledge/search`
- `GET /knowledge/cve/{id}`
- `GET /knowledge/cwe/{id}`
- `GET /knowledge/attack/{id}`

Example import:

```json
{
  "source": "cvelistV5",
  "provider": "cve",
  "format": "json",
  "payload": {
    "records": [
      {
        "knowledge_type": "CVE",
        "external_id": "CVE-2026-1234",
        "version": "5.2.0:2026-07-30T12:00:00Z",
        "title": "Example vulnerability",
        "description": "Normalized description",
        "references": ["https://example.com/advisory"],
        "status": "ACTIVE",
        "attributes": {"cvss": 9.8}
      }
    ]
  }
}
```

Response:

```json
{
  "source": "cvelistV5",
  "imported": 1,
  "unchanged": 0,
  "relations": 0,
  "knowledge_ids": ["8f100bf7-8a80-4f7f-a088-f67de184cbf9"]
}
```

## Cross-domain provenance

`AssetKnowledge`, `EvidenceKnowledge`, and `ReportKnowledge` store both stable Knowledge ID and the exact KnowledgeVersion ID. Report generation copies unique EvidenceKnowledge links for its task and embeds a normalized knowledge summary in report JSON. Later imports therefore do not rewrite historical report meaning.

## Security and audit

Importer payloads are Pydantic-validated. Source mismatches, unsupported type/relation/format, disabled source, missing relation target, and self-relations fail closed. Providers are expected to use public or explicitly authorized sources. Imports and version/relationship/link creation publish normalized audit events through the injected event bus.

## Architecture trade-off analysis

### Unified center versus Agent-private knowledge

A unified center provides one identity, lifecycle, trust boundary, audit trail, and relationship graph. Agent-private copies cause semantic drift, stale versions, inconsistent severity, and unreviewable updates. The trade-off is a shared dependency; this is mitigated through cached read models and version-pinned links.

### Relational source of truth versus graph database

PostgreSQL preserves transactional version creation, uniqueness, referential integrity, migrations, and existing CAP operations. Deep graph traversal is less efficient than a native graph store. CAP therefore keeps relational writes and leaves Graph Projection as a future replaceable read model.

### Provider plus Importer versus source-specific services

The split keeps network/source behavior outside the domain transaction and makes decoding formats independently extensible. It adds interfaces and normalization work, but prevents each source from bypassing version and audit rules.

### Explicit ReportKnowledge versus Evidence-only derivation

Explicit links make immutable reports independently queryable, survive evidence retention changes, and pin the version used at generation. The storage duplication is intentional provenance, protected by unique constraints.

## Data model evolution analysis

Phase 5 adds a Knowledge bounded context beside Asset and Runtime. Assets describe what CAP governs; Knowledge describes reusable security facts; Evidence records observations; Reports freeze conclusions. Workflow and Capability consumers reference Knowledge through services rather than embedding source datasets. Assessment can map findings to CVE/CWE/CPE, Detection can map rule metadata and IOC to ATT&CK, Response can retrieve KEV/vendor action guidance, and Sandbox can select scenarios from CAPEC/ATT&CK mappings. No existing Task or Workflow column is made mandatory, preserving Phase 1-4 compatibility.

## Technical debt and evolution

- Add official CVE JSON, ATT&CK STIX, KEV, and vendor Provider implementations.
- Add streaming and ZIP bomb/size-limit controls before accepting remote ZIP imports.
- Add source trust/confidence and conflict-resolution policy.
- Add PostgreSQL FTS/Graph projections through outbox/CDC.
- Add approval for non-authoritative manual knowledge publication.
