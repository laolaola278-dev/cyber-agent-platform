import axios from "axios";
import type {
  ApprovalItem,
  AssessmentTask,
  Asset,
  AuditEvent,
  Dashboard,
  DetectionTask,
  DomainRecord,
  Finding,
  Health,
  Incident,
  KnowledgeEntry,
  NotificationRecord,
  PageResponse,
  PlatformUser,
  Playbook,
  PlaybookExecution,
  PluginItem,
  ResponsePlan,
  ResponsePlugin,
  Role,
  SandboxExecution,
  SecurityEvent,
  SettingsView,
  Ticket,
  Worker,
} from "../types";
import type { ApprovalState, ExecutionState } from "./constants";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
  timeout: 10000,
  withCredentials: true,
});

export const getHealth = async (): Promise<Health> => (await api.get<Health>("/health")).data;
export const getDashboard = async (): Promise<Dashboard> =>
  (await api.get<Dashboard>("/dashboard")).data;
export const getRoles = async (): Promise<Role[]> => (await api.get<Role[]>("/roles")).data;
export const getUsers = async (): Promise<PlatformUser[]> =>
  (await api.get<PlatformUser[]>("/users")).data;
export const getPlugins = async (): Promise<PluginItem[]> =>
  (await api.get<PluginItem[]>("/plugins")).data;
export const getApprovals = async (): Promise<ApprovalItem[]> =>
  (await api.get<ApprovalItem[]>("/approvals")).data;
export const getAudit = async (): Promise<PageResponse<AuditEvent>> =>
  (await api.get<PageResponse<AuditEvent>>("/audit")).data;
export const getSettings = async (): Promise<SettingsView> =>
  (await api.get<SettingsView>("/settings")).data;

export interface Investigation {
  id: string;
  goal: string;
  status: string;
  conclusion: {
    summary?: string;
    confidence?: number;
    hypotheses?: Array<{ statement: string; evidence_refs: string[]; confidence: number }>;
    recommended_actions?: Array<{
      capability: string;
      action: string;
      risk: string;
      requires_approval: boolean;
    }>;
    unresolved_questions?: string[];
  } | null;
  conclusion_confidence: number | null;
  created_at: string;
  updated_at: string;
  run_id: string | null;
  plan: {
    goal: string;
    reasoning_summary: string;
    steps: Array<{ capability: string; purpose: string; risk: string; required_approval: boolean }>;
    requires_approval: boolean;
    risk_level: string;
  } | null;
  observations: Array<{ capability: string; summary: string; evidence_refs: string[]; confidence: number }>;
  decisions: Array<{ decision_type: string; rationale: string; capability: string | null }>;
  handoffs: Array<{ source_agent: string; target_agent: string; reason: string; status: string }>;
}

export const createInvestigation = async (goal: string, context?: Record<string, unknown>, dataBlocks?: Array<Record<string, unknown>>): Promise<Investigation> =>
  (await api.post<Investigation>("/agent/investigations", { goal, context: context ?? {}, data_blocks: dataBlocks ?? [] })).data;
export const getInvestigation = async (id: string): Promise<Investigation> =>
  (await api.get<Investigation>(`/agent/investigations/${id}`)).data;
export const continueInvestigation = async (id: string, goal?: string): Promise<Investigation> =>
  (await api.post<Investigation>(`/agent/investigations/${id}/continue`, { goal })).data;
export const getAgentRun = async (runId: string): Promise<Record<string, unknown>> =>
  (await api.get<Record<string, unknown>>(`/agent/runs/${runId}`)).data;
export const getEvaluations = async (): Promise<{ overall_score: number; metrics: Array<{ name: string; passed: number; total: number; rate: number }>; total_scenarios: number }> =>
  (await api.get("/agent/evaluations")).data;

export interface TriageResult {
  classification: string;
  severity_assessment: string;
  confidence: number;
  likely_false_positive: boolean;
  related_entities: string[];
  techniques: string[];
  recommended_investigation: string[];
  escalation_recommended: boolean;
  evidence_refs: string[];
  uncertainties: string[];
}

export const runTriage = async (
  source: Record<string, unknown>,
  context?: Record<string, unknown>,
): Promise<{ triage: TriageResult; evidence_grounded: boolean; model: string }> =>
  (await api.post("/agent/triage", { source, context: context ?? {} })).data;

export const runAttackChain = async (
  events: Array<Record<string, unknown>>,
  findings?: Array<Record<string, unknown>>,
): Promise<Record<string, unknown>> =>
  (await api.post("/agent/attack-chain", { events, findings: findings ?? [] })).data;

