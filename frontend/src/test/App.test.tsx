import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import App from "../App";

// ---------------------------------------------------------------------------
// MSW handlers — mock every API endpoint the App calls on startup & navigation
// ---------------------------------------------------------------------------
const handlers = [
  http.get("/api/health", () =>
    HttpResponse.json({ status: "ok", service: "Cyber Agent Platform", version: "1.0.0-rc1" }),
  ),
  http.get("/api/dashboard", () =>
    HttpResponse.json({
      counts: { assets: 42, incidents: 7, security_events: 128, findings: 15 },
      playbooks: { total: 10, succeeded: 8, failed: 1, waiting_approval: 1, success_rate: 0.8 },
      workers: {
        total: 5,
        healthy: 4,
        active_executions: 3,
        capacity: 10,
        utilization: 0.3,
      },
      plugins: { total: 6, healthy: 5, enabled: 4 },
      responses: { total: 20, succeeded: 18, failed: 1, waiting_approval: 1, success_rate: 0.9 },
      notifications: {
        total: 15,
        succeeded: 14,
        failed: 0,
        waiting_approval: 1,
        success_rate: 0.93,
      },
    }),
  ),
  http.get("/api/plugins", () =>
    HttpResponse.json([
      {
        id: "nuclei-1",
        domain: "assessment",
        name: "Nuclei",
        version: "1.0.0",
        enabled: true,
        health_status: "HEALTHY",
        capabilities: ["assessment.execute"],
        certified: true,
        sandbox_compatible: true,
      },
    ]),
  ),
  http.get("/api/approvals", () => HttpResponse.json([])),
  http.get("/api/audit", () => HttpResponse.json({ items: [], page: 1, page_size: 50, total: 0 })),
  http.get("/api/roles", () => HttpResponse.json([])),
  http.get("/api/users", () => HttpResponse.json([])),
  http.get("/api/settings", () =>
    HttpResponse.json({
      app_name: "CAP",
      app_version: "1.0.0-rc1",
      api_prefix: "/api",
      debug: false,
      log_level: "INFO",
      cors_origins: ["*"],
      database_driver: "postgresql",
      redis_configured: true,
      rbac_enabled: true,
      identity_header: "X-CAP-User",
      trusted_proxy_header: "X-CAP-Proxy-Secret",
      metrics_enabled: true,
      tracing_enabled: false,
      otel_service_name: "cap-backend",
      otel_exporter_endpoint_configured: false,
    }),
  ),
  http.get("/api/agent/evaluations", () =>
    HttpResponse.json({
      overall_score: 92,
      metrics: [
        { name: "Triage", passed: 48, total: 50, rate: 0.96 },
        { name: "Investigation", passed: 44, total: 50, rate: 0.88 },
      ],
      total_scenarios: 100,
    }),
  ),
  http.get(/\/api\/(assets|knowledge|evidence|assessment|detection|incidents|response|playbooks|workers|sandbox|investigations|acquisitions)/, () =>
    HttpResponse.json([]),
  ),
  http.get("/api/acquisitions", () => HttpResponse.json([])),
];

const server = setupServer(...handlers);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("App smoke tests", () => {
  it("renders the dashboard on load with health status", async () => {
    render(<App />);

    // Branding
    expect(screen.getByText("CAP")).toBeInTheDocument();
    expect(screen.getByText("Platform")).toBeInTheDocument();

    // Wait for health data to load
    await waitFor(() => {
      expect(screen.getByText("ok")).toBeInTheDocument();
    });

    // Dashboard title
    expect(screen.getByText("安全运营态势")).toBeInTheDocument();
  });

  it("shows dashboard metric cards after loading", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("42")).toBeInTheDocument(); // Assets count
      expect(screen.getByText("7")).toBeInTheDocument();  // Incidents count
    });
  });

  it("navigates to the Plugins page when clicking the menu item", async () => {
    const user = userEvent.setup();
    render(<App />);

    // Wait for initial load
    await waitFor(() => {
      expect(screen.getByText("安全运营态势")).toBeInTheDocument();
    });

    // Click Plugin in the sidebar
    await user.click(screen.getByText("Plugin"));

    // Plugin page heading
    await waitFor(() => {
      expect(screen.getByText("Nuclei")).toBeInTheDocument();
    });
  });

  it("shows an error banner when the API is unreachable", async () => {
    // Override health handler to return a network-level error
    server.use(
      http.get("/api/health", () => HttpResponse.error()),
    );

    render(<App />);

    await waitFor(() => {
      // The Ant Design Alert component with type="warning" is rendered
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });

  it("navigates to the Approvals page and shows empty table", async () => {
    const user = userEvent.setup();
    render(<App />);

    // Wait for initial load (Dashboard title is visible)
    await waitFor(() => {
      expect(screen.getByText("安全运营态势")).toBeInTheDocument();
    });

    // Click the approvals menu item (text-based, since Ant Design inline groups
    // may not expose all items with the menuitem role in jsdom)
    await user.click(screen.getByText("Approval Center"));

    // After navigation the approvals page renders its own table with specific
    // column headers that are only present in the approvals view.
    await waitFor(() => {
      expect(screen.getByText("Capability")).toBeInTheDocument();
    });
  });

  it("navigates to Access Control and shows Roles section", async () => {
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("安全运营态势")).toBeInTheDocument();
    });

    await user.click(screen.getByText("Access Control"));

    await waitFor(() => {
      expect(screen.getByText("Roles")).toBeInTheDocument();
      expect(screen.getByText("Local Users")).toBeInTheDocument();
    });
  });
});