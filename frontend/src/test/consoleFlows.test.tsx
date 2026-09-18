import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntApp } from "antd";
import { describe, expect, it } from "vitest";
import { calls, resetCalls, stub } from "./httpMock";
import App from "../App";
import WorkersPage from "../pages/WorkersPage";
import InvestigationsPage from "../pages/InvestigationsPage";
import AcquisitionsPage from "../pages/AcquisitionsPage";
import ApprovalsPage from "../pages/ApprovalsPage";
import AssetsPage from "../pages/AssetsPage";
import IncidentsPage from "../pages/IncidentsPage";
import PluginsPage from "../pages/PluginsPage";

/**
 * Core-flow tests for the defects closed in the delivery audit. Each one fails
 * against the pre-fix code, which is the point.
 */

const mount = (ui: React.ReactElement) => render(<AntApp>{ui}</AntApp>);

const runSummary = (id: string, goal: string, status: string) => ({
  id, goal, status, source_type: "HTTP", strategy: "static", blocked_reason: "NONE",
  total_bytes: 0, total_requests: 0, duration_seconds: 0, replans: 0,
});

const runDetail = (id: string, goal: string, status: string) => ({
  ...runSummary(id, goal, status),
  blocked_detail: null, retries: 0, strategy_history: [],
});

/** Exactly what `GET /dashboard` returns (backend `DashboardRead`). */
const dashboardView = {
  counts: { assets: 3, incidents: 2, security_events: 11, findings: 4 },
  playbooks: { total: 5, succeeded: 4, failed: 1, waiting_approval: 0, success_rate: 0.8 },
  workers: { total: 2, healthy: 2, active_executions: 1, capacity: 8, utilization: 0.125 },
  plugins: { total: 6, healthy: 6, enabled: 5 },
  responses: { total: 3, succeeded: 3, failed: 0, waiting_approval: 0, success_rate: 1 },
  notifications: { total: 7, succeeded: 6, failed: 1, waiting_approval: 0, success_rate: 0.86 },
};

describe("shell", () => {
  it("reports the version the platform is actually running", async () => {
    // The shell mounts DashboardPage, which reads every aggregate off this
    // payload, so a partial envelope would crash the app rather than test it.
    stub({
      "/health": { data: { status: "ok", service: "cap-backend", version: "1.0.5" } },
      "/dashboard": { data: dashboardView },
    });

    mount(<App />);

    // Used to be a hardcoded literal that contradicted VERSION.
    expect(await screen.findByText("v1.0.5")).toBeInTheDocument();
    expect(screen.queryByText("v2.0")).not.toBeInTheDocument();
  });
});

describe("Workers & Sandbox", () => {
  it("opens the sandbox execution detail drawer from a row", async () => {
    stub({
      "/workers": { data: [] },
      "/sandbox": {
        data: [{
          id: "s1", execution_id: "exec-1", worker_id: "w-1", profile_id: null,
          plugin_name: "whois", plugin_version: "1.2.0", operation: "lookup",
          provider: "sandbox", status: "SUCCEEDED", timed_out: false,
          started_at: null, finished_at: null, result_metadata: {}, error: null,
        }],
      },
    });

    mount(<WorkersPage />);

    const tabs = await screen.findByRole("tablist");
    await userEvent.click(within(tabs).getByRole("tab", { name: "沙箱执行" }));
    await userEvent.click(await screen.findByText("whois"));

    const drawer = await screen.findByRole("dialog");
    expect(drawer).toHaveTextContent("沙箱执行 · whois");
    expect(drawer).toHaveTextContent("exec-1");
  });
});

