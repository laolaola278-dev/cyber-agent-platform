BEGIN;

-- Running downgrade 20260730_0008 -> 20260730_0007

DROP TABLE report_knowledge;

DROP TABLE evidence_knowledge;

DROP TABLE asset_knowledge;

DROP TABLE knowledge_relations;

DROP TABLE knowledge_versions;

DROP TABLE knowledge;

DROP TABLE knowledge_sources;

UPDATE alembic_version SET version_num='20260730_0007' WHERE alembic_version.version_num = '20260730_0008';

COMMIT;

