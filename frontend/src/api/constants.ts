// Pure status/severity data and formatting helpers. The two JSX renderers live
// in `tags.tsx` and are re-exported here under their original names, so a page
// can keep importing everything from `../api/constants` while each file exports
// a single kind of thing (react-refresh fast-refresh boundary).

export { severityTag, statusTag } from "./tags";

export const SEVERITIES = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export const PRIORITIES = ["P1", "P2", "P3", "P4"] as const;
export const CONFIDENCES = ["LOW", "MEDIUM", "HIGH"] as const;
export const INCIDENT_STATUSES = [
  "NEW", "TRIAGED", "INVESTIGATING", "CONTAINED", "RESOLVED", "CLOSED", "REOPENED",
] as const;
export const FINDING_STATUSES = [
  "NEW", "TRIAGED", "CONFIRMED", "FALSE_POSITIVE", "ACCEPTED_RISK", "FIXED", "REOPENED",
] as const;
export const EVENT_STATUSES = ["NEW", "CORRELATED", "TRIAGED", "IGNORED", "ARCHIVED"] as const;
export const APPROVAL_STATES = [
  "DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "EXPIRED", "EXECUTED", "ROLLED_BACK",
] as const;
export const EXECUTION_STATES = [
  "PLANNED", "BLOCKED", "READY", "RUNNING", "SUCCEEDED", "FAILED", "VERIFIED",
] as const;
export const ROLLBACK_STATES = [
  "NOT_SUPPORTED", "AVAILABLE", "RUNNING", "SUCCEEDED", "FAILED", "VERIFIED",
] as const;
export const ASSET_TYPES = [
  "DOMAIN", "IP", "HOST", "WEBSITE", "APPLICATION", "CONTAINER", "CLOUD_RESOURCE",
  "REPOSITORY", "DOCUMENT", "USER", "AGENT",
] as const;
export const NOTIFICATION_STATUSES = [
  "PLANNED", "SUPPRESSED", "RUNNING", "SENT", "VERIFIED", "FAILED",
] as const;
export const TICKET_STATUSES = ["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"] as const;
export const TICKET_PRIORITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export const RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export const RESPONSE_CAPABILITIES = [
  "response.notify", "response.ticket", "response.block", "response.isolate",
  "response.rollback", "response.waf", "response.firewall", "response.edr", "response.custom",
] as const;

export type Severity = (typeof SEVERITIES)[number];
export type IncidentStatus = (typeof INCIDENT_STATUSES)[number];
export type FindingStatus = (typeof FINDING_STATUSES)[number];
export type EventStatus = (typeof EVENT_STATUSES)[number];
export type ApprovalState = (typeof APPROVAL_STATES)[number];
export type ExecutionState = (typeof EXECUTION_STATES)[number];
export type RollbackState = (typeof ROLLBACK_STATES)[number];
export type AssetType = (typeof ASSET_TYPES)[number];
export type NotificationStatus = (typeof NOTIFICATION_STATUSES)[number];
export type TicketStatus = (typeof TICKET_STATUSES)[number];
export type TicketPriority = (typeof TICKET_PRIORITIES)[number];
export type RiskLevel = (typeof RISK_LEVELS)[number];
export type Priority = (typeof PRIORITIES)[number];

export const statusColor = (value?: string): string => {
  const status = value?.toUpperCase() ?? "UNKNOWN";
  if (["OK", "HEALTHY", "ONLINE", "SUCCEEDED", "VERIFIED", "APPROVED", "EXECUTED"].includes(status)) return "success";
  if (["FAILED", "ERROR", "REJECTED", "OFFLINE"].includes(status)) return "error";
  if (["RUNNING", "PENDING", "PENDING_APPROVAL", "WAITING_APPROVAL"].includes(status)) return "processing";
  return "default";
};

export const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString("zh-CN") : "—";
