import axios from "axios";
import type {
  ApprovalItem,
  AuditEvent,
  Dashboard,
  DomainRecord,
  Health,
  PageResponse,
  PlatformUser,
  PluginItem,
  Role,
  SettingsView,
} from "../types";

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
