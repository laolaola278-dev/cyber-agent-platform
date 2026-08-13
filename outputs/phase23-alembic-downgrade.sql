BEGIN;

-- Running downgrade 20260803_0018 -> 20260802_0017

DROP INDEX ix_playbook_step_executions_capability;

DROP INDEX ix_playbook_step_executions_status;

DROP INDEX ix_playbook_step_executions_node_type;

DROP INDEX ix_playbook_step_executions_execution_id;

DROP TABLE playbook_step_executions;

DROP INDEX ix_playbook_executions_trace_id;

DROP INDEX ix_playbook_executions_status;

DROP INDEX ix_playbook_executions_trigger_type;

DROP INDEX ix_playbook_executions_trigger_id;

DROP INDEX ix_playbook_executions_playbook_version_id;

DROP INDEX ix_playbook_executions_playbook_id;

DROP TABLE playbook_executions;

DROP INDEX ix_playbook_triggers_enabled;

DROP INDEX ix_playbook_triggers_trigger_type;

DROP INDEX ix_playbook_triggers_playbook_version_id;

DROP TABLE playbook_triggers;

DROP INDEX ix_playbook_versions_playbook_id;

DROP TABLE playbook_versions;

DROP INDEX ix_playbooks_enabled;

DROP INDEX ix_playbooks_name;

DROP TABLE playbooks;

UPDATE alembic_version SET version_num='20260802_0017' WHERE alembic_version.version_num = '20260803_0018';

-- Running downgrade 20260802_0017 -> 20260802_0016

ALTER TABLE sandbox_executions DROP CONSTRAINT fk_sandbox_executions_lease_id;

DROP INDEX ix_sandbox_executions_recovery_of_execution_id;

DROP INDEX ix_sandbox_executions_lease_id;

ALTER TABLE sandbox_executions DROP COLUMN recovery_of_execution_id;

ALTER TABLE sandbox_executions DROP COLUMN attempt;

ALTER TABLE sandbox_executions DROP COLUMN lease_version;

ALTER TABLE sandbox_executions DROP COLUMN lease_id;

DROP INDEX ix_worker_leases_fencing_token;

ALTER TABLE worker_leases DROP COLUMN fencing_token;

ALTER TABLE workers DROP COLUMN state_version;

UPDATE alembic_version SET version_num='20260802_0016' WHERE alembic_version.version_num = '20260802_0017';

-- Running downgrade 20260802_0016 -> 20260801_0015

DROP TABLE sandbox_executions;

DROP TABLE worker_leases;

DROP TABLE secret_references;

DROP TABLE sandbox_profiles;

DROP TABLE workers;

UPDATE alembic_version SET version_num='20260801_0015' WHERE alembic_version.version_num = '20260802_0016';

-- Running downgrade 20260801_0015 -> 20260801_0014

DROP TABLE tickets;

DROP TABLE notification_evidence;

DROP TABLE notification_executions;

DROP TABLE notification_plans;

DROP TABLE notification_templates;

DROP TABLE notification_plugins;

UPDATE alembic_version SET version_num='20260801_0014' WHERE alembic_version.version_num = '20260801_0015';

-- Running downgrade 20260801_0014 -> 20260801_0013

DROP TABLE response_evidence;

DROP TABLE response_rollbacks;

DROP TABLE response_executions;

DROP TABLE response_approvals;

DROP TABLE response_plan_assets;

DROP TABLE response_plans;

DROP TABLE response_policies;

DROP TABLE response_plugins;

UPDATE alembic_version SET version_num='20260801_0013' WHERE alembic_version.version_num = '20260801_0014';

-- Running downgrade 20260801_0013 -> 20260731_0012

DROP TABLE telemetry_runtime_states;

DROP TABLE telemetry_checkpoints;

DROP TABLE telemetry_tasks;

DROP TABLE telemetry_pipelines;

UPDATE alembic_version SET version_num='20260731_0012' WHERE alembic_version.version_num = '20260801_0013';

-- Running downgrade 20260731_0012 -> 20260731_0011

DROP TABLE incident_knowledge;

DROP TABLE incident_assets;

DROP TABLE incident_events;

DROP TABLE incident_findings;

DROP TABLE case_comments;

DROP TABLE investigation_cases;