export const getModelComparison = async (): Promise<{
  scenario_count: number;
  fake: Record<string, unknown>;
  real: Record<string, unknown>;
  comparison: Record<string, unknown>;
  real_provider_note: string;
}> => (await api.get("/agent/model-comparison")).data;

export const getEvaluationsV2 = async (): Promise<{
  scenario_count: number;
  fake: Record<string, unknown>;
  real: Record<string, unknown>;
}> => (await api.get("/agent/evaluations/v2")).data;

export interface HybridTriageOutput {
  classification: string;
  severity: {
    severity: string;
    score: number;
    confidence: number;
    factors: Array<{ name: string; value: string; contribution: number }>;
  };
  false_positive: {
    false_positive_probability: number;
    confidence: number;
    likely_false_positive: boolean;
    factors: Array<{ name: string; value: string; direction: string }>;
  };
  technique_mapping: {
    technique_id: string | null;
    score: number;
    confidence: number;
    mapped_techniques: string[];
    unknown: boolean;
    explanation: string;
  };
  fact_count: number;
  knowledge_hits: Array<{ type: string; id: string; score: number }>;
  grounding: {
    claims: Array<{ claim: string; status: string; matched_refs: string[]; unmatched_refs: string[] }>;
    aggregate: Record<string, number>;
  };
  confidence: { confidence: number; components: Record<string, number>; basis: string };
  explanation: {
    statement: string;
    evidence_refs: string[];
    knowledge_refs: string[];
    factors: string[];
    model_generated: boolean;
  };
  chain_stages: string[];
  uncertainties: string[];
}

export const runHybridTriage = async (
  source: Record<string, unknown>,
  context?: Record<string, unknown>,
  preferReal = false,
): Promise<HybridTriageOutput> =>
  (
    await api.post("/agent/hybrid/triage", {
      source,
      context: context ?? {},
      prefer_real: preferReal,
    })
  ).data;

export const getHybridEvaluation = async (): Promise<{
  scenario_count: number;
  groups: Record<string, Record<string, number | string>>;
  note: string;
}> => (await api.get("/agent/hybrid/evaluation")).data;

const domainPaths: Record<string, string> = {
  assets: "/assets",
  knowledge: "/knowledge",
  evidence: "/assets?page_size=20",
  assessment: "/assessment/tasks",
  detection: "/detection/events",
  incidents: "/incidents",
  response: "/response/plans",
  playbooks: "/playbooks/executions",
  workers: "/workers",
  sandbox: "/sandbox",
};

export const getDomainRecords = async (domain: string): Promise<DomainRecord[]> => {
  const path = domainPaths[domain];
  if (!path) return [];
  const data = (await api.get<PageResponse<DomainRecord> | DomainRecord[]>(path)).data;
  return Array.isArray(data) ? data : data.items;
};

// -- Phase 28: Data Acquisition --------------------------------------------

export interface AcquisitionCreateRequest {
  goal: string;
  url: string;
  target_asset?: string;
  expected_fields?: string[];
  expected_time_range?: string[];
  expected_record_count?: number | null;
}

export interface AcquisitionSummary {
  id: string;
  goal: string;
  status: string;
  source_type: string;
  strategy: string;
  blocked_reason: string;
  total_bytes: number;
  total_requests: number;
  duration_seconds: number;
  replans: number;
}

export interface AcquisitionDetail {
  id: string;
  goal: string;
  status: string;
  source_type: string;
  strategy: string;
  blocked_reason: string;
  blocked_detail: string | null;
  replans: number;
  retries: number;
  total_bytes: number;
  total_requests: number;
  duration_seconds: number;
  strategy_history: string[];
}

export const createAcquisition = async (req: AcquisitionCreateRequest): Promise<AcquisitionSummary> =>
  (await api.post("/acquisitions", req)).data;

export const getAcquisitions = async (): Promise<{ items: AcquisitionSummary[] }> =>
  (await api.get("/acquisitions")).data;

export const getAcquisition = async (id: string): Promise<AcquisitionDetail> =>
  (await api.get(`/acquisitions/${id}`)).data;

export const getAcquisitionEvidence = async (id: string): Promise<{ run_id: string; evidence: unknown[] }> =>
  (await api.get(`/acquisitions/${id}/evidence`)).data;

export const getAcquisitionCompleteness = async (id: string): Promise<Record<string, unknown>> =>
  (await api.get(`/acquisitions/${id}/completeness`)).data;

// ---- Console v1.1: 域列表（带过滤/分页）与操作端点 ---------------------------

