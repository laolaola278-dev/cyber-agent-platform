BEGIN;

-- Running downgrade 20260801_0013 -> 20260731_0012

DROP TABLE telemetry_runtime_states;

DROP TABLE telemetry_checkpoints;

DROP TABLE telemetry_tasks;

DROP TABLE telemetry_pipelines;

UPDATE alembic_version SET version_num='20260731_0012' WHERE alembic_version.version_num = '20260801_0013';

COMMIT;