DROP TABLE incident_artifacts;

DROP TABLE incident_timelines;

DROP TABLE incidents;

UPDATE alembic_version SET version_num='20260731_0011' WHERE alembic_version.version_num = '20260731_0012';

-- Running downgrade 20260731_0011 -> 20260731_0010

DROP TABLE event_knowledge;

DROP TABLE event_assets;

DROP TABLE event_evidence;

DROP TABLE event_references;

DROP TABLE security_events;

DROP TABLE detection_tasks;

DROP TABLE detection_capabilities;

DROP TABLE detection_plugins;

UPDATE alembic_version SET version_num='20260731_0010' WHERE alembic_version.version_num = '20260731_0011';

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

-- Running downgrade 20260731_0009 -> 20260730_0008

DROP TABLE finding_knowledge;

DROP TABLE finding_assets;

DROP TABLE finding_evidence;

DROP TABLE finding_references;

DROP TABLE findings;

DROP TABLE assessment_tasks;

DROP TABLE assessment_capabilities;

DROP TABLE assessment_plugins;

UPDATE alembic_version SET version_num='20260730_0008' WHERE alembic_version.version_num = '20260731_0009';

-- Running downgrade 20260730_0008 -> 20260730_0007

DROP TABLE report_knowledge;

DROP TABLE evidence_knowledge;

DROP TABLE asset_knowledge;

DROP TABLE knowledge_relations;

DROP TABLE knowledge_versions;

DROP TABLE knowledge;

DROP TABLE knowledge_sources;

UPDATE alembic_version SET version_num='20260730_0007' WHERE alembic_version.version_num = '20260730_0008';

-- Running downgrade 20260730_0007 -> 20260730_0006

DROP INDEX ix_workflow_instances_asset_id;

ALTER TABLE workflow_instances DROP CONSTRAINT fk_workflow_instances_asset_id_assets;

ALTER TABLE workflow_instances DROP COLUMN asset_id;

DROP INDEX ix_tasks_asset_id;

ALTER TABLE tasks DROP CONSTRAINT fk_tasks_asset_id_assets;

ALTER TABLE tasks DROP COLUMN asset_id;

DROP TABLE asset_reports;

DROP TABLE asset_evidence;

DROP TABLE asset_tags;

DROP TABLE asset_relations;

DROP TABLE assets;

UPDATE alembic_version SET version_num='20260730_0006' WHERE alembic_version.version_num = '20260730_0007';

-- Running downgrade 20260730_0006 -> 20260729_0005

DROP TABLE workflow_executions;

DROP TABLE workflow_steps;

DROP TABLE workflow_instances;

DROP TABLE workflow_definitions;

UPDATE alembic_version SET version_num='20260729_0005' WHERE alembic_version.version_num = '20260730_0006';

-- Running downgrade 20260729_0005 -> 20260729_0004

ALTER TABLE reports DROP COLUMN html_content;

DROP INDEX ix_evidence_evidence_type;

ALTER TABLE evidence DROP COLUMN object_storage_path;

ALTER TABLE evidence DROP COLUMN content_type;

ALTER TABLE evidence DROP COLUMN sha256;

ALTER TABLE evidence DROP COLUMN evidence_type;

DROP TABLE agent_capabilities;

DROP TABLE capabilities;

ALTER TABLE tasks DROP COLUMN required_capabilities;

ALTER TABLE agents DROP COLUMN sdk_version;

ALTER TABLE agents DROP COLUMN platform_version;

ALTER TABLE agents DROP COLUMN minimum_runtime_version;

ALTER TABLE agents DROP COLUMN capabilities;

UPDATE alembic_version SET version_num='20260729_0004' WHERE alembic_version.version_num = '20260729_0005';

-- Running downgrade 20260729_0004 -> 20260729_0003

DROP TABLE reports;

DROP TABLE evidence;

DROP TABLE agent_runtimes;

UPDATE alembic_version SET version_num='20260729_0003' WHERE alembic_version.version_num = '20260729_0004';

-- Running downgrade 20260729_0003 -> 20260729_0002

ALTER TABLE task_executions DROP CONSTRAINT ck_task_executions_ck_task_executions_status;

ALTER TABLE tasks DROP CONSTRAINT ck_tasks_ck_tasks_status;

