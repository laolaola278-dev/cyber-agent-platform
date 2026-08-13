BEGIN;

-- Running upgrade 20260731_0009 -> 20260731_0010

ALTER TABLE findings DROP CONSTRAINT ck_findings_ck_findings_status;

UPDATE findings SET status = 'NEW' WHERE status = 'OPEN';

UPDATE findings SET status = 'FIXED' WHERE status = 'MITIGATED';

UPDATE findings SET status = 'ACCEPTED_RISK' WHERE status = 'ACCEPTED';

ALTER TABLE findings ADD CONSTRAINT ck_findings_ck_findings_status CHECK (status IN ('NEW', 'TRIAGED', 'CONFIRMED', 'FALSE_POSITIVE', 'ACCEPTED_RISK', 'FIXED', 'REOPENED'));

CREATE TABLE finding_history (
    finding_id UUID NOT NULL, 
    actor VARCHAR(256) NOT NULL, 
    action VARCHAR(64) NOT NULL, 
    from_status VARCHAR(32), 
    to_status VARCHAR(32) NOT NULL, 
    reason TEXT, 
    snapshot JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_finding_history PRIMARY KEY (id), 
    CONSTRAINT fk_finding_history_finding_id_findings FOREIGN KEY(finding_id) REFERENCES findings (id) ON DELETE CASCADE
);

CREATE TABLE finding_comments (
    finding_id UUID NOT NULL, 
    author VARCHAR(256) NOT NULL, 
    body TEXT NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_finding_comments PRIMARY KEY (id), 
    CONSTRAINT fk_finding_comments_finding_id_findings FOREIGN KEY(finding_id) REFERENCES findings (id) ON DELETE CASCADE
);

CREATE TABLE finding_transitions (
    finding_id UUID NOT NULL, 
    from_status VARCHAR(32) NOT NULL, 
    to_status VARCHAR(32) NOT NULL, 
    actor VARCHAR(256) NOT NULL, 
    reason TEXT, 
    trace_id VARCHAR(64) NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_finding_transitions PRIMARY KEY (id), 
    CONSTRAINT fk_finding_transitions_finding_id_findings FOREIGN KEY(finding_id) REFERENCES findings (id) ON DELETE CASCADE
);

CREATE TABLE assessment_reports (
    assessment_task_id UUID NOT NULL, 
    plugin_id UUID NOT NULL, 
    asset_id UUID NOT NULL, 
    trace_id VARCHAR(64) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    summary JSON NOT NULL, 
    content JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_assessment_reports PRIMARY KEY (id), 
    CONSTRAINT fk_assessment_reports_assessment_task_id_assessment_tasks FOREIGN KEY(assessment_task_id) REFERENCES assessment_tasks (id) ON DELETE CASCADE, 
    CONSTRAINT fk_assessment_reports_plugin_id_assessment_plugins FOREIGN KEY(plugin_id) REFERENCES assessment_plugins (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_assessment_reports_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_assessment_reports_assessment_task_id UNIQUE (assessment_task_id)
);

CREATE INDEX ix_finding_history_finding_id ON finding_history (finding_id);

CREATE INDEX ix_finding_history_actor ON finding_history (actor);

CREATE INDEX ix_finding_history_action ON finding_history (action);

CREATE INDEX ix_finding_comments_finding_id ON finding_comments (finding_id);

CREATE INDEX ix_finding_comments_author ON finding_comments (author);

CREATE INDEX ix_finding_transitions_finding_id ON finding_transitions (finding_id);

CREATE INDEX ix_finding_transitions_from_status ON finding_transitions (from_status);

CREATE INDEX ix_finding_transitions_to_status ON finding_transitions (to_status);

CREATE INDEX ix_finding_transitions_actor ON finding_transitions (actor);

CREATE INDEX ix_finding_transitions_trace_id ON finding_transitions (trace_id);

CREATE INDEX ix_assessment_reports_assessment_task_id ON assessment_reports (assessment_task_id);

CREATE INDEX ix_assessment_reports_plugin_id ON assessment_reports (plugin_id);

CREATE INDEX ix_assessment_reports_asset_id ON assessment_reports (asset_id);

CREATE INDEX ix_assessment_reports_trace_id ON assessment_reports (trace_id);

CREATE INDEX ix_assessment_reports_status ON assessment_reports (status);

UPDATE alembic_version SET version_num='20260731_0010' WHERE alembic_version.version_num = '20260731_0009';

COMMIT;

