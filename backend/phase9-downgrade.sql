BEGIN;

-- Running downgrade 20260731_0011 -> 20260731_0010

DROP TABLE event_knowledge;

DROP TABLE event_assets;

DROP TABLE event_evidence;

DROP TABLE event_references;

DROP TABLE security_events;

DROP TABLE detection_tasks;

DROP TABLE detection_capabilities;

DROP TABLE detection_plugins;

UPDATE alembic_version SET version_num='20260731_0010' WHERE alembic_version.version_num = '20260731_0011';

COMMIT;