export interface IncidentFilters {
  severity?: string;
  status?: string;
  priority?: string;
  owner?: string;
  assignee?: string;
  queue?: string;
}

export const listIncidents = async (
  filters: IncidentFilters = {},
  page = 1,
  pageSize = 20,
): Promise<PageResponse<Incident>> =>
  (await api.get<PageResponse<Incident>>("/incidents", { params: { ...filters, page, page_size: pageSize } })).data;

export const getIncident = async (id: string): Promise<Incident> =>
  (await api.get<Incident>(`/incidents/${id}`)).data;

export const createIncident = async (payload: Record<string, unknown>): Promise<Incident> =>
  (await api.post<Incident>("/incidents", payload)).data;

export const transitionIncident = async (
  id: string,
  payload: { status: string; actor: string; reason?: string },
): Promise<Incident> =>
  (await api.post<Incident>(`/incidents/${id}/transition`, payload)).data;

export const assignIncident = async (
  id: string,
  payload: { actor: string; owner?: string; assignee?: string; queue?: string; priority?: string; reason?: string },
): Promise<Incident> =>
  (await api.post<Incident>(`/incidents/${id}/assign`, payload)).data;

export interface AssetFilters {
  name?: string;
  asset_type?: string;
  tag?: string;
  owner?: string;
  risk?: string;
  environment?: string;
  capability?: string;
}

export const listAssets = async (
  filters: AssetFilters = {},
  page = 1,
  pageSize = 20,
): Promise<PageResponse<Asset>> =>
  (await api.get<PageResponse<Asset>>("/assets", { params: { ...filters, page, page_size: pageSize } })).data;

export const getAsset = async (id: string): Promise<Asset> =>
  (await api.get<Asset>(`/assets/${id}`)).data;

export const createAsset = async (payload: Record<string, unknown>): Promise<Asset> =>
  (await api.post<Asset>("/assets", payload)).data;

export const updateAsset = async (id: string, payload: Record<string, unknown>): Promise<Asset> =>
  (await api.put<Asset>(`/assets/${id}`, payload)).data;

export const getAssetRelations = async (id: string): Promise<Record<string, unknown>[]> =>
  (await api.get(`/assets/${id}/relations`)).data;

export const getAssetEvidence = async (id: string): Promise<Record<string, unknown>[]> =>
  (await api.get(`/assets/${id}/evidence`)).data;

export interface EventFilters { severity?: string; status?: string; asset_id?: string }

export const listSecurityEvents = async (
  filters: EventFilters = {},
  page = 1,
  pageSize = 20,
): Promise<PageResponse<SecurityEvent>> =>
  (await api.get<PageResponse<SecurityEvent>>("/detection/events", { params: { ...filters, page, page_size: pageSize } })).data;

export const getSecurityEvent = async (id: string): Promise<SecurityEvent> =>
  (await api.get<SecurityEvent>(`/detection/events/${id}`)).data;

export const listDetectionTasks = async (page = 1, pageSize = 20): Promise<PageResponse<DetectionTask>> =>
  (await api.get<PageResponse<DetectionTask>>("/detection/tasks", { params: { page, page_size: pageSize } })).data;

export interface FindingFilters { severity?: string; status?: string; asset_id?: string }

export const listFindings = async (
  filters: FindingFilters = {},
  page = 1,
  pageSize = 20,
): Promise<PageResponse<Finding>> =>
  (await api.get<PageResponse<Finding>>("/assessment/findings", { params: { ...filters, page, page_size: pageSize } })).data;

export const getFinding = async (id: string): Promise<Finding> =>
  (await api.get<Finding>(`/assessment/findings/${id}`)).data;

export const transitionFinding = async (
  id: string,
  payload: { status: string; actor: string; reason?: string },
): Promise<Record<string, unknown>> =>
  (await api.post(`/assessment/findings/${id}/transition`, payload)).data;

export const listAssessmentTasks = async (page = 1, pageSize = 20): Promise<PageResponse<AssessmentTask>> =>
  (await api.get<PageResponse<AssessmentTask>>("/assessment/tasks", { params: { page, page_size: pageSize } })).data;

export interface ResponsePlanFilters {
  incident_id?: string;
  approval_state?: ApprovalState;
  execution_state?: ExecutionState;
}

export const listResponsePlans = async (
  filters: ResponsePlanFilters = {},
  page = 1,
  pageSize = 20,
): Promise<PageResponse<ResponsePlan>> =>
  (await api.get<PageResponse<ResponsePlan>>("/response/plans", { params: { ...filters, page, page_size: pageSize } })).data;

export const getResponsePlan = async (id: string): Promise<ResponsePlan> =>
  (await api.get<ResponsePlan>(`/response/plans/${id}`)).data;