describe("Investigation", () => {
  it("refuses to analyse without a real record and posts nothing", async () => {
    resetCalls();
    stub({
      "/incidents": { data: { items: [], page: 1, page_size: 50, total: 0 } },
      "/detection/events": { data: { items: [], page: 1, page_size: 20, total: 0 } },
    });

    mount(<InvestigationsPage />);

    const triage = await screen.findByRole("button", { name: "Triage 选中事件" });
    expect(triage).toBeDisabled();
    expect(screen.getByRole("button", { name: "Hybrid Triage" })).toBeDisabled();

    await userEvent.click(triage);
    expect(calls("post")).toHaveLength(0);
  });

  it("triages the selected real incident, carrying its own id", async () => {
    resetCalls();
    stub({
      "/incidents": {
        data: [{
          id: "inc-77", title: "Suspicious SMB traffic", severity: "HIGH", status: "NEW",
          description: "", priority: "P2", confidence: "MEDIUM", source: "DETECTION",
          owner: null, assignee: null, queue: null, classification: null, risk: null,
          attributes: {}, finding_ids: ["f1"], event_ids: ["e1"],
        }],
      },
      "/detection/events": { data: [] },
      "/agent/triage": { data: { triage: {
        classification: "SUSPICIOUS", severity_assessment: "HIGH", confidence: 0.7,
        likely_false_positive: false, escalation_recommended: true, techniques: [],
        uncertainties: [],
      }, evidence_grounded: true, model: "deterministic" } },
    });

    mount(<InvestigationsPage />);

    // The picker is an antd Select: the option list only exists once the
    // dropdown is open, and the option is labelled with the incident's
    // severity/status prefix, never with the bare title.
    await userEvent.click(await screen.findByRole("combobox"));
    await userEvent.click(await screen.findByText("[HIGH/NEW] Suspicious SMB traffic"));

    await userEvent.click(await screen.findByRole("button", { name: "Triage 选中事件" }));

    await waitFor(() => expect(calls("post")).toHaveLength(1));
    const [post] = calls("post");
    expect(post.url).toBe("/agent/triage");
    // axios serialises the body before the adapter sees it, so the recorded
    // request carries JSON text exactly as it goes over the wire.
    const body = JSON.parse(String(post.config.data)) as { source: Record<string, unknown> };
    // Real lineage from the stored record, not a fabricated fixture.
    expect(body.source.id).toBe("inc-77");
    expect(body.source.evidence_refs).toEqual(["finding://f1", "event://e1"]);
  });
});

describe("Data Acquisition", () => {
  it("does not offer resume or cancel on a terminal run", async () => {
    stub({ "/acquisitions": { data: { items: [runSummary("run-1", "collect CVE advisories", "COMPLETE")] } } });

    mount(<AcquisitionsPage />);

    await screen.findByText("collect CVE advisories");
    const table = screen.getByRole("table");
    expect(within(table).getByRole("button", { name: "继续" })).toBeDisabled();
    expect(within(table).getByRole("button", { name: "取消" })).toBeDisabled();
  });

  it("resumes a non-terminal run through the checkpoint endpoint", async () => {
    resetCalls();
    stub({
      "/acquisitions": { data: { items: [runSummary("run-9", "collect advisories", "PARTIAL")] } },
      "/acquisitions/run-9": { data: runDetail("run-9", "collect advisories", "PARTIAL") },
      "/acquisitions/run-9/evidence": { data: { evidence: [] } },
      "/acquisitions/run-9/completeness": { data: { verdict: "PARTIAL" } },
      "POST /acquisitions/run-9/resume": { data: { id: "run-9", status: "QUEUED", resumed: true } },
    });

    mount(<AcquisitionsPage />);

    // The row action only arms the confirmation; the request belongs to the
    // operator approving it.
    const table = await screen.findByRole("table");
    await userEvent.click(within(table).getByRole("button", { name: "继续" }));
    const confirm = await screen.findByRole("tooltip");
    // antd pads this primary button's two-character label ("继 续"), so the
    // name is matched tolerantly but only inside the confirmation.
    await userEvent.click(within(confirm).getByRole("button", { name: /继\s*续/ }));

    await waitFor(() => expect(calls("post").some((call) => call.url === "/acquisitions/run-9/resume")).toBe(true));
  });

  it("clears a previous run's evidence when the new run's evidence fails", async () => {
    stub({
      "/acquisitions": {
        data: { items: [runSummary("run-a", "A", "RUNNING"), runSummary("run-b", "B", "RUNNING")] },
      },
      "/acquisitions/run-a": { data: runDetail("run-a", "A", "RUNNING") },
      "/acquisitions/run-a/evidence": {
        data: { evidence: [{ object_key: "k-a", sha256: "sha-aaa", content_type: "text/html", http_status: 200, final_url: "u", tool: "t" }] },
      },
      "/acquisitions/run-a/completeness": { data: { verdict: "OK" } },
      "/acquisitions/run-b": { data: runDetail("run-b", "B", "RUNNING") },
      "/acquisitions/run-b/evidence": { status: 500, detail: "evidence store unreachable" },
    });

    mount(<AcquisitionsPage />);

    await userEvent.click(await screen.findByText("A"));
    await screen.findByText("sha-aaa");

    // Run B's evidence request fails: run A's artifact must not linger under it.
    await userEvent.click(screen.getByText("B"));

    expect(await screen.findByText("运行详情 · B")).toBeInTheDocument();
    expect(screen.queryByText("sha-aaa")).not.toBeInTheDocument();
    expect(screen.getByText("证据加载失败")).toBeInTheDocument();
  });
});

