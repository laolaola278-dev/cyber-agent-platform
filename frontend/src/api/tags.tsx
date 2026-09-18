import type { ReactNode } from "react";
import { Tag } from "antd";

// JSX render helpers live here, apart from the pure data in `constants.ts`,
// so each file exports one kind of thing. `api/constants.ts` re-exports these
// under their original names, so no call site changes.

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