export const createResponsePlan = async (payload: Record<string, unknown>): Promise<ResponsePlan> =>
  (await api.post<ResponsePlan>("/response/plans", payload)).data;

export const approveResponsePlan = async (
  id: string,
  payload: { approver: string; comment?: string; level?: number },
): Promise<ResponsePlan> =>
  (await api.post<ResponsePlan>(`/response/plans/${id}/approve`, payload)).data;

export const rejectResponsePlan = async (
  id: string,
  payload: { approver: string; comment: string },
): Promise<ResponsePlan> =>
  (await api.post<ResponsePlan>(`/response/plans/${id}/reject`, payload)).data;

export const executeResponsePlan = async (
  id: string,
  payload: { actor: string },
): Promise<ResponsePlan> =>
  (await api.post<ResponsePlan>(`/response/plans/${id}/execute`, payload)).data;

export const rollbackResponsePlan = async (
  id: string,
  payload: { actor: string; reason: string },
): Promise<ResponsePlan> =>
  (await api.post<ResponsePlan>(`/response/plans/${id}/rollback`, payload)).data;

export const listResponsePlugins = async (): Promise<ResponsePlugin[]> =>
  (await api.get<ResponsePlugin[]>("/response/plugins")).data;

export const listPlaybooks = async (page = 1, pageSize = 20): Promise<PageResponse<Playbook>> =>
  (await api.get<PageResponse<Playbook>>("/playbooks", { params: { page, page_size: pageSize } })).data;

export const getPlaybook = async (id: string): Promise<Playbook> =>
  (await api.get<Playbook>(`/playbooks/${id}`)).data;

export const listPlaybookExecutions = async (page = 1, pageSize = 20): Promise<PageResponse<PlaybookExecution>> =>
  (await api.get<PageResponse<PlaybookExecution>>("/playbooks/executions", { params: { page, page_size: pageSize } })).data;

export const getPlaybookExecution = async (id: string): Promise<PlaybookExecution> =>
  (await api.get<PlaybookExecution>(`/playbooks/executions/${id}`)).data;

export const resumePlaybookExecution = async (
  id: string,
  payload: { actor: string; input?: Record<string, unknown> },
): Promise<PlaybookExecution> =>
  (await api.post<PlaybookExecution>(`/playbooks/executions/${id}/resume`, payload)).data;

export const listWorkers = async (): Promise<Worker[]> =>
  (await api.get<Worker[]>("/workers")).data;

export const listSandboxExecutions = async (): Promise<SandboxExecution[]> =>
  (await api.get<SandboxExecution[]>("/sandbox")).data;

export const getSandboxExecution = async (id: string): Promise<SandboxExecution> =>
  (await api.get<SandboxExecution>(`/sandbox/${id}`)).data;

export interface KnowledgeFilters { knowledge_type?: string; source?: string; status?: string }

export const listKnowledge = async (
  filters: KnowledgeFilters = {},
  page = 1,
  pageSize = 20,
): Promise<PageResponse<KnowledgeEntry>> =>
  (await api.get<PageResponse<KnowledgeEntry>>("/knowledge", { params: { ...filters, page, page_size: pageSize } })).data;

export const searchKnowledge = async (
  q: string,
  page = 1,
  pageSize = 20,
): Promise<PageResponse<KnowledgeEntry>> =>
  (await api.get<PageResponse<KnowledgeEntry>>("/knowledge/search", { params: { q, page, page_size: pageSize } })).data;

export interface NotificationFilters { incident_id?: string; status?: string }

export const listNotifications = async (
  filters: NotificationFilters = {},
  page = 1,
  pageSize = 20,
): Promise<PageResponse<NotificationRecord>> =>
  (await api.get<PageResponse<NotificationRecord>>("/notifications", { params: { ...filters, page, page_size: pageSize } })).data;

export const listTickets = async (status?: string, page = 1, pageSize = 20): Promise<PageResponse<Ticket>> =>
  (await api.get<PageResponse<Ticket>>("/tickets", { params: { status, page, page_size: pageSize } })).data;

export interface AuditFilters {
  operator?: string;
  event_type?: string;
  resource?: string;
  plugin?: string;
  incident?: string;
  worker?: string;
  start?: string;
  end?: string;
}

export const queryAudit = async (
  filters: AuditFilters = {},
  page = 1,
  pageSize = 20,
): Promise<PageResponse<AuditEvent>> =>
  (await api.get<PageResponse<AuditEvent>>("/audit", { params: { ...filters, page, page_size: pageSize } })).data;
