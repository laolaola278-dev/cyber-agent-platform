BEGIN;

-- Running downgrade 20260731_0010 -> 20260731_0009

DROP TABLE assessment_reports;

DROP TABLE finding_transitions;

DROP TABLE finding_comments;

DROP TABLE finding_history;

ALTER TABLE findings DROP CONSTRAINT ck_findings_ck_findings_status;

UPDATE findings SET status = 'OPEN' WHERE status IN ('NEW', 'TRIAGED', 'REOPENED');

UPDATE findings SET status = 'MITIGATED' WHERE status = 'FIXED';

UPDATE findings SET status = 'ACCEPTED' WHERE status = 'ACCEPTED_RISK';

ALTER TABLE findings ADD CONSTRAINT ck_findings_ck_findings_status CHECK (status IN ('OPEN', 'CONFIRMED', 'FALSE_POSITIVE', 'MITIGATED', 'ACCEPTED'));

UPDATE alembic_version SET version_num='20260731_0009' WHERE alembic_version.version_num = '20260731_0010';

COMMIT;

