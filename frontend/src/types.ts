export interface Health {
  status: string;
  service: string;
  version: string;
}

export interface PageResponse<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface ExecutionSummary {
  total: number;
  succeeded: number;
  failed: number;
  waiting_approval: number;
  success_rate: number;
}

export interface Dashboard {
  counts: {
    assets: number;
    incidents: number;
    security_events: number;
    findings: number;
  };
  playbooks: ExecutionSummary;
  workers: {
    total: number;
    healthy: number;
    active_executions: number;
    capacity: number;
    utilization: number;
  };
  plugins: { total: number; healthy: number; enabled: number };
  responses: ExecutionSummary;
  notifications: ExecutionSummary;
}

export interface Role {
  name: string;
  description: string;
  permissions: string[];
}

export interface PlatformUser {
  username: string;
  display_name: string;
  roles: string[];
  permissions: string[];
}

export interface PluginItem {
  id: string;
  domain: string;
  name: string;
  version: string;
  enabled: boolean;
  health_status: string;
  capabilities: string[];
  certified: boolean;
  sandbox_compatible: boolean;
}

export interface ApprovalItem {
  plan_id: string;
  incident_id: string;
  capability: string;
  requested_by: string;
  risk_level: string;
  approval_state: string;
  execution_state: string;
  rollback_state: string;
  expires_at: string;
  approver?: string;
  decision?: string;
  comment?: string;
  decided_at?: string;
}

export interface AuditEvent {
  id: string;
  operator: string;
  action: string;
  resource: string;
  details: Record<string, unknown>;
  trace_id: string;
  result?: Record<string, unknown>;
  error?: string;
  timestamp: string;
}

export interface SettingsView {
  app_name: string;
  app_version: string;
  api_prefix: string;
  debug: boolean;
  log_level: string;
  cors_origins: string[];
  database_driver: string;
  redis_configured: boolean;
  rbac_enabled: boolean;
  identity_header: string;
  trusted_proxy_header: string;
  metrics_enabled: boolean;
  tracing_enabled: boolean;
  otel_service_name: string;
  otel_exporter_endpoint_configured: boolean;
}

