BEGIN;

-- Running downgrade 20260731_0009 -> 20260730_0008

DROP TABLE finding_knowledge;

DROP TABLE finding_assets;

DROP TABLE finding_evidence;

DROP TABLE finding_references;

DROP TABLE findings;

DROP TABLE assessment_tasks;

DROP TABLE assessment_capabilities;

DROP TABLE assessment_plugins;

UPDATE alembic_version SET version_num='20260730_0008' WHERE alembic_version.version_num = '20260731_0009';

COMMIT;