describe("Assets", () => {
  it("shows a retryable error rather than an endless loading spinner in the drawer", async () => {
    stub({
      "/assets": { data: [{ id: "a1", name: "payments.example", asset_type: "DOMAIN", environment: "prod", criticality: "HIGH", owner: null, tags: [], capabilities: [], attributes: {}, risk: null, created_at: null, updated_at: null }] },
      "/assets/a1": { status: 500, detail: "asset store unavailable" },
    });

    mount(<AssetsPage />);

    // The drawer is opened by the row's 详情 action; the name cell is inert.
    // antd pads a two-character CJK label with a typographic space, so the
    // name is matched with optional whitespace rather than pinned.
    await screen.findByText("payments.example");
    const table = screen.getByRole("table");
    await userEvent.click(within(table).getByRole("button", { name: /详\s*情/ }));

    const drawer = await screen.findByRole("dialog");
    await waitFor(() => expect(drawer).toHaveTextContent("数据加载失败"));
    expect(drawer).not.toHaveTextContent("加载中…");
    // ListError pins this control's accessible name, so it is matched exactly.
    expect(within(drawer).getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});

/** Exactly what `GET /approvals` returns for one plan (ApprovalCenterItem). */
const approvalRow = (overrides: Record<string, unknown> = {}) => ({
  plan_id: "plan-1", incident_id: "inc-1", capability: "response.block",
  requested_by: "soc-analyst", risk_level: "HIGH", approval_state: "PENDING_APPROVAL",
  execution_state: "PLANNED", rollback_state: "AVAILABLE",
  expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
  approver: null, decision: null, comment: null, decided_at: null,
  ...overrides,
});

describe("Approval Center", () => {
  it("labels a queue item the platform has already given up on", async () => {
    stub({});
    mount(
      <ApprovalsPage approvals={[approvalRow({
        expires_at: new Date(Date.now() - 60_000).toISOString(),
      })] as never} loading={false} />,
    );

    await screen.findByText("response.block");
    // Before this the row read "待审批" forever, so an operator worked a plan
    // the server would refuse.
    expect(screen.getAllByText("已过期").length).toBeGreaterThan(0);
    expect(screen.getByText("soc-analyst")).toBeInTheDocument();
    expect(screen.getByText("inc-1", { exact: false })).toBeInTheDocument();
  });

  it("posts the decision to the plan's approval endpoint and reloads the queue", async () => {
    resetCalls();
    stub({
      "POST /response/plans/plan-1/approve": { data: { id: "plan-1", approval_state: "APPROVED" } },
    });
    let reloaded = 0;
    mount(
      <ApprovalsPage approvals={[approvalRow()] as never} loading={false} onChanged={() => { reloaded += 1; }} />,
    );

    const table = await screen.findByRole("table");
    await userEvent.click(within(table).getByRole("button", { name: /批\s*准/ }));

    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "确认批准" }));

    await waitFor(() => expect(calls("post")).toHaveLength(1));
    const [post] = calls("post");
    expect(post.url).toBe("/response/plans/plan-1/approve");
    const body = JSON.parse(String(post.config.data)) as { approver: string };
    expect(body.approver).toBe("console-operator");
    await waitFor(() => expect(reloaded).toBe(1));
  });

  it("refuses to send a rejection without a reason", async () => {
    resetCalls();
    stub({});
    mount(<ApprovalsPage approvals={[approvalRow()] as never} loading={false} />);

    const table = await screen.findByRole("table");
    await userEvent.click(within(table).getByRole("button", { name: /拒\s*绝/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "确认拒绝" }));

    expect(await screen.findByText("拒绝必须给出理由")).toBeInTheDocument();
    expect(calls("post")).toHaveLength(0);
  });

  it("does not offer a decision on a plan that already has one", async () => {
    stub({});
    mount(
      <ApprovalsPage approvals={[approvalRow({
        plan_id: "plan-done", approval_state: "EXECUTED", execution_state: "SUCCEEDED",
        approver: "administrator", decision: "APPROVED",
      })] as never} loading={false} />,
    );

    const table = await screen.findByRole("table");
    expect(within(table).getByRole("button", { name: /批\s*准/ })).toBeDisabled();
    expect(within(table).getByRole("button", { name: /拒\s*绝/ })).toBeDisabled();
  });
});

