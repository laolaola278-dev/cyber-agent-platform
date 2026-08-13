BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 20260729_0001

CREATE TABLE agents (
    name VARCHAR(128) NOT NULL, 
    version VARCHAR(64) NOT NULL, 
    description TEXT, 
    status VARCHAR(32) NOT NULL, 
    runtime_image VARCHAR(512), 
    permissions JSON NOT NULL, 
    tools JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_agents PRIMARY KEY (id), 
    CONSTRAINT uq_agents_name_version UNIQUE (name, version)
);

CREATE INDEX ix_agents_name ON agents (name);

CREATE INDEX ix_agents_status ON agents (status);

CREATE TABLE audit_logs (
    operator VARCHAR(256) NOT NULL, 
    action VARCHAR(128) NOT NULL, 
    resource VARCHAR(512) NOT NULL, 
    details JSON NOT NULL, 
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    id UUID NOT NULL, 
    CONSTRAINT pk_audit_logs PRIMARY KEY (id)
);

CREATE INDEX ix_audit_logs_action ON audit_logs (action);

CREATE INDEX ix_audit_logs_operator ON audit_logs (operator);

CREATE INDEX ix_audit_logs_resource ON audit_logs (resource);

CREATE INDEX ix_audit_logs_timestamp ON audit_logs (timestamp);

CREATE TABLE tasks (
    name VARCHAR(256) NOT NULL, 
    task_type VARCHAR(128) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    input JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_tasks PRIMARY KEY (id)
);

CREATE INDEX ix_tasks_name ON tasks (name);

CREATE INDEX ix_tasks_status ON tasks (status);

CREATE INDEX ix_tasks_task_type ON tasks (task_type);

CREATE TABLE tools (
    name VARCHAR(128) NOT NULL, 
    version VARCHAR(64) NOT NULL, 
    type VARCHAR(64) NOT NULL, 
    description TEXT, 
    config JSON NOT NULL, 
    id UUID NOT NULL, 
    CONSTRAINT pk_tools PRIMARY KEY (id), 
    CONSTRAINT uq_tools_name_version UNIQUE (name, version)
);

CREATE INDEX ix_tools_name ON tools (name);

CREATE INDEX ix_tools_type ON tools (type);

CREATE TABLE task_executions (
    task_id UUID NOT NULL, 
    agent_id UUID NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    start_time TIMESTAMP WITH TIME ZONE, 
    end_time TIMESTAMP WITH TIME ZONE, 
    result JSON, 
    logs TEXT, 
    id UUID NOT NULL, 
    CONSTRAINT pk_task_executions PRIMARY KEY (id), 
    CONSTRAINT fk_task_executions_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_task_executions_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
);

CREATE INDEX ix_task_executions_agent_id ON task_executions (agent_id);

CREATE INDEX ix_task_executions_status ON task_executions (status);

CREATE INDEX ix_task_executions_task_id ON task_executions (task_id);

INSERT INTO alembic_version (version_num) VALUES ('20260729_0001') RETURNING alembic_version.version_num;

-- Running upgrade 20260729_0001 -> 20260729_0002

ALTER TABLE agents DROP CONSTRAINT uq_agents_name_version;

ALTER TABLE agents ADD CONSTRAINT uq_agents_name UNIQUE (name);

ALTER TABLE tools DROP CONSTRAINT uq_tools_name_version;

ALTER TABLE tools ADD CONSTRAINT uq_tools_name UNIQUE (name);

ALTER TABLE audit_logs ADD COLUMN trace_id VARCHAR(64) DEFAULT '-' NOT NULL;

ALTER TABLE audit_logs ADD COLUMN agent_id VARCHAR(36);

ALTER TABLE audit_logs ADD COLUMN task_id VARCHAR(36);

ALTER TABLE audit_logs ADD COLUMN tool_id VARCHAR(36);

ALTER TABLE audit_logs ADD COLUMN result JSON;

ALTER TABLE audit_logs ADD COLUMN error VARCHAR(2048);

CREATE INDEX ix_audit_logs_trace_id ON audit_logs (trace_id);

CREATE INDEX ix_audit_logs_agent_id ON audit_logs (agent_id);

