BEGIN;

-- Running downgrade 20260731_0012 -> 20260731_0011

DROP TABLE incident_knowledge;

DROP TABLE incident_assets;

DROP TABLE incident_events;

DROP TABLE incident_findings;

DROP TABLE case_comments;

DROP TABLE investigation_cases;

DROP TABLE incident_artifacts;

DROP TABLE incident_timelines;

DROP TABLE incidents;

UPDATE alembic_version SET version_num='20260731_0011' WHERE alembic_version.version_num = '20260731_0012';

COMMIT;

