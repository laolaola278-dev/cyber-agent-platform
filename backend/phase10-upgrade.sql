BEGIN;

-- Running upgrade 20260731_0011 -> 20260731_0012

CREATE TABLE incidents (
    title VARCHAR(512) NOT NULL, 
    description TEXT NOT NULL, 
    severity VARCHAR(16) NOT NULL, 
    priority VARCHAR(8) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    confidence VARCHAR(16) NOT NULL, 
    source VARCHAR(64) NOT NULL, 
    owner VARCHAR(256), 
    assignee VARCHAR(256), 
    queue VARCHAR(128), 
    classification VARCHAR(128), 
    risk VARCHAR(64), 
    correlation_key VARCHAR(256) NOT NULL, 
    duplicate_of_id UUID, 
    attributes JSON NOT NULL, 
    sla_due_at TIMESTAMP WITH TIME ZONE, 
    resolved_at TIMESTAMP WITH TIME ZONE, 
    closed_at TIMESTAMP WITH TIME ZONE, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_incidents PRIMARY KEY (id), 
    CONSTRAINT ck_incidents_ck_incidents_severity CHECK (severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')), 
    CONSTRAINT ck_incidents_ck_incidents_confidence CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')), 
    CONSTRAINT ck_incidents_ck_incidents_priority CHECK (priority IN ('P1', 'P2', 'P3', 'P4')), 
    CONSTRAINT ck_incidents_ck_incidents_status CHECK (status IN ('NEW', 'TRIAGED', 'INVESTIGATING', 'CONTAINED', 'RESOLVED', 'CLOSED', 'REOPENED')), 
    CONSTRAINT fk_incidents_duplicate_of_id_incidents FOREIGN KEY(duplicate_of_id) REFERENCES incidents (id) ON DELETE RESTRICT
);

CREATE INDEX ix_incidents_title ON incidents (title);

CREATE INDEX ix_incidents_severity ON incidents (severity);

CREATE INDEX ix_incidents_priority ON incidents (priority);

CREATE INDEX ix_incidents_status ON incidents (status);

CREATE INDEX ix_incidents_confidence ON incidents (confidence);

CREATE INDEX ix_incidents_source ON incidents (source);

CREATE INDEX ix_incidents_owner ON incidents (owner);

CREATE INDEX ix_incidents_assignee ON incidents (assignee);

CREATE INDEX ix_incidents_queue ON incidents (queue);

CREATE INDEX ix_incidents_classification ON incidents (classification);

CREATE INDEX ix_incidents_risk ON incidents (risk);

CREATE INDEX ix_incidents_correlation_key ON incidents (correlation_key);

CREATE INDEX ix_incidents_duplicate_of_id ON incidents (duplicate_of_id);

CREATE INDEX ix_incidents_sla_due_at ON incidents (sla_due_at);

CREATE TABLE incident_timelines (
    incident_id UUID NOT NULL, 
    event_type VARCHAR(64) NOT NULL, 
    actor VARCHAR(256) NOT NULL, 
    description TEXT NOT NULL, 
    from_status VARCHAR(32), 
    to_status VARCHAR(32), 
    details JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_incident_timelines PRIMARY KEY (id), 
    CONSTRAINT fk_incident_timelines_incident_id_incidents FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE
);

CREATE INDEX ix_incident_timelines_incident_id ON incident_timelines (incident_id);

CREATE INDEX ix_incident_timelines_event_type ON incident_timelines (event_type);

CREATE INDEX ix_incident_timelines_actor ON incident_timelines (actor);

CREATE TABLE incident_artifacts (
    incident_id UUID NOT NULL, 
    artifact_type VARCHAR(32) NOT NULL, 
    reference_id UUID, 
    value TEXT, 
    label VARCHAR(256), 
    attributes JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_incident_artifacts PRIMARY KEY (id), 
    CONSTRAINT ck_incident_artifacts_ck_incident_artifacts_type CHECK (artifact_type IN ('ASSET', 'EVIDENCE', 'FINDING', 'SECURITY_EVENT', 'KNOWLEDGE', 'REPORT', 'URL', 'HASH', 'IP', 'DOMAIN')), 
    CONSTRAINT fk_incident_artifacts_incident_id_incidents FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE
);

CREATE INDEX ix_incident_artifacts_incident_id ON incident_artifacts (incident_id);

CREATE INDEX ix_incident_artifacts_artifact_type ON incident_artifacts (artifact_type);

CREATE INDEX ix_incident_artifacts_reference_id ON incident_artifacts (reference_id);

CREATE TABLE investigation_cases (
    incident_id UUID NOT NULL, 
    title VARCHAR(512) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    owner VARCHAR(256), 
    assignee VARCHAR(256), 
    queue VARCHAR(128), 
    started_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    attributes JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_investigation_cases PRIMARY KEY (id), 
    CONSTRAINT ck_investigation_cases_ck_investigation_cases_status CHECK (status IN ('OPEN', 'ACTIVE', 'ON_HOLD', 'COMPLETED', 'CLOSED')), 
    CONSTRAINT fk_investigation_cases_incident_id_incidents FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE
);

CREATE INDEX ix_investigation_cases_incident_id ON investigation_cases (incident_id);

CREATE INDEX ix_investigation_cases_status ON investigation_cases (status);

CREATE INDEX ix_investigation_cases_owner ON investigation_cases (owner);

CREATE INDEX ix_investigation_cases_assignee ON investigation_cases (assignee);

CREATE INDEX ix_investigation_cases_queue ON investigation_cases (queue);

CREATE TABLE case_comments (
    case_id UUID NOT NULL, 
    author VARCHAR(256) NOT NULL, 
    body TEXT NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_case_comments PRIMARY KEY (id), 
    CONSTRAINT fk_case_comments_case_id_investigation_cases FOREIGN KEY(case_id) REFERENCES investigation_cases (id) ON DELETE CASCADE
);

CREATE INDEX ix_case_comments_case_id ON case_comments (case_id);

CREATE INDEX ix_case_comments_author ON case_comments (author);

CREATE TABLE incident_findings (
    incident_id UUID NOT NULL, 
    finding_id UUID NOT NULL, 
    relation VARCHAR(64) NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_incident_findings PRIMARY KEY (id), 
    CONSTRAINT fk_incident_findings_incident_id_incidents FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_incident_findings_finding_id_findings FOREIGN KEY(finding_id) REFERENCES findings (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_incident_findings_pair UNIQUE (incident_id, finding_id)
);

CREATE INDEX ix_incident_findings_incident_id ON incident_findings (incident_id);

CREATE INDEX ix_incident_findings_finding_id ON incident_findings (finding_id);

CREATE TABLE incident_events (
    incident_id UUID NOT NULL, 
    event_id UUID NOT NULL, 
    relation VARCHAR(64) NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_incident_events PRIMARY KEY (id), 
    CONSTRAINT fk_incident_events_incident_id_incidents FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_incident_events_event_id_security_events FOREIGN KEY(event_id) REFERENCES security_events (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_incident_events_pair UNIQUE (incident_id, event_id)
);

CREATE INDEX ix_incident_events_incident_id ON incident_events (incident_id);

CREATE INDEX ix_incident_events_event_id ON incident_events (event_id);

CREATE TABLE incident_assets (
    incident_id UUID NOT NULL, 
    asset_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_incident_assets PRIMARY KEY (id), 
    CONSTRAINT fk_incident_assets_incident_id_incidents FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_incident_assets_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_incident_assets_pair UNIQUE (incident_id, asset_id)
);

CREATE INDEX ix_incident_assets_incident_id ON incident_assets (incident_id);

CREATE INDEX ix_incident_assets_asset_id ON incident_assets (asset_id);

CREATE TABLE incident_knowledge (
    incident_id UUID NOT NULL, 
    knowledge_id UUID NOT NULL, 
    knowledge_version_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_incident_knowledge PRIMARY KEY (id), 
    CONSTRAINT fk_incident_knowledge_incident_id_incidents FOREIGN KEY(incident_id) REFERENCES incidents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_incident_knowledge_knowledge_id_knowledge FOREIGN KEY(knowledge_id) REFERENCES knowledge (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_incident_knowledge_knowledge_version_id_knowledge_versions FOREIGN KEY(knowledge_version_id) REFERENCES knowledge_versions (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_incident_knowledge_pair UNIQUE (incident_id, knowledge_id)
);

CREATE INDEX ix_incident_knowledge_incident_id ON incident_knowledge (incident_id);

CREATE INDEX ix_incident_knowledge_knowledge_id ON incident_knowledge (knowledge_id);

CREATE INDEX ix_incident_knowledge_knowledge_version_id ON incident_knowledge (knowledge_version_id);

UPDATE alembic_version SET version_num='20260731_0012' WHERE alembic_version.version_num = '20260731_0011';

COMMIT;

