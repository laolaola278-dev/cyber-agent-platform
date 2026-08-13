BEGIN;

-- Running upgrade 20260731_0012 -> 20260801_0013

CREATE TABLE telemetry_pipelines (
    name VARCHAR(128) NOT NULL, 
    version VARCHAR(64) NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    receivers JSON NOT NULL, 
    processors JSON NOT NULL, 
    exporters JSON NOT NULL, 
    configuration JSON NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_telemetry_pipelines PRIMARY KEY (id), 
    CONSTRAINT uq_telemetry_pipelines_name_version UNIQUE (name, version)
);

CREATE INDEX ix_telemetry_pipelines_name ON telemetry_pipelines (name);

CREATE INDEX ix_telemetry_pipelines_enabled ON telemetry_pipelines (enabled);

CREATE TABLE telemetry_tasks (
    task_id UUID NOT NULL, 
    pipeline_id UUID NOT NULL, 
    plugin_name VARCHAR(128) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    stream VARCHAR(256) NOT NULL, 
    partition VARCHAR(128) NOT NULL, 
    consumer VARCHAR(128) NOT NULL, 
    policy JSON NOT NULL, 
    plan JSON NOT NULL, 
    result_summary JSON NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    error TEXT, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_telemetry_tasks PRIMARY KEY (id), 
    CONSTRAINT ck_telemetry_tasks_ck_telemetry_tasks_status CHECK (status IN ('PLANNED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')), 
    CONSTRAINT fk_telemetry_tasks_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE, 
    CONSTRAINT fk_telemetry_tasks_pipeline_id_telemetry_pipelines FOREIGN KEY(pipeline_id) REFERENCES telemetry_pipelines (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_telemetry_tasks_task_id UNIQUE (task_id)
);

CREATE INDEX ix_telemetry_tasks_task_id ON telemetry_tasks (task_id);

CREATE INDEX ix_telemetry_tasks_pipeline_id ON telemetry_tasks (pipeline_id);

CREATE INDEX ix_telemetry_tasks_plugin_name ON telemetry_tasks (plugin_name);

CREATE INDEX ix_telemetry_tasks_status ON telemetry_tasks (status);

CREATE INDEX ix_telemetry_tasks_stream ON telemetry_tasks (stream);

CREATE INDEX ix_telemetry_tasks_partition ON telemetry_tasks (partition);

CREATE INDEX ix_telemetry_tasks_consumer ON telemetry_tasks (consumer);

CREATE TABLE telemetry_checkpoints (
    provider VARCHAR(64) NOT NULL, 
    stream VARCHAR(256) NOT NULL, 
    partition VARCHAR(128) NOT NULL, 
    consumer VARCHAR(128) NOT NULL, 
    "offset" INTEGER NOT NULL, 
    sequence INTEGER NOT NULL, 
    checksum VARCHAR(64), 
    metadata JSON NOT NULL, 
    committed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_telemetry_checkpoints PRIMARY KEY (id), 
    CONSTRAINT ck_telemetry_checkpoints_ck_telemetry_checkpoints_offset CHECK (offset >= 0), 
    CONSTRAINT ck_telemetry_checkpoints_ck_telemetry_checkpoints_sequence CHECK (sequence >= 0), 
    CONSTRAINT uq_telemetry_checkpoint_cursor UNIQUE (provider, stream, partition, consumer)
);

CREATE INDEX ix_telemetry_checkpoints_provider ON telemetry_checkpoints (provider);

CREATE INDEX ix_telemetry_checkpoints_stream ON telemetry_checkpoints (stream);

CREATE INDEX ix_telemetry_checkpoints_partition ON telemetry_checkpoints (partition);

CREATE INDEX ix_telemetry_checkpoints_consumer ON telemetry_checkpoints (consumer);

CREATE INDEX ix_telemetry_checkpoints_committed_at ON telemetry_checkpoints (committed_at);

CREATE TABLE telemetry_runtime_states (
    worker_id VARCHAR(128) NOT NULL, 
    pipeline_id UUID, 
    status VARCHAR(32) NOT NULL, 
    stream VARCHAR(256), 
    partition VARCHAR(128), 
    consumer VARCHAR(128), 
    current_offset INTEGER, 
    lag INTEGER NOT NULL, 
    queue_depth INTEGER NOT NULL, 
    backpressure_action VARCHAR(32), 
    metadata JSON NOT NULL, 
    heartbeat_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_telemetry_runtime_states PRIMARY KEY (id), 
    CONSTRAINT ck_telemetry_runtime_states_ck_telemetry_runtime_states_status CHECK (status IN ('IDLE', 'RUNNING', 'PAUSED', 'FAILED', 'STOPPED')), 
    CONSTRAINT ck_telemetry_runtime_states_ck_telemetry_runtime_states_lag CHECK (lag >= 0), 
    CONSTRAINT ck_telemetry_runtime_states_ck_telemetry_runtime_states_1f7f CHECK (queue_depth >= 0), 
    CONSTRAINT fk_telemetry_runtime_states_pipeline_id_telemetry_pipelines FOREIGN KEY(pipeline_id) REFERENCES telemetry_pipelines (id) ON DELETE SET NULL, 
    CONSTRAINT uq_telemetry_runtime_states_worker_id UNIQUE (worker_id)
);

CREATE INDEX ix_telemetry_runtime_states_worker_id ON telemetry_runtime_states (worker_id);

CREATE INDEX ix_telemetry_runtime_states_pipeline_id ON telemetry_runtime_states (pipeline_id);

CREATE INDEX ix_telemetry_runtime_states_status ON telemetry_runtime_states (status);

CREATE INDEX ix_telemetry_runtime_states_stream ON telemetry_runtime_states (stream);

CREATE INDEX ix_telemetry_runtime_states_heartbeat_at ON telemetry_runtime_states (heartbeat_at);

UPDATE alembic_version SET version_num='20260801_0013' WHERE alembic_version.version_num = '20260731_0012';

COMMIT;