CREATE INDEX ix_audit_logs_task_id ON audit_logs (task_id);

CREATE INDEX ix_audit_logs_tool_id ON audit_logs (tool_id);

ALTER TABLE agents ADD COLUMN author VARCHAR(256) DEFAULT 'system' NOT NULL;

ALTER TABLE agents ADD COLUMN runtime JSON DEFAULT '{}'::json NOT NULL;

ALTER TABLE agents ADD COLUMN network_policy JSON DEFAULT '{}'::json NOT NULL;

ALTER TABLE agents ADD COLUMN resource_limit JSON DEFAULT '{}'::json NOT NULL;

ALTER TABLE agents ADD COLUMN approval_policy JSON DEFAULT '{}'::json NOT NULL;

ALTER TABLE agents ADD COLUMN health_status VARCHAR(32) DEFAULT 'UNKNOWN' NOT NULL;

ALTER TABLE agents ADD COLUMN heartbeat_time TIMESTAMP WITH TIME ZONE;

CREATE INDEX ix_agents_health_status ON agents (health_status);

CREATE TABLE agent_versions (
    agent_id UUID NOT NULL, 
    version VARCHAR(64) NOT NULL, 
    manifest JSON NOT NULL, 
    is_active BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_agent_versions PRIMARY KEY (id), 
    CONSTRAINT fk_agent_versions_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE, 
    CONSTRAINT uq_agent_versions_agent_version UNIQUE (agent_id, version)
);

CREATE INDEX ix_agent_versions_agent_id ON agent_versions (agent_id);