describe("Plugin inventory", () => {
  it("surfaces the enablement and sandbox fields the platform returns", async () => {
    stub({});
    mount(
      <PluginsPage
        loading={false}
        plugins={[
          { id: "p1", domain: "recon", name: "whois", version: "1.2.0", enabled: true, health_status: "HEALTHY", capabilities: ["recon.whois"], certified: true, sandbox_compatible: true },
          { id: "p2", domain: "web", name: "browser", version: "0.9.0", enabled: false, health_status: "DEGRADED", capabilities: [], certified: false, sandbox_compatible: false },
        ]}
      />,
    );

    // `enabled` decides whether the scheduler may route work to a plugin, so a
    // row that cannot show it is hiding a operational fact from the operator.
    const table = await screen.findByRole("table");
    expect(within(table).getByText("已启用")).toBeInTheDocument();
    expect(within(table).getByText("已停用")).toBeInTheDocument();
    expect(within(table).getByText("支持")).toBeInTheDocument();
    expect(within(table).getByText("不支持")).toBeInTheDocument();
    expect(within(table).getByText("已认证")).toBeInTheDocument();
    expect(within(table).getByText("未认证")).toBeInTheDocument();
    expect(within(table).getByText("DEGRADED")).toBeInTheDocument();
  });
});

describe("Incident assignment", () => {
  const incident = {
    id: "inc-9", title: "Kerberoasting burst from workstation", severity: "HIGH",
    status: "TRIAGED", description: "", priority: "P2", confidence: "MEDIUM",
    source: "DETECTION", owner: null, assignee: null, queue: null,
    classification: null, risk: null, attributes: {}, finding_ids: [], event_ids: [],
    created_at: "2026-09-18T09:00:00Z", updated_at: "2026-09-18T09:00:00Z",
  };

  const listOnly = {
    "/incidents": { data: { items: [incident], page: 1, page_size: 20, total: 1 } },
  };

  const openDialog = async () => {
    await userEvent.click(await screen.findByRole("button", { name: /指\s*派/ }));
    const dialog = await screen.findByRole("dialog");
    return within(dialog);
  };

  it("sends nothing when the dialog would change no field", async () => {
    resetCalls();
    stub(listOnly);
    mount(<IncidentsPage />);

    const dialog = await openDialog();
    // 操作人 is pre-filled, so pressing OK here used to POST a partial
    // assignment that changed nothing and still reported "已更新指派".
    await userEvent.click(dialog.getByRole("button", { name: /OK|确\s*定/ }));

    expect(calls("post")).toHaveLength(0);
    expect(await screen.findByText("请至少修改 Owner、处理人或优先级中的一项")).toBeInTheDocument();
    // The dialog stays open so the operator can correct it rather than lose input.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("posts the operator together with the field that changed", async () => {
    resetCalls();
    stub({
      ...listOnly,
      "POST /incidents/inc-9/assign": {
        data: { ...incident, assignee: "ana", status: "INVESTIGATING" },
      },
    });
    mount(<IncidentsPage />);

    const dialog = await openDialog();
    await userEvent.type(dialog.getByLabelText("处理人"), "ana");
    await userEvent.click(dialog.getByRole("button", { name: /OK|确\s*定/ }));

    await waitFor(() => expect(calls("post")).toHaveLength(1));
    const [post] = calls("post");
    expect(post.url).toBe("/incidents/inc-9/assign");
    // Undefined optional fields are dropped by serialisation: the platform
    // receives only what the operator actually set.
    expect(JSON.parse(String(post.config.data))).toEqual({
      actor: "console-operator", assignee: "ana",
    });
    expect(await screen.findByText("已更新指派")).toBeInTheDocument();
  });
});
