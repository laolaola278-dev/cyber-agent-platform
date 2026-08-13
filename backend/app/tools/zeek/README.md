# Zeek Adapter Boundary

Phase 13 accepts only platform-configured Zeek JSONL sources identified by `data_source_id`.
The adapter preserves source SHA-256, raw record SHA-256, line number and schema fingerprint in
lineage metadata. TSV is intentionally reserved and rejected until a future phase defines its
header/schema policy.
