import type { ReactNode } from "react";
import { Tag } from "antd";

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

const SEVERITY_COLOR: Record<string, string> = {
  INFO: "default", LOW: "blue", MEDIUM: "gold", HIGH: "orange", CRITICAL: "red",
};
const STATUS_COLOR: Record<string, string> = {
  NEW: "blue", TRIAGED: "geekblue", CONFIRMED: "orange", INVESTIGATING: "processing",
  CONTAINED: "purple", RESOLVED: "green", CLOSED: "default", REOPENED: "volcano",
  FALSE_POSITIVE: "default", ACCEPTED_RISK: "default", FIXED: "green",
  CORRELATED: "geekblue", IGNORED: "default", ARCHIVED: "default",
  DRAFT: "default", PENDING_APPROVAL: "gold", APPROVED: "green", REJECTED: "red",
  EXPIRED: "default", EXECUTED: "green", ROLLED_BACK: "purple",
  PLANNED: "default", BLOCKED: "red", READY: "cyan", RUNNING: "processing",
  SUCCEEDED: "green", FAILED: "red", VERIFIED: "green",
  NOT_SUPPORTED: "default", AVAILABLE: "cyan",
  OPEN: "blue", IN_PROGRESS: "processing", SENT: "green", SUPPRESSED: "default",
  ON_HOLD: "orange", ACTIVE: "processing", COMPLETED: "green",
};

export const severityTag = (value?: string): ReactNode =>
  <Tag color={SEVERITY_COLOR[value ?? ""] ?? "default"}>{value ?? "—"}</Tag>;

export const statusTag = (value?: string): ReactNode =>
  <Tag color={STATUS_COLOR[value ?? ""] ?? "default"}>{value ?? "—"}</Tag>;

export const statusColor = (value?: string): string => {
  const status = value?.toUpperCase() ?? "UNKNOWN";
  if (["OK", "HEALTHY", "ONLINE", "SUCCEEDED", "VERIFIED", "APPROVED", "EXECUTED"].includes(status)) return "success";
  if (["FAILED", "ERROR", "REJECTED", "OFFLINE"].includes(status)) return "error";
  if (["RUNNING", "PENDING", "PENDING_APPROVAL", "WAITING_APPROVAL"].includes(status)) return "processing";
  return "default";
};

export const formatTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString("zh-CN") : "—";
