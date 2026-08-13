BEGIN;

-- Running upgrade 20260730_0008 -> 20260731_0009

CREATE TABLE assessment_plugins (
    name VARCHAR(128) NOT NULL, 
    version VARCHAR(64) NOT NULL, 
    description TEXT, 
    enabled BOOLEAN NOT NULL, 
    permissions JSON NOT NULL, 
    configuration JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_assessment_plugins PRIMARY KEY (id), 
    CONSTRAINT uq_assessment_plugins_name_version UNIQUE (name, version)
);

CREATE INDEX ix_assessment_plugins_name ON assessment_plugins (name);

CREATE INDEX ix_assessment_plugins_enabled ON assessment_plugins (enabled);

CREATE TABLE assessment_capabilities (
    plugin_id UUID NOT NULL, 
    capability_id UUID NOT NULL, 
    configuration JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_assessment_capabilities PRIMARY KEY (id), 
    CONSTRAINT fk_assessment_capabilities_plugin_id_assessment_plugins FOREIGN KEY(plugin_id) REFERENCES assessment_plugins (id) ON DELETE CASCADE, 
    CONSTRAINT fk_assessment_capabilities_capability_id_capabilities FOREIGN KEY(capability_id) REFERENCES capabilities (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_assessment_capabilities_plugin_capability UNIQUE (plugin_id, capability_id)
);

CREATE INDEX ix_assessment_capabilities_plugin_id ON assessment_capabilities (plugin_id);

CREATE INDEX ix_assessment_capabilities_capability_id ON assessment_capabilities (capability_id);

CREATE TABLE assessment_tasks (
    task_id UUID NOT NULL, 
    plugin_id UUID, 
    status VARCHAR(32) NOT NULL, 
    requested_capabilities JSON NOT NULL, 
    policy JSON NOT NULL, 
    plan JSON NOT NULL, 
    result_summary JSON NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    error TEXT, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_assessment_tasks PRIMARY KEY (id), 
    CONSTRAINT ck_assessment_tasks_ck_assessment_tasks_status CHECK (status IN ('PLANNED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')), 
    CONSTRAINT fk_assessment_tasks_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE, 
    CONSTRAINT fk_assessment_tasks_plugin_id_assessment_plugins FOREIGN KEY(plugin_id) REFERENCES assessment_plugins (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_assessment_tasks_task_id UNIQUE (task_id)
);

CREATE INDEX ix_assessment_tasks_task_id ON assessment_tasks (task_id);

CREATE INDEX ix_assessment_tasks_plugin_id ON assessment_tasks (plugin_id);

CREATE INDEX ix_assessment_tasks_status ON assessment_tasks (status);

CREATE TABLE findings (
    assessment_task_id UUID NOT NULL, 
    duplicate_of_id UUID, 
    fingerprint VARCHAR(64) NOT NULL, 
    title VARCHAR(512) NOT NULL, 
    severity VARCHAR(16) NOT NULL, 
    confidence VARCHAR(16) NOT NULL, 
    description TEXT NOT NULL, 
    affected_asset TEXT NOT NULL, 
    plugin VARCHAR(128) NOT NULL, 
    tool VARCHAR(128), 
    rule VARCHAR(256), 
    risk_level VARCHAR(16) NOT NULL, 
    risk_score FLOAT NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    attributes JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_findings PRIMARY KEY (id), 
    CONSTRAINT ck_findings_ck_findings_severity CHECK (severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')), 
    CONSTRAINT ck_findings_ck_findings_confidence CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')), 
    CONSTRAINT ck_findings_ck_findings_status CHECK (status IN ('OPEN', 'CONFIRMED', 'FALSE_POSITIVE', 'MITIGATED', 'ACCEPTED')), 
    CONSTRAINT fk_findings_assessment_task_id_assessment_tasks FOREIGN KEY(assessment_task_id) REFERENCES assessment_tasks (id) ON DELETE CASCADE, 
    CONSTRAINT fk_findings_duplicate_of_id_findings FOREIGN KEY(duplicate_of_id) REFERENCES findings (id) ON DELETE RESTRICT
);

CREATE INDEX ix_findings_assessment_task_id ON findings (assessment_task_id);

CREATE INDEX ix_findings_duplicate_of_id ON findings (duplicate_of_id);

CREATE INDEX ix_findings_fingerprint ON findings (fingerprint);

CREATE INDEX ix_findings_title ON findings (title);

CREATE INDEX ix_findings_severity ON findings (severity);

CREATE INDEX ix_findings_confidence ON findings (confidence);

CREATE INDEX ix_findings_affected_asset ON findings (affected_asset);

CREATE INDEX ix_findings_plugin ON findings (plugin);

CREATE INDEX ix_findings_tool ON findings (tool);

CREATE INDEX ix_findings_rule ON findings (rule);

CREATE INDEX ix_findings_risk_level ON findings (risk_level);

CREATE INDEX ix_findings_status ON findings (status);

CREATE TABLE finding_references (
    finding_id UUID NOT NULL, 
    url TEXT NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_finding_references PRIMARY KEY (id), 
    CONSTRAINT fk_finding_references_finding_id_findings FOREIGN KEY(finding_id) REFERENCES findings (id) ON DELETE CASCADE, 
    CONSTRAINT uq_finding_references_finding_url UNIQUE (finding_id, url)
);

CREATE INDEX ix_finding_references_finding_id ON finding_references (finding_id);

CREATE INDEX ix_finding_references_url ON finding_references (url);

CREATE TABLE finding_evidence (
    finding_id UUID NOT NULL, 
    evidence_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_finding_evidence PRIMARY KEY (id), 
    CONSTRAINT fk_finding_evidence_finding_id_findings FOREIGN KEY(finding_id) REFERENCES findings (id) ON DELETE CASCADE, 
    CONSTRAINT fk_finding_evidence_evidence_id_evidence FOREIGN KEY(evidence_id) REFERENCES evidence (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_finding_evidence_pair UNIQUE (finding_id, evidence_id)
);

CREATE INDEX ix_finding_evidence_finding_id ON finding_evidence (finding_id);

CREATE INDEX ix_finding_evidence_evidence_id ON finding_evidence (evidence_id);

CREATE TABLE finding_assets (
    finding_id UUID NOT NULL, 
    asset_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_finding_assets PRIMARY KEY (id), 
    CONSTRAINT fk_finding_assets_finding_id_findings FOREIGN KEY(finding_id) REFERENCES findings (id) ON DELETE CASCADE, 
    CONSTRAINT fk_finding_assets_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_finding_assets_pair UNIQUE (finding_id, asset_id)
);

CREATE INDEX ix_finding_assets_finding_id ON finding_assets (finding_id);

CREATE INDEX ix_finding_assets_asset_id ON finding_assets (asset_id);

CREATE TABLE finding_knowledge (
    finding_id UUID NOT NULL, 
    knowledge_id UUID NOT NULL, 
    knowledge_version_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_finding_knowledge PRIMARY KEY (id), 
    CONSTRAINT fk_finding_knowledge_finding_id_findings FOREIGN KEY(finding_id) REFERENCES findings (id) ON DELETE CASCADE, 
    CONSTRAINT fk_finding_knowledge_knowledge_id_knowledge FOREIGN KEY(knowledge_id) REFERENCES knowledge (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_finding_knowledge_knowledge_version_id_knowledge_versions FOREIGN KEY(knowledge_version_id) REFERENCES knowledge_versions (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_finding_knowledge_pair UNIQUE (finding_id, knowledge_id)
);

CREATE INDEX ix_finding_knowledge_finding_id ON finding_knowledge (finding_id);

CREATE INDEX ix_finding_knowledge_knowledge_id ON finding_knowledge (knowledge_id);

CREATE INDEX ix_finding_knowledge_knowledge_version_id ON finding_knowledge (knowledge_version_id);

UPDATE alembic_version SET version_num='20260731_0009' WHERE alembic_version.version_num = '20260730_0008';

COMMIT;