export interface DomainRecord {
  id: string;
  name?: string;
  title?: string;
  status?: string;
  severity?: string;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

// ---- Incident 域 -----------------------------------------------------------

export interface IncidentTimelineEntry {
  id: string;
  event_type: string;
  actor: string;
  description: string;
  from_status: string | null;
  to_status: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface IncidentArtifact {
  id: string;
  artifact_type: string;
  reference_id: string | null;
  value: string | null;
  label: string | null;
  attributes: Record<string, unknown>;
  created_at: string;
}

export interface InvestigationCase {
  id: string;
  incident_id: string;
  title: string;
  status: string;
  owner: string | null;
  assignee: string | null;
  queue: string | null;
  started_at: string | null;
  completed_at: string | null;
  attributes: Record<string, unknown>;
}

export interface Incident {
  id: string;
  title: string;
  description: string;
  severity: string;
  priority: string;
  status: string;
  confidence: string;
  source: string;
  owner: string | null;
  assignee: string | null;
  queue: string | null;
  classification: string | null;
  risk: string | null;
  attributes: Record<string, unknown>;
  duplicate_of_id: string | null;
  sla_due_at: string | null;
  resolved_at: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  timelines: IncidentTimelineEntry[];
  artifacts: IncidentArtifact[];
  cases: InvestigationCase[];
}

// ---- Asset 域 --------------------------------------------------------------

export interface Asset {
  id: string;
  asset_type: string;
  name: string;
  value: string;
  canonical_value: string;
  owner: string | null;
  business_unit: string | null;
  environment: string | null;
  criticality: string | null;
  risk: string | null;
  tags: string[];
  capabilities: string[];
  properties: Record<string, unknown>;
  agent_id: string | null;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AssetRelation {
  id: string;
  source_asset_id: string;
  target_asset_id: string;
  relation_type: string;
  properties: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AssetEvidence {
  id: string;
  task_id: string;
  agent_id: string;
  trace_id: string;
  url: string;
  evidence_type: string;
  sha256: string;
  captured_at: string;
}

// ---- Detection 域 ------------------------------------------------------------

export interface SecurityEvent {
  id: string;
  detection_task_id: string;
  fingerprint: string;
  event_type: string;
  source: string;
  severity: string;
  confidence: string;
  timestamp: string;
  plugin: string;
  tool: string | null;
  rule: string | null;
  status: string;
  attributes: Record<string, unknown>;
  references: string[];
}

export interface DetectionTask {
  id: string;
  task_id: string;
  plugin_id: string | null;
  status: string;
  requested_capabilities: string[];
  policy: Record<string, unknown>;
  plan: Record<string, unknown>;
  result_summary: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

// ---- Assessment 域 ----------------------------------------------------------

export interface AssessmentTask {
  id: string;
  task_id: string;
  plugin_id: string | null;
  status: string;
  requested_capabilities: string[];
  policy: Record<string, unknown>;
  plan: Record<string, unknown>;
  result_summary: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Finding {
  id: string;
  assessment_task_id: string;
  duplicate_of_id: string | null;
  fingerprint: string;
  title: string;
  severity: string;
  confidence: string;
  description: string;
  affected_asset: string;
  plugin: string;
  tool: string | null;
  rule: string | null;
  risk_level: string;
  risk_score: number;
  status: string;
  attributes: Record<string, unknown>;
  references: string[];
  created_at: string;
  updated_at: string;
}

// ---- Response 域 ------------------------------------------------------------

export interface ResponseApproval {
  id: string;
  plan_id: string;
  approver: string;
  decision: string;
  comment: string;
  decided_at: string;
  [key: string]: unknown;
}

export interface ResponseExecution {
  id: string;
  plan_id: string;
  actor: string;
  state: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  [key: string]: unknown;
}

export interface ResponseRollback {
  id: string;
  plan_id: string;
  actor: string;
  reason: string;
  state: string;
  [key: string]: unknown;
}

export interface ResponsePlan {
  id: string;
  incident_id: string;
  plugin_id: string;
  target_capability: string;
  requested_by: string;
  reason: string;
  risk_level: string;
  approval_state: string;
  execution_state: string;
  rollback_state: string;
  policy_snapshot: Record<string, unknown>;
  plan: Record<string, unknown>;
  parameters: Record<string, unknown>;
  rollback_parameters: Record<string, unknown>;
  supports_rollback: boolean;
  expires_at: string;
  asset_ids: string[];
  approvals: ResponseApproval[];
  executions: ResponseExecution[];
  rollbacks: ResponseRollback[];
}

export interface ResponsePlugin {
  id: string;
  name: string;
  version: string;
  description: string | null;
  capabilities: string[];
  [key: string]: unknown;
}

// ---- Playbook 域 ------------------------------------------------------------

export interface PlaybookStep {
  id: string;
  step_id: string;
  node_type: string;
  capability: string | null;
  status: string;
  attempt: number;
  max_attempts: number;
  output: Record<string, unknown> | null;
  error: string | null;
  compensation_status: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface PlaybookExecution {
  id: string;
  playbook_id: string;
  trigger_type: string;
  status: string;
  actor: string;
  input: Record<string, unknown>;
  context: Record<string, unknown>;
  current_step: string | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  steps: PlaybookStep[];
}

export interface Playbook {
  id: string;
  name: string;
  version: string;
  description: string | null;
  enabled: boolean;
  document: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// ---- Worker / Sandbox -------------------------------------------------------

export interface Worker {
  id: string;
  name: string;
  runtime_version: string;
  capabilities: string[];
  status: string;
  max_concurrency: number;
  active_executions: number;
  registered_at: string;
  last_heartbeat_at: string;
}

export interface SandboxExecution {
  id: string;
  execution_id: string;
  worker_id: string;
  profile_id: string | null;
  plugin_name: string;
  plugin_version: string;
  operation: string;
  provider: string;
  status: string;
  result_metadata: Record<string, unknown>;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  timed_out: boolean;
}

// ---- Knowledge / Notification / Ticket -------------------------------------

export interface KnowledgeEntry {
  id: string;
  knowledge_type: string;
  external_id: string;
  source: string;
  version: string;
  title: string;
  description: string;
  references: string[];
  status: string;
  attributes: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface NotificationRecord {
  id: string;
  incident_id: string;
  response_plan_id: string | null;
  plugin_id: string;
  template_id: string;
  capability: string;
  recipient_group: string;
  recipients: string[];
  severity: string;
  priority: string;
  status: string;
  requested_by: string;
  deduplication_key: string;
  suppression_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface Ticket {
  id: string;
  incident_id: string | null;
  title: string;
  description: string;
  priority: string;
  status: string;
  external_reference: string | null;
  labels: string[];
  created_by: string;
  created_at: string;
  updated_at: string;
}
