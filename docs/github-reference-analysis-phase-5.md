# Phase 5 Official Reference Analysis

## Scope and method

This analysis uses official project repositories, specifications, and publisher documentation. CAP borrows domain concepts and extension patterns; it does not copy the deployment architecture of a CTI product or let external formats become the internal domain model.

## OpenCTI

Official references:

- https://github.com/OpenCTI-Platform/opencti
- https://docs.opencti.io/latest/usage/data-model/
- https://docs.opencti.io/latest/deployment/connectors/

OpenCTI structures technical and non-technical threat intelligence as a knowledge graph based on STIX 2.1. Nodes represent entities; edges represent relationships. The model distinguishes STIX Domain Objects, Cyber Observables, and Relationship Objects, while extending STIX where product requirements need more object or relation types. Its connectors separate import, enrichment, stream, import-file, and export-file responsibilities. Import and enrichment connectors transform source data to STIX bundles before platform ingestion. Dedicated connector users and scoped tokens demonstrate that integration identities and permissions should be isolated.

CAP adopts: explicit directed relationships, source provenance, provider isolation, import/enrichment separation, and API-mediated writes. CAP does not adopt: OpenCTI's full STIX object hierarchy, GraphQL/Elastic/RabbitMQ deployment topology, or CTI-product-specific inference semantics.

## CVE List V5 and CVE JSON 5.x

Official references:

- https://github.com/CVEProject/cvelistV5
- https://github.com/CVEProject/cve-schema
- https://cveproject.github.io/cve-schema/schema/docs/

The repository is the official downloadable CVE List cache. Records are updated from CVE Services and are distributed as individual JSON records, daily baselines, and hourly deltas. A record can contain one CNA container, a CVE Program container represented as ADP, and multiple optional ADP containers. Consumers must process both CNA and CVE Program containers to obtain the Program's core record, and should expect duplicate references across containers. The `dataVersion` field, not publication date, selects the proper schema. Historical corrections demonstrate that downstream systems must retain source snapshots rather than overwrite prior state.

CAP adopts: stable CVE identity, schema-version-aware import, all-container preservation in version payloads, reference normalization, immutable import snapshots, and delta-friendly idempotency. CAP does not flatten CNA/ADP authority into one untraceable record.

## MITRE ATT&CK STIX Data

Official references:

- https://github.com/mitre-attack/attack-stix-data
- https://attack.mitre.org/resources/attack-data-and-tools/

ATT&CK is distributed as STIX 2.1 JSON collections for Enterprise, Mobile, and ICS. Each domain has versioned release collections and an unversioned latest bundle; a machine-readable collection index enables release discovery. Techniques are attack-pattern objects, tactics are represented through kill-chain phases and ATT&CK metadata, and STIX relationship objects connect techniques to mitigations, groups, software, and data components. STIX lifecycle fields such as modified, revoked, and ATT&CK deprecation metadata must be retained.

CAP adopts: external IDs such as T1059 as stable identities, collection release as import version, directed relationship preservation, and explicit ACTIVE/DEPRECATED/REVOKED status. CAP does not reimplement the complete ATT&CK Workbench editing model.

## CISA Known Exploited Vulnerabilities

Official references:

- https://www.cisa.gov/known-exploited-vulnerabilities-catalog
- https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
- https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities_schema.json

KEV is an authoritative prioritization input for vulnerabilities exploited in the wild. Catalog records are keyed by CVE and include vendor/project, product, vulnerability name, date added, short description, required action, due date, ransomware usage knowledge, notes, and CWEs. Catalog metadata contains title, catalog version, date released, and count. A KEV entry enriches a CVE; it does not replace the CVE record.

CAP adopts: KEV as a separate knowledge type related to CVE, catalog version/date as source version, and preservation of remediation deadlines and ransomware flags. CAP does not treat KEV membership as an Asset finding without Evidence.

## OWASP Top 10

Official references:

- https://owasp.org/www-project-top-ten/
- https://owasp.org/Top10/2025/
- https://github.com/OWASP/www-project-top-ten

OWASP Top 10 is an awareness and risk-category document, not a vulnerability feed. The 2025 release defines ten ranked categories. Categories aggregate classes of weaknesses and supporting risk analysis; they are not interchangeable with an individual CVE, CWE, finding, or exploit.

CAP adopts: release-scoped category identities such as A01:2025, category-to-CWE/CVE mapping relations, narrative risk guidance, and explicit versioning by release. CAP does not model an OWASP category as a concrete Asset vulnerability.

## CAP design conclusions

1. `Knowledge` is the stable source-scoped identity (`source`, `type`, `external_id`).
2. `KnowledgeVersion` is an immutable normalized snapshot identified by source version and content hash.
3. `KnowledgeSource` records provider identity and controlled configuration.
4. Providers fetch/parse external source semantics; Importers validate, normalize, version, relate, persist, and audit.
5. `KnowledgeRelation` is directed and source-attributed.
6. Asset, Evidence, and Report links pin a `KnowledgeVersion`, preserving historical meaning.
7. PostgreSQL is the source of truth. A future graph/search projection may be rebuilt from relational records without changing write semantics.
