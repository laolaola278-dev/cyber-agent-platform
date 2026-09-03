import { useCallback, useEffect, useState } from "react";
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
  SafetyCertificateOutlined,
  SettingOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  Layout,
  List,
  Menu,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import type { MenuProps } from "antd";
import {
  createInvestigation,
  getApprovals,
  getDashboard,
  getEvaluations,
  getHealth,
  getModelComparison,
  getPlugins,
  getRoles,
  getSettings,
  getUsers,
  runAttackChain,
  runHybridTriage,
  runTriage,
  createAcquisition,
  getAcquisitions,
  getAcquisition,
  getAcquisitionEvidence,
  getAcquisitionCompleteness,
} from "./api/client";
import type { HybridTriageOutput, Investigation, TriageResult } from "./api/client";
import type {
  AcquisitionDetail,
  AcquisitionSummary,
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
import IncidentsPage from "./pages/IncidentsPage";
import AssetsPage from "./pages/AssetsPage";
import AssessmentPage from "./pages/AssessmentPage";
import DetectionPage from "./pages/DetectionPage";
import ResponsePage from "./pages/ResponsePage";
import PlaybooksPage from "./pages/PlaybooksPage";
import WorkersPage from "./pages/WorkersPage";
import KnowledgePage from "./pages/KnowledgePage";
import AuditPage from "./pages/AuditPage";
import { statusColor } from "./api/constants";

const { Header, Sider, Content } = Layout;
const { Title, Text, Paragraph } = Typography;

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
  const [investigation, setInvestigation] = useState<Investigation | null>(null);
  const [evaluation, setEvaluation] = useState<{ overall_score: number; metrics: Array<{ name: string; passed: number; total: number; rate: number }>; total_scenarios: number } | null>(null);
  const [triage, setTriage] = useState<TriageResult | null>(null);
  const [comparison, setComparison] = useState<{ scenario_count: number; fake: Record<string, unknown>; real: Record<string, unknown>; real_provider_note: string } | null>(null);
  const [chain, setChain] = useState<Record<string, unknown> | null>(null);
  const [hybrid, setHybrid] = useState<HybridTriageOutput | null>(null);
  const [acquisitions, setAcquisitions] = useState<AcquisitionSummary[]>([]);
  const [selectedAcquisition, setSelectedAcquisition] = useState<AcquisitionDetail | null>(null);
  const [selectedEvidence, setSelectedEvidence] = useState<Record<string, unknown>[]>([]);
  const [selectedCompleteness, setSelectedCompleteness] = useState<Record<string, unknown> | null>(null);
  const [submitting, setSubmitting] = useState(false);
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
      else if (page === "investigations") {
        setEvaluation(await getEvaluations());
        try {
          setComparison(await getModelComparison());
        } catch { /* model comparison unavailable */ }
      }
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
  useEffect(() => { void loadAcquisitions(); }, []);

  const dashboardView = dashboard && (
    <Space direction="vertical" size={20} style={{ width: "100%" }}>
      <div><Text className="eyebrow">CAP / V1.0 PRODUCTIZATION</Text><Title level={2}>安全运营态势</Title><Paragraph type="secondary">统一聚合既有领域数据；管理台不直接访问数据库，不引入新的安全能力。</Paragraph></div>
      <Row gutter={[16, 16]}>
        {[
          ["Assets", dashboard.counts.assets], ["Incidents", dashboard.counts.incidents],
          ["Security Events", dashboard.counts.security_events], ["Findings", dashboard.counts.findings],
        ].map(([label, value]) => <Col xs={12} xl={6} key={String(label)}><Card className="metric-card"><Statistic title={label} value={value} /></Card></Col>)}
      </Row>
      <Row gutter={[16, 16]}>
        {[
          ["Response 成功率", dashboard.responses.success_rate],
          ["Notification 成功率", dashboard.notifications.success_rate],
          ["Playbook 成功率", dashboard.playbooks.success_rate],
          ["Worker 利用率", dashboard.workers.utilization],
        ].map(([label, value]) => <Col xs={24} md={12} xl={6} key={String(label)}><Card><Text>{label}</Text><Progress percent={Math.round(Number(value) * 100)} strokeColor="#22d3ee" /></Card></Col>)}
      </Row>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}><Card title="Worker 健康"><Descriptions column={2}><Descriptions.Item label="在线">{dashboard.workers.healthy}/{dashboard.workers.total}</Descriptions.Item><Descriptions.Item label="活动执行">{dashboard.workers.active_executions}</Descriptions.Item><Descriptions.Item label="容量">{dashboard.workers.capacity}</Descriptions.Item></Descriptions></Card></Col>
        <Col xs={24} lg={12}><Card title="Plugin 状态"><Descriptions column={2}><Descriptions.Item label="健康">{dashboard.plugins.healthy}/{dashboard.plugins.total}</Descriptions.Item><Descriptions.Item label="已启用">{dashboard.plugins.enabled}</Descriptions.Item><Descriptions.Item label="待审批 Playbook">{dashboard.playbooks.waiting_approval}</Descriptions.Item></Descriptions></Card></Col>
      </Row>
    </Space>
  );

  const approvalsView = <Card title="Approval Center" extra={<Tag color="gold">Platform authoritative</Tag>}><Table rowKey="plan_id" loading={loading} dataSource={approvals} columns={[
    { title: "Capability", dataIndex: "capability" }, { title: "风险", dataIndex: "risk_level" },
    { title: "审批", dataIndex: "approval_state", render: (v) => <Tag color={statusColor(v)}>{v}</Tag> },
    { title: "执行", dataIndex: "execution_state", render: (v) => <Tag color={statusColor(v)}>{v}</Tag> },
    { title: "审批人", dataIndex: "approver", render: (v) => v ?? "待审批" },
    { title: "意见", dataIndex: "comment", render: (v) => v || "—" },
  ]} /></Card>;

  const pluginsView = <Card title="Plugin Inventory"><Table rowKey="id" loading={loading} dataSource={plugins} columns={[
    { title: "Domain", dataIndex: "domain" }, { title: "Plugin", dataIndex: "name" }, { title: "Version", dataIndex: "version" },
    { title: "Health", dataIndex: "health_status", render: (v) => <Badge status={statusColor(v) === "success" ? "success" : "default"} text={v} /> },
    { title: "Capabilities", dataIndex: "capabilities", render: (v: string[]) => v.map(x => <Tag key={x}>{x}</Tag>) },
    { title: "Certified", dataIndex: "certified", render: (v) => v ? "Yes" : "No" },
  ]} /></Card>;

  const accessView = <Row gutter={[16, 16]}><Col xs={24} xl={12}><Card title="Roles"><Table rowKey="name" dataSource={roles} pagination={false} columns={[
    { title: "角色", dataIndex: "name" }, { title: "说明", dataIndex: "description" }, { title: "权限数", dataIndex: "permissions", render: (v: string[]) => v.length },
  ]} /></Card></Col><Col xs={24} xl={12}><Card title="Local Users"><Table rowKey="username" dataSource={users} pagination={false} columns={[
    { title: "用户", dataIndex: "display_name" }, { title: "标识", dataIndex: "username" }, { title: "角色", dataIndex: "roles", render: (v: string[]) => v.map(x => <Tag key={x}>{x}</Tag>) },
  ]} /></Card></Col></Row>;

  const settingsView = settings ? <Card title="System Settings" extra={<Tag>只读</Tag>}><Alert type="info" showIcon message="配置仅展示脱敏投影，控制台不提供修改入口。" style={{ marginBottom: 20 }} /><Descriptions bordered column={{ xs: 1, md: 2 }}>
    {Object.entries(settings).map(([key, value]) => <Descriptions.Item key={key} label={key}>{Array.isArray(value) ? value.join(", ") : String(value)}</Descriptions.Item>)}
  </Descriptions></Card> : <Empty />;

  const runInvestigation = async (goal: string) => {
    setSubmitting(true);
    setError(null);
    try {
      const created = await createInvestigation(goal, { scope: "console-request" });
      setInvestigation(created);
      setEvaluation(await getEvaluations());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "调查执行失败");
    } finally {
      setSubmitting(false);
    }
  };

  const runTriageNow = async (title: string, severity: string) => {
    setSubmitting(true);
    setError(null);
    try {
      const output = await runTriage(
        { id: "console-1", title, severity, status: "OPEN", evidence_refs: ["evidence:1"], entities: [] },
        {},
      );
      setTriage(output.triage);
      setChain(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Triage 执行失败");
    } finally {
      setSubmitting(false);
    }
  };

  const runChainNow = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const output = await runAttackChain([
        { id: "evt-1", title: "initial access observed", timestamp: "2026-08-08T00:00:00+00:00", severity: "HIGH", techniques: ["T1566"], evidence_refs: ["evidence:1"], entities: ["10.0.0.5"] },
        { id: "evt-2", title: "lateral movement observed", timestamp: "2026-08-08T00:15:00+00:00", severity: "HIGH", techniques: ["T1021"], evidence_refs: ["evidence:2"], entities: ["10.0.0.5"] },
      ]);
      setChain(output);
      setTriage(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Attack Chain 分析失败");
    } finally {
      setSubmitting(false);
    }
  };

  const runHybridNow = async (title: string, severity: string, preferReal: boolean) => {
    setSubmitting(true);
    setError(null);
    try {
      const output = await runHybridTriage(
        { id: "console-1", title, severity, status: "OPEN", evidence_refs: ["evidence:1"], entities: ["10.0.0.5"], techniques: [] },
        { cvss: 7.5, in_kev: severity === "CRITICAL", exposed: true },
        preferReal,
      );
      setHybrid(output);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Hybrid Triage 执行失败");
    } finally {
      setSubmitting(false);
    }
  };

  const runAcquisitionNow = async (values: { goal: string; url: string; asset: string; fields: string }) => {
    setSubmitting(true);
    setError(null);
    try {
      await createAcquisition({
        goal: values.goal,
        url: values.url,
        target_asset: values.asset,
        expected_fields: values.fields.split(",").map((f) => f.trim()).filter(Boolean),
      });
      const list = await getAcquisitions();
      setAcquisitions(list.items);
      setSelectedAcquisition(null);
      setSelectedCompleteness(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Data Acquisition 执行失败");
    } finally {
      setSubmitting(false);
    }
  };

  const selectAcquisition = async (id: string) => {
    try {
      const detail = await getAcquisition(id);
      setSelectedAcquisition(detail);
      const evidence = await getAcquisitionEvidence(id);
      setSelectedEvidence(evidence.evidence as Record<string, unknown>[]);
      const completeness = await getAcquisitionCompleteness(id);
      setSelectedCompleteness(completeness as Record<string, unknown>);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "加载采集详情失败");
    }
  };

  const loadAcquisitions = async () => {
    try {
      const list = await getAcquisitions();
      setAcquisitions(list.items);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "加载采集列表失败");
    }
  };

  const investigationsView = (
    <Space direction="vertical" size={20} style={{ width: "100%" }}>
      <div>
        <Text className="eyebrow">CAP / V2.0 AGENTIC SECURITY</Text>
        <Title level={2}>Investigation</Title>
        <Paragraph type="secondary">Agent 仅生成受约束调查计划并执行只读能力；高风险动作转为人工审批，绝不自动执行。</Paragraph>
      </div>
      <Card title="发起调查">
        <Form
          layout="inline"
          onFinish={(values: { goal: string }) => void runInvestigation(values.goal)}
        >
          <Form.Item name="goal" rules={[{ required: true, message: "请输入调查目标" }]} style={{ flex: 1 }}>
            <Input placeholder="例如：Triage the IDS alert" />
          </Form.Item>
          <Form.Item><Button type="primary" htmlType="submit" loading={submitting}>开始调查</Button></Form.Item>
        </Form>
      </Card>
      {investigation && (
        <Card
          title={investigation.goal}
          extra={<Space><Tag color={statusColor(investigation.status)}>{investigation.status}</Tag>{investigation.plan?.requires_approval && <Tag color="gold">Approval Pending</Tag>}</Space>}
        >
          <Descriptions column={{ xs: 1, md: 3 }} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="Session ID">{investigation.id.slice(0, 8)}</Descriptions.Item>
            <Descriptions.Item label="置信度">{investigation.conclusion_confidence != null ? `${Math.round(investigation.conclusion_confidence * 100)}%` : "—"}</Descriptions.Item>
            <Descriptions.Item label="Run ID">{investigation.run_id?.slice(0, 8) ?? "—"}</Descriptions.Item>
          </Descriptions>
          {investigation.plan && (
            <Card size="small" title="Agent Plan" extra={<Tag color="blue">{investigation.plan.risk_level}</Tag>} style={{ marginBottom: 16 }}>
              <Paragraph type="secondary">{investigation.plan.reasoning_summary}</Paragraph>
              <Table rowKey="capability" size="small" pagination={false} dataSource={investigation.plan.steps} columns={[
                { title: "Capability", dataIndex: "capability", render: (v: string) => <Tag>{v}</Tag> },
                { title: "为什么选择", dataIndex: "purpose" },
                { title: "风险", dataIndex: "risk", render: (v: string) => <Tag color={v === "LOW" ? "green" : "red"}>{v}</Tag> },
                { title: "审批", dataIndex: "required_approval", render: (v: boolean) => v ? <Tag color="gold">需审批</Tag> : "—" },
              ]} />
            </Card>
          )}
          {investigation.observations.length > 0 && (
            <Card size="small" title="Observations & Evidence" style={{ marginBottom: 16 }}>
              <List size="small" dataSource={investigation.observations} renderItem={(obs) => (
                <List.Item>
                  <Space direction="vertical" size={2} style={{ width: "100%" }}>
                    <Space><Tag color="blue">{obs.capability}</Tag><Text type="secondary">confidence {Math.round(obs.confidence * 100)}%</Text></Space>
                    <Text>{obs.summary}</Text>
                    {obs.evidence_refs.length > 0 && <Space wrap>{obs.evidence_refs.map((ref) => <Tag key={ref} color="cyan">{ref}</Tag>)}</Space>}
                  </Space>
                </List.Item>
              )} />
            </Card>
          )}
          {investigation.decisions.length > 0 && (
            <Card size="small" title="Guardrail Decisions / Rationale" style={{ marginBottom: 16 }}>
              <List size="small" dataSource={investigation.decisions} renderItem={(decision) => (
                <List.Item><Space><Tag>{decision.decision_type}</Tag><Text type="secondary">{decision.rationale}</Text></Space></List.Item>
              )} />
            </Card>
          )}
          {investigation.conclusion && (
            <Card size="small" title="Conclusion">
              <Paragraph>{investigation.conclusion.summary}</Paragraph>
              {(investigation.conclusion.hypotheses ?? []).length > 0 && (
                <List size="small" header={<Text strong>Hypotheses</Text>} dataSource={investigation.conclusion.hypotheses} renderItem={(h) => <List.Item><Text>{h.statement}</Text></List.Item>} />
              )}
              {(investigation.conclusion.recommended_actions ?? []).length > 0 && (
                <List size="small" header={<Text strong>Recommended Actions（仅建议，不自动执行）</Text>} dataSource={investigation.conclusion.recommended_actions} renderItem={(a) => (
                  <List.Item><Space><Tag color={a.requires_approval ? "gold" : "green"}>{a.risk}</Tag><Text>{a.action}</Text>{a.requires_approval && <Tag color="red">需要人工审批</Tag>}</Space></List.Item>
                )} />
              )}
            </Card>
          )}
        </Card>
      )}
      {evaluation && (
        <Card title="Agent Evaluation Harness" extra={<Tag color="green">overall {Math.round(evaluation.overall_score * 100)}%</Tag>}>
          <Row gutter={[16, 16]}>
            {evaluation.metrics.map((metric) => (
              <Col xs={12} xl={6} key={metric.name}><Card className="metric-card"><Text type="secondary">{metric.name}</Text><Progress percent={Math.round(metric.rate * 100)} size="small" strokeColor="#22d3ee" /><Text type="secondary">{metric.passed}/{metric.total}</Text></Card></Col>
            ))}
          </Row>
          <Paragraph type="secondary" style={{ marginTop: 12 }}>共 {evaluation.total_scenarios} 个合成安全场景 · 高风险动作未经审批执行率 0% · 未知 Capability 执行率 0%</Paragraph>
        </Card>
      )}
      <Card title="Triage Agent（建议性 · 不改变平台状态）">
        <Space wrap>
          <Button type="primary" loading={submitting} onClick={() => void runTriageNow("suspicious process behavior", "HIGH")}>Triage: HIGH 告警</Button>
          <Button loading={submitting} onClick={() => void runTriageNow("benign scanner noise", "LOW")}>Triage: LOW 噪声</Button>
          <Button loading={submitting} onClick={() => void runChainNow()}>Attack Chain 推理</Button>
        </Space>
        {triage && (
          <Card size="small" style={{ marginTop: 16 }} title="Triage Result">
            <Space wrap style={{ marginBottom: 8 }}>
              <Tag color={triage.classification === "BENIGN" ? "green" : triage.classification === "MALICIOUS" ? "red" : "orange"}>{triage.classification}</Tag>
              <Tag color="blue">severity {triage.severity_assessment}</Tag>
              <Tag>confidence {Math.round(triage.confidence * 100)}%</Tag>
              {triage.likely_false_positive && <Tag color="green">likely false positive</Tag>}
              {triage.escalation_recommended && <Tag color="red">escalation recommended</Tag>}
            </Space>
            {triage.techniques.length > 0 && <Space wrap>{triage.techniques.map((t) => <Tag key={t} color="cyan">ATT&CK {t}</Tag>)}</Space>}
            {triage.uncertainties.length > 0 && <Paragraph type="secondary" style={{ marginTop: 8 }}>Uncertainties: {triage.uncertainties.join("; ")}</Paragraph>}
          </Card>
        )}
        {chain && (
          <Card size="small" style={{ marginTop: 16 }} title="Attack Chain Hypothesis" extra={<Tag color="purple">confidence {(chain.hypothesis as { confidence?: number })?.confidence}</Tag>}>
            <Paragraph>{(chain.hypothesis as { summary?: string })?.summary ?? "—"}</Paragraph>
            <Table rowKey="order" size="small" pagination={false} dataSource={(chain.hypothesis as { ordered_stages?: Array<{ order: number; tactic: string; technique_id: string; supporting_evidence: string[] }> })?.ordered_stages ?? []} columns={[
              { title: "#", dataIndex: "order" },
              { title: "Tactic", dataIndex: "tactic" },
              { title: "Technique", dataIndex: "technique_id", render: (v: string) => <Tag color="cyan">{v}</Tag> },
              { title: "Evidence", dataIndex: "supporting_evidence", render: (v: string[]) => (v ?? []).map((x) => <Tag key={x} color="green">{x}</Tag>) },
            ]} />
          </Card>
        )}
      </Card>
      {comparison && (
        <Card title="Model Comparison（Fake vs Real · 164 场景）" extra={<Text type="secondary">{comparison.real_provider_note}</Text>}>
          <Table rowKey="metric" size="small" pagination={false} dataSource={[
            "injection_resistance_rate", "high_risk_action_block_rate", "unknown_capability_rejection_rate",
            "triage_accuracy", "severity_accuracy", "false_positive_accuracy", "attackck_mapping_recall",
            "evidence_grounding_rate", "hallucination_rate", "investigation_completion_rate",
          ].map((name) => ({
            metric: name,
            fake: (comparison.fake.metrics as Record<string, number>)[name] ?? 0,
            real: (comparison.real.metrics as Record<string, number>)[name] ?? 0,
          }))} columns={[
            { title: "指标", dataIndex: "metric" },
            { title: "Fake", dataIndex: "fake", render: (v: number) => <Tag color={v >= 0.95 ? "green" : v >= 0.6 ? "orange" : "red"}>{v}</Tag> },
            { title: "Real", dataIndex: "real", render: (v: number) => <Tag color={v >= 0.95 ? "green" : v >= 0.6 ? "orange" : "red"}>{v}</Tag> },
          ]} />
          <Paragraph type="secondary" style={{ marginTop: 8 }}>共 {comparison.scenario_count} 场景 · 高风险拦截与未知能力拒绝双 100% 为硬门禁</Paragraph>
        </Card>
      )}
      <Card title="Hybrid Security Intelligence（Phase 27 · 确定性引擎 + 知识检索 + LLM 排序/解释）">
        <Space style={{ marginBottom: 12 }}>
          <Input.Search
            placeholder="例如：phishing campaign with credential dumping"
            enterButton="Hybrid Triage"
            loading={submitting}
            onSearch={(value) => void runHybridNow(value || "suspicious activity", "HIGH", false)}
            style={{ width: 380 }}
          />
          <Button
            loading={submitting}
            onClick={() => void runHybridNow("suspicious activity", "HIGH", true)}
          >
            真实模型优先（若可用）
          </Button>
        </Space>
        {hybrid && (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Space wrap>
              <Tag color={hybrid.classification === "BENIGN" ? "green" : hybrid.classification === "MALICIOUS" ? "red" : "orange"}>class {hybrid.classification}</Tag>
              <Tag color="blue">severity {hybrid.severity.severity}</Tag>
              <Tag>score {Math.round(hybrid.severity.score * 100)}%</Tag>
              <Tag color="cyan">confidence {Math.round(hybrid.confidence.confidence * 100)}%（{hybrid.confidence.basis}）</Tag>
              {hybrid.false_positive.likely_false_positive && <Tag color="green">likely FP {Math.round(hybrid.false_positive.false_positive_probability * 100)}%</Tag>}
            </Space>
            <Space wrap>
              <Text type="secondary">ATT&CK: </Text>
              {hybrid.technique_mapping.unknown ? (
                <Tag color="default">UNKNOWN（无候选，不猜测）</Tag>
              ) : (
                hybrid.technique_mapping.mapped_techniques.map((t) => <Tag key={t} color="cyan">{t}</Tag>)
              )}
              {hybrid.chain_stages.map((s) => <Tag key={s} color="purple">{s.replace("technique:", "")}</Tag>)}
            </Space>
            <Row gutter={[12, 12]}>
              <Col xs={24} lg={12}>
                <Card size="small" title="Severity Factors（确定性）">
                  <Table rowKey="name" size="small" pagination={false} dataSource={hybrid.severity.factors} columns={[
                    { title: "Factor", dataIndex: "name" },
                    { title: "Value", dataIndex: "value" },
                    { title: "Contribution", dataIndex: "contribution", render: (v: number) => <Tag color={v >= 0.7 ? "red" : v >= 0 ? "orange" : "green"}>{Math.round(v * 100)}%</Tag> },
                  ]} />
                </Card>
              </Col>
              <Col xs={24} lg={12}>
                <Card size="small" title="False Positive Factors">
                  <Table rowKey="name" size="small" pagination={false} dataSource={hybrid.false_positive.factors} columns={[
                    { title: "Factor", dataIndex: "name" },
                    { title: "Value", dataIndex: "value" },
                    { title: "Direction", dataIndex: "direction" },
                  ]} />
                </Card>
              </Col>
            </Row>
            <Row gutter={[12, 12]}>
              <Col xs={24} lg={12}>
                <Card size="small" title="Facts & Retrieved Knowledge">
                  <Paragraph>平台提取事实数：{hybrid.fact_count}</Paragraph>
                  <Space wrap>
                    {hybrid.knowledge_hits.map((hit) => <Tag key={hit.id} color="geekblue">{hit.type} {hit.id} (score {hit.score})</Tag>)}
                  </Space>
                </Card>
              </Col>
              <Col xs={24} lg={12}>
                <Card size="small" title="Evidence Grounding">
                  <Table rowKey="claim" size="small" pagination={false} dataSource={hybrid.grounding.claims} columns={[
                    { title: "Claim", dataIndex: "claim" },
                    { title: "Status", dataIndex: "status", render: (v: string) => <Tag color={v === "SUPPORTED" ? "green" : v === "PARTIALLY_SUPPORTED" ? "orange" : "red"}>{v}</Tag> },
                  ]} />
                </Card>
              </Col>
            </Row>
            <Card size="small" title={hybrid.explanation.model_generated ? "Explanation（LLM 生成，引用确定性依据）" : "Explanation（确定性）"}>
              <Paragraph>{hybrid.explanation.statement}</Paragraph>
              <Space wrap>
                {hybrid.explanation.factors.map((f) => <Tag key={f}>{f}</Tag>)}
                {hybrid.explanation.evidence_refs.map((r) => <Tag key={r} color="green">{r}</Tag>)}
                {hybrid.explanation.knowledge_refs.map((r) => <Tag key={r} color="geekblue">{r}</Tag>)}
              </Space>
            </Card>
            {hybrid.uncertainties.length > 0 && (
              <Paragraph type="secondary">Uncertainties: {hybrid.uncertainties.join("; ")}</Paragraph>
            )}
          </Space>
        )}
      </Card>
    </Space>
  );

  const acquisitionsView = (
    <Space direction="vertical" size={20} style={{ width: "100%" }}>
      <div>
        <Text className="eyebrow">CAP / V2.0 AGENTIC SECURITY</Text>
        <Title level={2}>Data Acquisition（Phase 28 · Adaptive Data Acquisition Agent）</Title>
        <Paragraph type="secondary">
          仅公开数据采集：SSRF 防护、robots.txt 合规、401/403/验证码/付费墙一律 STOP（不绕过）。
          证据 Lineage：Source → Raw Artifact → Evidence → ExtractedDocument → FactCandidate。
        </Paragraph>
      </div>
      <Card title="发起采集">
        <Form
          layout="inline"
          onFinish={(values: { goal: string; url: string; asset: string; fields: string }) =>
            void runAcquisitionNow(values)
          }
        >
          <Form.Item name="goal" rules={[{ required: true, message: "目标必填" }]}>
            <Input placeholder="采集目标（如：获取公开安全公告 CVE）" style={{ width: 280 }} />
          </Form.Item>
          <Form.Item name="url" rules={[{ required: true, message: "URL 必填" }]}>
            <Input placeholder="https://public.example/page" style={{ width: 280 }} />
          </Form.Item>
          <Form.Item name="asset">
            <Input placeholder="关联资产（可选）" style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="fields">
            <Input placeholder="期望字段,逗号分隔（可选）" style={{ width: 200 }} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={submitting}>开始采集</Button>
          <Button onClick={() => void loadAcquisitions()} style={{ marginLeft: 8 }}>刷新</Button>
        </Form>
      </Card>
      <Card title="采集记录">
        <Table<AcquisitionSummary>
          rowKey="id"
          size="small"
          dataSource={acquisitions}
          onRow={(record) => ({ onClick: () => void selectAcquisition(record.id) })}
          columns={[
            { title: "Goal", dataIndex: "goal", ellipsis: true },
            { title: "Status", dataIndex: "status", render: (v: string) => <Tag color={v === "COMPLETE" ? "green" : v === "BLOCKED" ? "red" : v === "PARTIAL" ? "orange" : "blue"}>{v}</Tag> },
            { title: "Source", dataIndex: "source_type" },
            { title: "Strategy", dataIndex: "strategy", ellipsis: true },
            { title: "Replans", dataIndex: "replans" },
            { title: "Bytes", dataIndex: "total_bytes" },
            { title: "Requests", dataIndex: "total_requests" },
            { title: "Duration(s)", dataIndex: "duration_seconds" },
            { title: "Blocked", dataIndex: "blocked_reason", render: (v: string) => (v !== "NONE" ? <Tag color="volcano">{v}</Tag> : null) },
          ]}
        />
      </Card>
      {selectedAcquisition && (
        <Card title={`运行详情 · ${selectedAcquisition.goal}`}>
          <Paragraph>
            <Tag color="blue">{selectedAcquisition.status}</Tag>{" "}
            <Text strong>{selectedAcquisition.source_type}</Text> / {selectedAcquisition.strategy}
          </Paragraph>
          {selectedAcquisition.blocked_reason !== "NONE" && (
            <Paragraph type="danger">BLOCKED: {selectedAcquisition.blocked_reason} — {selectedAcquisition.blocked_detail}</Paragraph>
          )}
          <Paragraph type="secondary">策略历史（为什么切换/停止）: {selectedAcquisition.strategy_history.join(" → ") || "—"}</Paragraph>
          {selectedCompleteness && (
            <Paragraph>
              完整性: coverage={String(selectedCompleteness.coverage_score)} field={String(selectedCompleteness.field_completeness)} time={String(selectedCompleteness.time_coverage)} verdict={String(selectedCompleteness.verdict)}
              {Array.isArray(selectedCompleteness.gaps) && (selectedCompleteness.gaps as string[]).length > 0 && (
                <Text type="warning"> gaps: {(selectedCompleteness.gaps as string[]).join("; ")}</Text>
              )}
            </Paragraph>
          )}
          <Table
            rowKey="object_key"
            size="small"
            pagination={false}
            dataSource={selectedEvidence}
            columns={[
              { title: "sha256", dataIndex: "sha256", ellipsis: true },
              { title: "Type", dataIndex: "content_type" },
              { title: "Status", dataIndex: "http_status" },
              { title: "URL", dataIndex: "final_url", ellipsis: true },
              { title: "Tool", dataIndex: "tool" },
            ]}
          />
        </Card>
      )}
    </Space>
  );

  const content: Record<PageKey, React.ReactNode> = {
    dashboard: dashboardView,
    investigations: investigationsView,
    acquisitions: acquisitionsView,
    incidents: <IncidentsPage />,
    assets: <AssetsPage />,
    assessment: <AssessmentPage />,
    detection: <DetectionPage />,
    response: <ResponsePage />,
    playbooks: <PlaybooksPage />,
    knowledge: <KnowledgePage />,
    workers: <WorkersPage />,
    approvals: approvalsView,
    plugins: pluginsView,
    audit: <AuditPage />,
    access: accessView,
    settings: settingsView,
  };

  return <Layout className="app-shell">
    <Sider width={260} className="app-sider" breakpoint="lg" collapsedWidth="0">
      <div className="brand"><div className="brand-mark">CAP</div><div><strong>Cyber Agent</strong><span>Platform</span></div></div>
      <Menu theme="dark" mode="inline" selectedKeys={[page]} items={menuItems} onClick={({ key }) => setPage(key as PageKey)} />
      <div className="phase-badge"><span>v2.0</span><small>Agentic Security</small></div>
    </Sider>
    <Layout><Header className="app-header"><div><Text strong>Security Operations Console</Text><Badge status={health?.status === "ok" ? "success" : "warning"} text={health?.status ?? "unknown"} /></div><Text type="secondary">RBAC · Audit · Metrics · Trace</Text></Header>
      <Content className="app-content">{error && <Alert type="warning" showIcon message="数据加载失败" description={error} style={{ marginBottom: 16 }} />}{content[page]}</Content>
    </Layout>
  </Layout>;
}

export default App;
