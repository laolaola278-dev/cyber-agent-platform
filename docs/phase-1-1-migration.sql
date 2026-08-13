INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Generating static SQL
BEGIN;

INFO  [alembic.runtime.migration] Will assume transactional DDL.
CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

INFO  [alembic.runtime.migration] Running upgrade  -> 20260729_0001, Create CAP Phase 0 core tables.
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

INFO  [alembic.runtime.migration] Running upgrade 20260729_0001 -> 20260729_0002, Add Registry, heartbeat, task lifecycle, and execution log primitives.
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

INFO  [alembic.runtime.migration] Running upgrade 20260729_0002 -> 20260729_0003, Add database-level Agent and Task status constraints.
-- Running upgrade 20260729_0002 -> 20260729_0003

ALTER TABLE agents ADD CONSTRAINT ck_agents_ck_agents_status CHECK (status IN ('ONLINE', 'OFFLINE', 'STARTING', 'STOPPING', 'ERROR'));

ALTER TABLE tasks ADD CONSTRAINT ck_tasks_ck_tasks_status CHECK (status IN ('CREATED', 'QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED'));

ALTER TABLE task_executions ADD CONSTRAINT ck_task_executions_ck_task_executions_status CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED'));

UPDATE alembic_version SET version_num='20260729_0003' WHERE alembic_version.version_num = '20260729_0002';

COMMIT;

