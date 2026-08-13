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