ALTER TABLE agents DROP CONSTRAINT ck_agents_ck_agents_status;

UPDATE alembic_version SET version_num='20260729_0002' WHERE alembic_version.version_num = '20260729_0003';

-- Running downgrade 20260729_0002 -> 20260729_0001

DROP INDEX ix_execution_logs_timestamp;

DROP INDEX ix_execution_logs_execution_id;

DROP TABLE execution_logs;

DROP INDEX ix_task_executions_trace_id;

ALTER TABLE task_executions DROP COLUMN trace_id;

DROP INDEX ix_task_logs_trace_id;

DROP INDEX ix_task_logs_task_id;

DROP TABLE task_logs;

DROP INDEX ix_tasks_target_agent_id;

ALTER TABLE tasks DROP COLUMN target_agent_id;

ALTER TABLE tasks DROP COLUMN required_permissions;

DROP INDEX ix_tool_versions_tool_id;

DROP TABLE tool_versions;

DROP INDEX ix_tools_status;

ALTER TABLE tools DROP COLUMN updated_at;

ALTER TABLE tools DROP COLUMN created_at;

ALTER TABLE tools DROP COLUMN status;

ALTER TABLE tools DROP COLUMN runtime_requirements;

ALTER TABLE tools DROP COLUMN required_permissions;

ALTER TABLE tools RENAME config_schema TO config;

ALTER TABLE tools RENAME tool_type TO type;

DROP INDEX ix_agent_heartbeats_timestamp;

DROP INDEX ix_agent_heartbeats_agent_id;

DROP TABLE agent_heartbeats;

DROP INDEX ix_agent_versions_agent_id;

DROP TABLE agent_versions;

DROP INDEX ix_agents_health_status;

ALTER TABLE agents DROP COLUMN heartbeat_time;

ALTER TABLE agents DROP COLUMN health_status;

ALTER TABLE agents DROP COLUMN approval_policy;

ALTER TABLE agents DROP COLUMN resource_limit;

ALTER TABLE agents DROP COLUMN network_policy;

ALTER TABLE agents DROP COLUMN runtime;

ALTER TABLE agents DROP COLUMN author;

ALTER TABLE tools DROP CONSTRAINT uq_tools_name;

ALTER TABLE tools ADD CONSTRAINT uq_tools_name_version UNIQUE (name, version);

ALTER TABLE agents DROP CONSTRAINT uq_agents_name;

ALTER TABLE agents ADD CONSTRAINT uq_agents_name_version UNIQUE (name, version);

DROP INDEX ix_audit_logs_tool_id;

DROP INDEX ix_audit_logs_task_id;

DROP INDEX ix_audit_logs_agent_id;

DROP INDEX ix_audit_logs_trace_id;

ALTER TABLE audit_logs DROP COLUMN error;

ALTER TABLE audit_logs DROP COLUMN result;

ALTER TABLE audit_logs DROP COLUMN tool_id;

ALTER TABLE audit_logs DROP COLUMN task_id;

ALTER TABLE audit_logs DROP COLUMN agent_id;

ALTER TABLE audit_logs DROP COLUMN trace_id;

UPDATE alembic_version SET version_num='20260729_0001' WHERE alembic_version.version_num = '20260729_0002';

-- Running downgrade 20260729_0001 -> 

DROP INDEX ix_task_executions_task_id;

DROP INDEX ix_task_executions_status;

DROP INDEX ix_task_executions_agent_id;

DROP TABLE task_executions;

DROP INDEX ix_tools_type;

DROP INDEX ix_tools_name;

DROP TABLE tools;

DROP INDEX ix_tasks_task_type;

DROP INDEX ix_tasks_status;

DROP INDEX ix_tasks_name;

DROP TABLE tasks;

DROP INDEX ix_audit_logs_timestamp;

DROP INDEX ix_audit_logs_resource;

DROP INDEX ix_audit_logs_operator;

DROP INDEX ix_audit_logs_action;

DROP TABLE audit_logs;

DROP INDEX ix_agents_status;

DROP INDEX ix_agents_name;

DROP TABLE agents;

DELETE FROM alembic_version WHERE alembic_version.version_num = '20260729_0001';

DROP TABLE alembic_version;

COMMIT;

