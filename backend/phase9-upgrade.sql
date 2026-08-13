BEGIN;

-- Running upgrade 20260731_0010 -> 20260731_0011

CREATE TABLE detection_plugins (
    name VARCHAR(128) NOT NULL, 
    version VARCHAR(64) NOT NULL, 
    description TEXT, 
    enabled BOOLEAN NOT NULL, 
    permissions JSON NOT NULL, 
    configuration JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_detection_plugins PRIMARY KEY (id), 
    CONSTRAINT uq_detection_plugins_name_version UNIQUE (name, version)
);

CREATE INDEX ix_detection_plugins_name ON detection_plugins (name);

CREATE INDEX ix_detection_plugins_enabled ON detection_plugins (enabled);

CREATE TABLE detection_capabilities (
    plugin_id UUID NOT NULL, 
    capability_id UUID NOT NULL, 
    configuration JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_detection_capabilities PRIMARY KEY (id), 
    CONSTRAINT fk_detection_capabilities_plugin_id_detection_plugins FOREIGN KEY(plugin_id) REFERENCES detection_plugins (id) ON DELETE CASCADE, 
    CONSTRAINT fk_detection_capabilities_capability_id_capabilities FOREIGN KEY(capability_id) REFERENCES capabilities (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_detection_capabilities_plugin_capability UNIQUE (plugin_id, capability_id)
);

CREATE INDEX ix_detection_capabilities_plugin_id ON detection_capabilities (plugin_id);

CREATE INDEX ix_detection_capabilities_capability_id ON detection_capabilities (capability_id);

CREATE TABLE detection_tasks (
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
    CONSTRAINT pk_detection_tasks PRIMARY KEY (id), 
    CONSTRAINT ck_detection_tasks_ck_detection_tasks_status CHECK (status IN ('PLANNED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')), 
    CONSTRAINT fk_detection_tasks_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE, 
    CONSTRAINT fk_detection_tasks_plugin_id_detection_plugins FOREIGN KEY(plugin_id) REFERENCES detection_plugins (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_detection_tasks_task_id UNIQUE (task_id)
);

CREATE INDEX ix_detection_tasks_task_id ON detection_tasks (task_id);

CREATE INDEX ix_detection_tasks_plugin_id ON detection_tasks (plugin_id);

CREATE INDEX ix_detection_tasks_status ON detection_tasks (status);

CREATE TABLE security_events (
    detection_task_id UUID NOT NULL, 
    fingerprint VARCHAR(64) NOT NULL, 
    event_type VARCHAR(128) NOT NULL, 
    source VARCHAR(256) NOT NULL, 
    severity VARCHAR(16) NOT NULL, 
    confidence VARCHAR(16) NOT NULL, 
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL, 
    plugin VARCHAR(128) NOT NULL, 
    tool VARCHAR(128), 
    rule VARCHAR(256), 
    status VARCHAR(32) NOT NULL, 
    attributes JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_security_events PRIMARY KEY (id), 
    CONSTRAINT ck_security_events_ck_security_events_severity CHECK (severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')), 
    CONSTRAINT ck_security_events_ck_security_events_confidence CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')), 
    CONSTRAINT ck_security_events_ck_security_events_status CHECK (status IN ('NEW', 'CORRELATED', 'TRIAGED', 'IGNORED', 'ARCHIVED')), 
    CONSTRAINT fk_security_events_detection_task_id_detection_tasks FOREIGN KEY(detection_task_id) REFERENCES detection_tasks (id) ON DELETE CASCADE
);

CREATE INDEX ix_security_events_detection_task_id ON security_events (detection_task_id);

CREATE INDEX ix_security_events_fingerprint ON security_events (fingerprint);

CREATE INDEX ix_security_events_event_type ON security_events (event_type);

CREATE INDEX ix_security_events_source ON security_events (source);

CREATE INDEX ix_security_events_severity ON security_events (severity);

CREATE INDEX ix_security_events_confidence ON security_events (confidence);

CREATE INDEX ix_security_events_timestamp ON security_events (timestamp);

CREATE INDEX ix_security_events_plugin ON security_events (plugin);

CREATE INDEX ix_security_events_tool ON security_events (tool);

CREATE INDEX ix_security_events_rule ON security_events (rule);

CREATE INDEX ix_security_events_status ON security_events (status);

CREATE TABLE event_references (
    event_id UUID NOT NULL, 
    url TEXT NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_event_references PRIMARY KEY (id), 
    CONSTRAINT fk_event_references_event_id_security_events FOREIGN KEY(event_id) REFERENCES security_events (id) ON DELETE CASCADE, 
    CONSTRAINT uq_event_references_pair UNIQUE (event_id, url)
);

CREATE INDEX ix_event_references_event_id ON event_references (event_id);

CREATE INDEX ix_event_references_url ON event_references (url);

CREATE TABLE event_evidence (
    event_id UUID NOT NULL, 
    evidence_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_event_evidence PRIMARY KEY (id), 
    CONSTRAINT fk_event_evidence_event_id_security_events FOREIGN KEY(event_id) REFERENCES security_events (id) ON DELETE CASCADE, 
    CONSTRAINT fk_event_evidence_evidence_id_evidence FOREIGN KEY(evidence_id) REFERENCES evidence (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_event_evidence_pair UNIQUE (event_id, evidence_id)
);

CREATE INDEX ix_event_evidence_event_id ON event_evidence (event_id);

CREATE INDEX ix_event_evidence_evidence_id ON event_evidence (evidence_id);

CREATE TABLE event_assets (
    event_id UUID NOT NULL, 
    asset_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_event_assets PRIMARY KEY (id), 
    CONSTRAINT fk_event_assets_event_id_security_events FOREIGN KEY(event_id) REFERENCES security_events (id) ON DELETE CASCADE, 
    CONSTRAINT fk_event_assets_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_event_assets_pair UNIQUE (event_id, asset_id)
);

CREATE INDEX ix_event_assets_event_id ON event_assets (event_id);

CREATE INDEX ix_event_assets_asset_id ON event_assets (asset_id);

CREATE TABLE event_knowledge (
    event_id UUID NOT NULL, 
    knowledge_id UUID NOT NULL, 
    knowledge_version_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_event_knowledge PRIMARY KEY (id), 
    CONSTRAINT fk_event_knowledge_event_id_security_events FOREIGN KEY(event_id) REFERENCES security_events (id) ON DELETE CASCADE, 
    CONSTRAINT fk_event_knowledge_knowledge_id_knowledge FOREIGN KEY(knowledge_id) REFERENCES knowledge (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_event_knowledge_knowledge_version_id_knowledge_versions FOREIGN KEY(knowledge_version_id) REFERENCES knowledge_versions (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_event_knowledge_pair UNIQUE (event_id, knowledge_id)
);

CREATE INDEX ix_event_knowledge_event_id ON event_knowledge (event_id);

CREATE INDEX ix_event_knowledge_knowledge_id ON event_knowledge (knowledge_id);

CREATE INDEX ix_event_knowledge_knowledge_version_id ON event_knowledge (knowledge_version_id);

UPDATE alembic_version SET version_num='20260731_0011' WHERE alembic_version.version_num = '20260731_0010';

COMMIT;

