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

COMMIT;

