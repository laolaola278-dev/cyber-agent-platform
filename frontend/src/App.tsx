import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import {
  AlertOutlined,
  ApiOutlined,
  AuditOutlined,
  BlockOutlined,
  BugOutlined,
  CloudDownloadOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  LockOutlined,
  NotificationOutlined,
  PlayCircleOutlined,
  SettingOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { Alert, Badge, Layout, Menu, Spin, Typography } from "antd";
import type { MenuProps } from "antd";
import {
  getApprovals,
  getDashboard,
  getHealth,
  getPlugins,
  getRoles,
  getSettings,
  getUsers,
} from "./api/client";
import type {
  ApprovalItem,
  Dashboard,
  Health,
  PlatformUser,
  PluginItem,
  Role,
  SettingsView,
} from "./types";

// Route-level code splitting (roadmap track 4): each of the 16 views
// loads on first navigation, so the initial bundle carries only the
// shell + antd vendor chunk. Home (dashboard) keeps prefetch priority
// via eager import below.
import DashboardPage from "./pages/DashboardPage";

const InvestigationsPage = lazy(() => import("./pages/InvestigationsPage"));
const AcquisitionsPage = lazy(() => import("./pages/AcquisitionsPage"));
const IncidentsPage = lazy(() => import("./pages/IncidentsPage"));
const AssetsPage = lazy(() => import("./pages/AssetsPage"));
const AssessmentPage = lazy(() => import("./pages/AssessmentPage"));
const DetectionPage = lazy(() => import("./pages/DetectionPage"));
const ResponsePage = lazy(() => import("./pages/ResponsePage"));
const PlaybooksPage = lazy(() => import("./pages/PlaybooksPage"));
const KnowledgePage = lazy(() => import("./pages/KnowledgePage"));
const WorkersPage = lazy(() => import("./pages/WorkersPage"));
const AuditPage = lazy(() => import("./pages/AuditPage"));
const ApprovalsPage = lazy(() => import("./pages/ApprovalsPage"));
const PluginsPage = lazy(() => import("./pages/PluginsPage"));
const AccessPage = lazy(() => import("./pages/AccessPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

type PageKey =
  | "dashboard" | "investigations" | "acquisitions" | "assets" | "knowledge" | "assessment" | "detection"
  | "incidents" | "response" | "playbooks" | "approvals" | "workers"
  | "plugins" | "audit" | "access" | "settings";

const menuItems: MenuProps["items"] = [
  { key: "dashboard", icon: <DashboardOutlined />, label: "Dashboard" },
  { type: "group", label: "AGENTIC SECURITY", children: [
    { key: "investigations", icon: <ExperimentOutlined />, label: "Investigation" },
    { key: "acquisitions", icon: <CloudDownloadOutlined />, label: "Data Acquisition" },
  ] },
  { type: "group", label: "SECURITY OPERATIONS", children: [
    { key: "incidents", icon: <NotificationOutlined />, label: "Incident" },
    { key: "assets", icon: <DatabaseOutlined />, label: "Assets" },
    { key: "knowledge", icon: <FileSearchOutlined />, label: "Knowledge" },
    { key: "assessment", icon: <BugOutlined />, label: "Assessment" },
    { key: "detection", icon: <AlertOutlined />, label: "Detection" },
    { key: "response", icon: <BlockOutlined />, label: "Response" },
    { key: "playbooks", icon: <PlayCircleOutlined />, label: "Playbook" },
  ] },
  { type: "group", label: "GOVERNANCE", children: [
    { key: "approvals", icon: <LockOutlined />, label: "Approval Center" },
    { key: "audit", icon: <AuditOutlined />, label: "Audit Center" },
    { key: "access", icon: <TeamOutlined />, label: "Access Control" },
  ] },
  { type: "group", label: "PLATFORM", children: [
    { key: "workers", icon: <CloudServerOutlined />, label: "Workers & Sandbox" },
    { key: "plugins", icon: <ApiOutlined />, label: "Plugin" },
    { key: "settings", icon: <SettingOutlined />, label: "Settings" },
  ] },
];

function App() {
  const [page, setPage] = useState<PageKey>("dashboard");
  const [health, setHealth] = useState<Health | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const healthData = await getHealth();
      setHealth(healthData);
      if (page === "dashboard") setDashboard(await getDashboard());
      else if (page === "plugins") setPlugins(await getPlugins());
      else if (page === "approvals") setApprovals(await getApprovals());
      else if (page === "access") {
        const [roleData, userData] = await Promise.all([getRoles(), getUsers()]);
        setRoles(roleData); setUsers(userData);
      } else if (page === "settings") setSettings(await getSettings());
      setError(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "无法连接平台 API");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { void refresh(); }, [refresh]);

  const content: Record<PageKey, React.ReactNode> = {
    dashboard: <DashboardPage dashboard={dashboard} />,
    investigations: <InvestigationsPage />,
    acquisitions: <AcquisitionsPage />,
    incidents: <IncidentsPage />,
    assets: <AssetsPage />,
    assessment: <AssessmentPage />,
    detection: <DetectionPage />,
    response: <ResponsePage />,
    playbooks: <PlaybooksPage />,
    knowledge: <KnowledgePage />,
    workers: <WorkersPage />,
    approvals: <ApprovalsPage approvals={approvals} loading={loading} />,
    plugins: <PluginsPage plugins={plugins} loading={loading} />,
    audit: <AuditPage />,
    access: <AccessPage roles={roles} users={users} />,
    settings: <SettingsPage settings={settings} />,
  };

  return <Layout className="app-shell">
    <Sider width={260} className="app-sider" breakpoint="lg" collapsedWidth="0">
      <div className="brand"><div className="brand-mark">CAP</div><div><strong>Cyber Agent</strong><span>Platform</span></div></div>
      <Menu theme="dark" mode="inline" selectedKeys={[page]} items={menuItems} onClick={({ key }) => setPage(key as PageKey)} />
      <div className="phase-badge"><span>v2.0</span><small>Agentic Security</small></div>
    </Sider>
    <Layout><Header className="app-header"><div><Text strong>Security Operations Console</Text><Badge status={health?.status === "ok" ? "success" : "warning"} text={health?.status ?? "unknown"} /></div><Text type="secondary">RBAC · Audit · Metrics · Trace</Text></Header>
      <Content className="app-content">
        {error && <Alert type="warning" showIcon message="数据加载失败" description={error} style={{ marginBottom: 16 }} />}
        <Suspense fallback={<div className="page-loading"><Spin size="large" /></div>}>{content[page]}</Suspense>
      </Content>
    </Layout>
  </Layout>;
}

export default App;