CREATE TABLE agent_heartbeats (
    agent_id UUID NOT NULL, 
    health_status VARCHAR(32) NOT NULL, 
    details JSON NOT NULL, 
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    id UUID NOT NULL, 
    CONSTRAINT pk_agent_heartbeats PRIMARY KEY (id), 
    CONSTRAINT fk_agent_heartbeats_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_heartbeats_agent_id ON agent_heartbeats (agent_id);

CREATE INDEX ix_agent_heartbeats_timestamp ON agent_heartbeats (timestamp);

ALTER TABLE tools RENAME type TO tool_type;

ALTER TABLE tools RENAME config TO config_schema;

ALTER TABLE tools ADD COLUMN required_permissions JSON DEFAULT '[]'::json NOT NULL;

ALTER TABLE tools ADD COLUMN runtime_requirements JSON DEFAULT '{}'::json NOT NULL;

ALTER TABLE tools ADD COLUMN status VARCHAR(32) DEFAULT 'ENABLED' NOT NULL;

ALTER TABLE tools ADD COLUMN created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL;

ALTER TABLE tools ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL;

CREATE INDEX ix_tools_status ON tools (status);

CREATE TABLE tool_versions (
    tool_id UUID NOT NULL, 
    version VARCHAR(64) NOT NULL, 
    manifest JSON NOT NULL, 
    is_active BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_tool_versions PRIMARY KEY (id), 
    CONSTRAINT fk_tool_versions_tool_id_tools FOREIGN KEY(tool_id) REFERENCES tools (id) ON DELETE CASCADE, 
    CONSTRAINT uq_tool_versions_tool_version UNIQUE (tool_id, version)
);

CREATE INDEX ix_tool_versions_tool_id ON tool_versions (tool_id);

ALTER TABLE tasks ADD COLUMN required_permissions JSON DEFAULT '[]'::json NOT NULL;

ALTER TABLE tasks ADD COLUMN target_agent_id UUID;

CREATE INDEX ix_tasks_target_agent_id ON tasks (target_agent_id);

UPDATE tasks SET status = 'CREATED' WHERE status = 'pending';

ALTER TABLE tasks ALTER COLUMN status SET DEFAULT 'CREATED';

CREATE TABLE task_logs (
    task_id UUID NOT NULL, 
    level VARCHAR(32) NOT NULL, 
    message TEXT NOT NULL, 
    trace_id VARCHAR(64) NOT NULL, 
    id UUID NOT NULL, 
    CONSTRAINT pk_task_logs PRIMARY KEY (id), 
    CONSTRAINT fk_task_logs_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
);

CREATE INDEX ix_task_logs_task_id ON task_logs (task_id);

CREATE INDEX ix_task_logs_trace_id ON task_logs (trace_id);

ALTER TABLE task_executions ADD COLUMN trace_id VARCHAR(64) DEFAULT '-' NOT NULL;

UPDATE task_executions SET status = 'QUEUED' WHERE status = 'queued';

ALTER TABLE task_executions ALTER COLUMN status SET DEFAULT 'QUEUED';

CREATE INDEX ix_task_executions_trace_id ON task_executions (trace_id);

CREATE TABLE execution_logs (
    execution_id UUID NOT NULL, 
    level VARCHAR(32) NOT NULL, 
    message TEXT NOT NULL, 
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    id UUID NOT NULL, 
    CONSTRAINT pk_execution_logs PRIMARY KEY (id), 
    CONSTRAINT fk_execution_logs_execution_id_task_executions FOREIGN KEY(execution_id) REFERENCES task_executions (id) ON DELETE CASCADE
);

CREATE INDEX ix_execution_logs_execution_id ON execution_logs (execution_id);

CREATE INDEX ix_execution_logs_timestamp ON execution_logs (timestamp);

UPDATE alembic_version SET version_num='20260729_0002' WHERE alembic_version.version_num = '20260729_0001';

-- Running upgrade 20260729_0002 -> 20260729_0003

ALTER TABLE agents ADD CONSTRAINT ck_agents_ck_agents_status CHECK (status IN ('ONLINE', 'OFFLINE', 'STARTING', 'STOPPING', 'ERROR'));

ALTER TABLE tasks ADD CONSTRAINT ck_tasks_ck_tasks_status CHECK (status IN ('CREATED', 'QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED'));

ALTER TABLE task_executions ADD CONSTRAINT ck_task_executions_ck_task_executions_status CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED'));

UPDATE alembic_version SET version_num='20260729_0003' WHERE alembic_version.version_num = '20260729_0002';

-- Running upgrade 20260729_0003 -> 20260729_0004

CREATE TABLE agent_runtimes (
    agent_id UUID NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    manifest_path VARCHAR(512) NOT NULL, 
    entrypoint VARCHAR(512) NOT NULL, 
    loaded_at TIMESTAMP WITH TIME ZONE, 
    started_at TIMESTAMP WITH TIME ZONE, 
    stopped_at TIMESTAMP WITH TIME ZONE, 
    last_health JSON NOT NULL, 
    last_error TEXT, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_agent_runtimes PRIMARY KEY (id), 
    CONSTRAINT fk_agent_runtimes_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE, 
    CONSTRAINT uq_agent_runtimes_agent_id UNIQUE (agent_id)
);

CREATE INDEX ix_agent_runtimes_agent_id ON agent_runtimes (agent_id);

CREATE INDEX ix_agent_runtimes_status ON agent_runtimes (status);

CREATE TABLE evidence (
    task_id UUID NOT NULL, 
    agent_id UUID NOT NULL, 
    trace_id VARCHAR(64) NOT NULL, 
    url TEXT NOT NULL, 
    http_status INTEGER, 
    title TEXT, 
    html_hash VARCHAR(64) NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    screenshot_path VARCHAR(1024), 
    captured_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_evidence PRIMARY KEY (id), 
    CONSTRAINT fk_evidence_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_evidence_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
);

CREATE INDEX ix_evidence_agent_id ON evidence (agent_id);

CREATE INDEX ix_evidence_task_id ON evidence (task_id);

CREATE INDEX ix_evidence_trace_id ON evidence (trace_id);

CREATE INDEX ix_evidence_captured_at ON evidence (captured_at);

CREATE TABLE reports (
    task_id UUID NOT NULL, 
    agent_id UUID NOT NULL, 
    trace_id VARCHAR(64) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    json_content JSON NOT NULL, 
    markdown_content TEXT NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_reports PRIMARY KEY (id), 
    CONSTRAINT fk_reports_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_reports_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE, 
    CONSTRAINT uq_reports_task_id UNIQUE (task_id)
);

CREATE INDEX ix_reports_agent_id ON reports (agent_id);

CREATE INDEX ix_reports_task_id ON reports (task_id);

CREATE INDEX ix_reports_trace_id ON reports (trace_id);

UPDATE alembic_version SET version_num='20260729_0004' WHERE alembic_version.version_num = '20260729_0003';

-- Running upgrade 20260729_0004 -> 20260729_0005

ALTER TABLE agents ADD COLUMN capabilities JSON DEFAULT '[]'::json NOT NULL;

ALTER TABLE agents ADD COLUMN minimum_runtime_version VARCHAR(64) DEFAULT '1.0.0' NOT NULL;

ALTER TABLE agents ADD COLUMN platform_version VARCHAR(64) DEFAULT '0.2.1' NOT NULL;

ALTER TABLE agents ADD COLUMN sdk_version VARCHAR(64) DEFAULT '1.0.0' NOT NULL;

ALTER TABLE tasks ADD COLUMN required_capabilities JSON DEFAULT '[]'::json NOT NULL;

CREATE TABLE capabilities (
    name VARCHAR(128) NOT NULL, 
    description TEXT, 
    risk_level VARCHAR(32) NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_capabilities PRIMARY KEY (id), 
    CONSTRAINT uq_capabilities_name UNIQUE (name)
);

CREATE UNIQUE INDEX ix_capabilities_name ON capabilities (name);

CREATE INDEX ix_capabilities_risk_level ON capabilities (risk_level);

CREATE TABLE agent_capabilities (
    agent_id UUID NOT NULL, 
    capability_id UUID NOT NULL, 
    configuration JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_agent_capabilities PRIMARY KEY (id), 
    CONSTRAINT fk_agent_capabilities_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE, 
    CONSTRAINT fk_agent_capabilities_capability_id_capabilities FOREIGN KEY(capability_id) REFERENCES capabilities (id) ON DELETE CASCADE, 
    CONSTRAINT uq_agent_capabilities_agent_capability UNIQUE (agent_id, capability_id)
);

CREATE INDEX ix_agent_capabilities_agent_id ON agent_capabilities (agent_id);

CREATE INDEX ix_agent_capabilities_capability_id ON agent_capabilities (capability_id);

ALTER TABLE evidence ADD COLUMN evidence_type VARCHAR(32) DEFAULT 'HTML' NOT NULL;

ALTER TABLE evidence ADD COLUMN sha256 VARCHAR(64);

UPDATE evidence SET sha256 = html_hash;

ALTER TABLE evidence ALTER COLUMN sha256 SET NOT NULL;

ALTER TABLE evidence ADD COLUMN content_type VARCHAR(255) DEFAULT 'text/html; charset=utf-8' NOT NULL;

ALTER TABLE evidence ADD COLUMN object_storage_path VARCHAR(1024);

CREATE INDEX ix_evidence_evidence_type ON evidence (evidence_type);

ALTER TABLE reports ADD COLUMN html_content TEXT DEFAULT '' NOT NULL;

ALTER TABLE agents ALTER COLUMN capabilities DROP DEFAULT;

ALTER TABLE agents ALTER COLUMN minimum_runtime_version DROP DEFAULT;

ALTER TABLE agents ALTER COLUMN platform_version DROP DEFAULT;

ALTER TABLE agents ALTER COLUMN sdk_version DROP DEFAULT;

ALTER TABLE tasks ALTER COLUMN required_capabilities DROP DEFAULT;

ALTER TABLE evidence ALTER COLUMN evidence_type DROP DEFAULT;

ALTER TABLE evidence ALTER COLUMN content_type DROP DEFAULT;

ALTER TABLE reports ALTER COLUMN html_content DROP DEFAULT;

UPDATE alembic_version SET version_num='20260729_0005' WHERE alembic_version.version_num = '20260729_0004';

-- Running upgrade 20260729_0005 -> 20260730_0006

CREATE TABLE workflow_definitions (
    name VARCHAR(256) NOT NULL, 
    version VARCHAR(64) NOT NULL, 
    description TEXT, 
    source_yaml TEXT NOT NULL, 
    definition JSON NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_workflow_definitions PRIMARY KEY (id), 
    CONSTRAINT uq_workflow_definitions_name_version UNIQUE (name, version)
);

CREATE INDEX ix_workflow_definitions_name ON workflow_definitions (name);

CREATE INDEX ix_workflow_definitions_enabled ON workflow_definitions (enabled);

CREATE TABLE workflow_instances (
    definition_id UUID NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    input JSON NOT NULL, 
    context JSON NOT NULL, 
    current_node VARCHAR(128), 
    trace_id VARCHAR(64) NOT NULL, 
    cancel_requested BOOLEAN NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    error TEXT, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_workflow_instances PRIMARY KEY (id), 
    CONSTRAINT ck_workflow_instances_ck_workflow_instances_workflow_in_eedd CHECK (status IN ('PENDING', 'RUNNING', 'WAITING', 'FAILED', 'SUCCESS', 'CANCELLED')), 
    CONSTRAINT fk_workflow_instances_definition_id_workflow_definitions FOREIGN KEY(definition_id) REFERENCES workflow_definitions (id) ON DELETE RESTRICT
);

CREATE INDEX ix_workflow_instances_definition_id ON workflow_instances (definition_id);

CREATE INDEX ix_workflow_instances_status ON workflow_instances (status);

CREATE INDEX ix_workflow_instances_trace_id ON workflow_instances (trace_id);

CREATE TABLE workflow_steps (
    instance_id UUID NOT NULL, 
    node_id VARCHAR(128) NOT NULL, 
    node_type VARCHAR(32) NOT NULL, 
    capability VARCHAR(128), 
    status VARCHAR(32) NOT NULL, 
    attempt INTEGER NOT NULL, 
    max_attempts INTEGER NOT NULL, 
    timeout_seconds INTEGER NOT NULL, 
    input JSON NOT NULL, 
    output JSON, 
    error TEXT, 
    started_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_workflow_steps PRIMARY KEY (id), 
    CONSTRAINT ck_workflow_steps_ck_workflow_steps_workflow_step_status CHECK (status IN ('PENDING', 'RUNNING', 'WAITING', 'FAILED', 'SUCCESS', 'CANCELLED', 'SKIPPED')), 
    CONSTRAINT fk_workflow_steps_instance_id_workflow_instances FOREIGN KEY(instance_id) REFERENCES workflow_instances (id) ON DELETE CASCADE, 
    CONSTRAINT uq_workflow_steps_instance_node UNIQUE (instance_id, node_id)
);

CREATE INDEX ix_workflow_steps_instance_id ON workflow_steps (instance_id);

CREATE INDEX ix_workflow_steps_node_type ON workflow_steps (node_type);

CREATE INDEX ix_workflow_steps_capability ON workflow_steps (capability);

CREATE INDEX ix_workflow_steps_status ON workflow_steps (status);

CREATE TABLE workflow_executions (
    instance_id UUID NOT NULL, 
    step_id UUID NOT NULL, 
    attempt INTEGER NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    duration_ms INTEGER, 
    output JSON, 
    error TEXT, 
    id UUID NOT NULL, 
    CONSTRAINT pk_workflow_executions PRIMARY KEY (id), 
    CONSTRAINT ck_workflow_executions_ck_workflow_executions_workflow__2469 CHECK (status IN ('PENDING', 'RUNNING', 'WAITING', 'FAILED', 'SUCCESS', 'CANCELLED', 'SKIPPED')), 
    CONSTRAINT fk_workflow_executions_instance_id_workflow_instances FOREIGN KEY(instance_id) REFERENCES workflow_instances (id) ON DELETE CASCADE, 
    CONSTRAINT fk_workflow_executions_step_id_workflow_steps FOREIGN KEY(step_id) REFERENCES workflow_steps (id) ON DELETE CASCADE
);

CREATE INDEX ix_workflow_executions_instance_id ON workflow_executions (instance_id);

CREATE INDEX ix_workflow_executions_step_id ON workflow_executions (step_id);

CREATE INDEX ix_workflow_executions_status ON workflow_executions (status);

UPDATE alembic_version SET version_num='20260730_0006' WHERE alembic_version.version_num = '20260729_0005';

-- Running upgrade 20260730_0006 -> 20260730_0007

CREATE TABLE assets (
    asset_type VARCHAR(32) NOT NULL, 
    name VARCHAR(256) NOT NULL, 
    value TEXT NOT NULL, 
    canonical_value TEXT NOT NULL, 
    owner VARCHAR(256), 
    business_unit VARCHAR(256), 
    environment VARCHAR(64), 
    criticality VARCHAR(32), 
    risk VARCHAR(32), 
    capabilities JSON NOT NULL, 
    properties JSON NOT NULL, 
    agent_id UUID, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    deleted_by VARCHAR(256), 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_assets PRIMARY KEY (id), 
    CONSTRAINT fk_assets_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_assets_agent_id UNIQUE (agent_id), 
    CONSTRAINT uq_assets_type_canonical_value UNIQUE (asset_type, canonical_value)
);

CREATE INDEX ix_assets_asset_type ON assets (asset_type);

CREATE INDEX ix_assets_name ON assets (name);

CREATE INDEX ix_assets_owner ON assets (owner);

CREATE INDEX ix_assets_business_unit ON assets (business_unit);

CREATE INDEX ix_assets_environment ON assets (environment);

CREATE INDEX ix_assets_criticality ON assets (criticality);

CREATE INDEX ix_assets_risk ON assets (risk);

CREATE INDEX ix_assets_agent_id ON assets (agent_id);

CREATE INDEX ix_assets_deleted_at ON assets (deleted_at);

CREATE TABLE asset_relations (
    source_asset_id UUID NOT NULL, 
    target_asset_id UUID NOT NULL, 
    relation_type VARCHAR(64) NOT NULL, 
    properties JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_asset_relations PRIMARY KEY (id), 
    CONSTRAINT fk_asset_relations_source_asset_id_assets FOREIGN KEY(source_asset_id) REFERENCES assets (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_asset_relations_target_asset_id_assets FOREIGN KEY(target_asset_id) REFERENCES assets (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_asset_relations_source_target_type UNIQUE (source_asset_id, target_asset_id, relation_type)
);

CREATE INDEX ix_asset_relations_source_asset_id ON asset_relations (source_asset_id);

CREATE INDEX ix_asset_relations_target_asset_id ON asset_relations (target_asset_id);

CREATE INDEX ix_asset_relations_relation_type ON asset_relations (relation_type);

CREATE TABLE asset_tags (
    asset_id UUID NOT NULL, 
    name VARCHAR(128) NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_asset_tags PRIMARY KEY (id), 
    CONSTRAINT fk_asset_tags_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE, 
    CONSTRAINT uq_asset_tags_asset_name UNIQUE (asset_id, name)
);

CREATE INDEX ix_asset_tags_asset_id ON asset_tags (asset_id);

CREATE INDEX ix_asset_tags_name ON asset_tags (name);

CREATE TABLE asset_evidence (
    asset_id UUID NOT NULL, 
    evidence_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_asset_evidence PRIMARY KEY (id), 
    CONSTRAINT fk_asset_evidence_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_asset_evidence_evidence_id_evidence FOREIGN KEY(evidence_id) REFERENCES evidence (id) ON DELETE CASCADE, 
    CONSTRAINT uq_asset_evidence_asset_evidence UNIQUE (asset_id, evidence_id)
);

CREATE INDEX ix_asset_evidence_asset_id ON asset_evidence (asset_id);

CREATE INDEX ix_asset_evidence_evidence_id ON asset_evidence (evidence_id);

CREATE TABLE asset_reports (
    asset_id UUID NOT NULL, 
    report_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_asset_reports PRIMARY KEY (id), 
    CONSTRAINT fk_asset_reports_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_asset_reports_report_id_reports FOREIGN KEY(report_id) REFERENCES reports (id) ON DELETE CASCADE, 
    CONSTRAINT uq_asset_reports_asset_report UNIQUE (asset_id, report_id)
);

CREATE INDEX ix_asset_reports_asset_id ON asset_reports (asset_id);

CREATE INDEX ix_asset_reports_report_id ON asset_reports (report_id);

ALTER TABLE tasks ADD COLUMN asset_id UUID;

ALTER TABLE tasks ADD CONSTRAINT fk_tasks_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE RESTRICT;

CREATE INDEX ix_tasks_asset_id ON tasks (asset_id);

ALTER TABLE workflow_instances ADD COLUMN asset_id UUID;

ALTER TABLE workflow_instances ADD CONSTRAINT fk_workflow_instances_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE RESTRICT;

CREATE INDEX ix_workflow_instances_asset_id ON workflow_instances (asset_id);

UPDATE alembic_version SET version_num='20260730_0007' WHERE alembic_version.version_num = '20260730_0006';

-- Running upgrade 20260730_0007 -> 20260730_0008

CREATE TABLE knowledge_sources (
    name VARCHAR(128) NOT NULL, 
    provider_type VARCHAR(128) NOT NULL, 
    base_url TEXT, 
    enabled BOOLEAN NOT NULL, 
    configuration JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_knowledge_sources PRIMARY KEY (id), 
    CONSTRAINT uq_knowledge_sources_name UNIQUE (name)
);

CREATE INDEX ix_knowledge_sources_name ON knowledge_sources (name);

CREATE INDEX ix_knowledge_sources_provider_type ON knowledge_sources (provider_type);

CREATE INDEX ix_knowledge_sources_enabled ON knowledge_sources (enabled);

CREATE TABLE knowledge (
    source_id UUID NOT NULL, 
    knowledge_type VARCHAR(64) NOT NULL, 
    external_id VARCHAR(256) NOT NULL, 
    current_version VARCHAR(256) NOT NULL, 
    current_content_hash VARCHAR(64) NOT NULL, 
    title VARCHAR(512) NOT NULL, 
    description TEXT NOT NULL, 
    "references" JSON NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    attributes JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_knowledge PRIMARY KEY (id), 
    CONSTRAINT fk_knowledge_source_id_knowledge_sources FOREIGN KEY(source_id) REFERENCES knowledge_sources (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_knowledge_source_type_external_id UNIQUE (source_id, knowledge_type, external_id)
);

CREATE INDEX ix_knowledge_source_id ON knowledge (source_id);

CREATE INDEX ix_knowledge_knowledge_type ON knowledge (knowledge_type);

CREATE INDEX ix_knowledge_external_id ON knowledge (external_id);

CREATE INDEX ix_knowledge_current_version ON knowledge (current_version);

CREATE INDEX ix_knowledge_current_content_hash ON knowledge (current_content_hash);

CREATE INDEX ix_knowledge_title ON knowledge (title);

CREATE INDEX ix_knowledge_status ON knowledge (status);

CREATE TABLE knowledge_versions (
    knowledge_id UUID NOT NULL, 
    version VARCHAR(256) NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    payload JSON NOT NULL, 
    source_updated_at TIMESTAMP WITH TIME ZONE, 
    imported_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    id UUID NOT NULL, 
    CONSTRAINT pk_knowledge_versions PRIMARY KEY (id), 
    CONSTRAINT fk_knowledge_versions_knowledge_id_knowledge FOREIGN KEY(knowledge_id) REFERENCES knowledge (id) ON DELETE CASCADE, 
    CONSTRAINT uq_knowledge_version_snapshot UNIQUE (knowledge_id, version, content_hash)
);

CREATE INDEX ix_knowledge_versions_knowledge_id ON knowledge_versions (knowledge_id);

CREATE INDEX ix_knowledge_versions_version ON knowledge_versions (version);

CREATE INDEX ix_knowledge_versions_content_hash ON knowledge_versions (content_hash);

CREATE INDEX ix_knowledge_versions_imported_at ON knowledge_versions (imported_at);

CREATE TABLE knowledge_relations (
    source_knowledge_id UUID NOT NULL, 
    target_knowledge_id UUID NOT NULL, 
    relation_type VARCHAR(64) NOT NULL, 
    source_name VARCHAR(128) NOT NULL, 
    properties JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_knowledge_relations PRIMARY KEY (id), 
    CONSTRAINT fk_knowledge_relations_source_knowledge_id_knowledge FOREIGN KEY(source_knowledge_id) REFERENCES knowledge (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_knowledge_relations_target_knowledge_id_knowledge FOREIGN KEY(target_knowledge_id) REFERENCES knowledge (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_knowledge_relations_source_target_type UNIQUE (source_knowledge_id, target_knowledge_id, relation_type)
);

CREATE INDEX ix_knowledge_relations_source_knowledge_id ON knowledge_relations (source_knowledge_id);

CREATE INDEX ix_knowledge_relations_target_knowledge_id ON knowledge_relations (target_knowledge_id);

CREATE INDEX ix_knowledge_relations_relation_type ON knowledge_relations (relation_type);

CREATE INDEX ix_knowledge_relations_source_name ON knowledge_relations (source_name);

CREATE TABLE asset_knowledge (
    asset_id UUID NOT NULL, 
    knowledge_id UUID NOT NULL, 
    knowledge_version_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_asset_knowledge PRIMARY KEY (id), 
    CONSTRAINT fk_asset_knowledge_asset_id_assets FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE, 
    CONSTRAINT fk_asset_knowledge_knowledge_id_knowledge FOREIGN KEY(knowledge_id) REFERENCES knowledge (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_asset_knowledge_knowledge_version_id_knowledge_versions FOREIGN KEY(knowledge_version_id) REFERENCES knowledge_versions (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_asset_knowledge_asset_knowledge UNIQUE (asset_id, knowledge_id)
);

CREATE INDEX ix_asset_knowledge_asset_id ON asset_knowledge (asset_id);

CREATE INDEX ix_asset_knowledge_knowledge_id ON asset_knowledge (knowledge_id);

CREATE INDEX ix_asset_knowledge_knowledge_version_id ON asset_knowledge (knowledge_version_id);

CREATE TABLE evidence_knowledge (
    evidence_id UUID NOT NULL, 
    knowledge_id UUID NOT NULL, 
    knowledge_version_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_evidence_knowledge PRIMARY KEY (id), 
    CONSTRAINT fk_evidence_knowledge_evidence_id_evidence FOREIGN KEY(evidence_id) REFERENCES evidence (id) ON DELETE CASCADE, 
    CONSTRAINT fk_evidence_knowledge_knowledge_id_knowledge FOREIGN KEY(knowledge_id) REFERENCES knowledge (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_evidence_knowledge_knowledge_version_id_knowledge_versions FOREIGN KEY(knowledge_version_id) REFERENCES knowledge_versions (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_evidence_knowledge_evidence_knowledge UNIQUE (evidence_id, knowledge_id)
);

CREATE INDEX ix_evidence_knowledge_evidence_id ON evidence_knowledge (evidence_id);

CREATE INDEX ix_evidence_knowledge_knowledge_id ON evidence_knowledge (knowledge_id);

CREATE INDEX ix_evidence_knowledge_knowledge_version_id ON evidence_knowledge (knowledge_version_id);

CREATE TABLE report_knowledge (
    report_id UUID NOT NULL, 
    knowledge_id UUID NOT NULL, 
    knowledge_version_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_report_knowledge PRIMARY KEY (id), 
    CONSTRAINT fk_report_knowledge_report_id_reports FOREIGN KEY(report_id) REFERENCES reports (id) ON DELETE CASCADE, 
    CONSTRAINT fk_report_knowledge_knowledge_id_knowledge FOREIGN KEY(knowledge_id) REFERENCES knowledge (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_report_knowledge_knowledge_version_id_knowledge_versions FOREIGN KEY(knowledge_version_id) REFERENCES knowledge_versions (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_report_knowledge_report_knowledge UNIQUE (report_id, knowledge_id)
);

CREATE INDEX ix_report_knowledge_report_id ON report_knowledge (report_id);

CREATE INDEX ix_report_knowledge_knowledge_id ON report_knowledge (knowledge_id);

CREATE INDEX ix_report_knowledge_knowledge_version_id ON report_knowledge (knowledge_version_id);

UPDATE alembic_version SET version_num='20260730_0008' WHERE alembic_version.version_num = '20260730_0007';

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

