import { useState } from "react";
import {
  Button, Card, Col, Descriptions, Form, Input, List, Progress, Row, Space, Table, Tag, Typography,
} from "antd";
import { App } from "antd";
import {
  createInvestigation,
  getEvaluations,
  getModelComparison,
  runAttackChain,
  runHybridTriage,
  runTriage,
} from "../api/client";
import type { HybridTriageOutput, Investigation, TriageResult } from "../api/client";
import { statusColor } from "../api/constants";
import { errorMessage } from "../api/http";

const { Text, Title, Paragraph } = Typography;

interface EvaluationShape {
  overall_score: number;
  metrics: Array<{ name: string; passed: number; total: number; rate: number }>;
  total_scenarios: number;
}

interface ComparisonShape {
  scenario_count: number;
  fake: Record<string, unknown>;
  real: Record<string, unknown>;
  real_provider_note: string;
}

export default function InvestigationsPage() {
  const { message } = App.useApp();
  const [investigation, setInvestigation] = useState<Investigation | null>(null);
  const [evaluation, setEvaluation] = useState<EvaluationShape | null>(null);
  const [triage, setTriage] = useState<TriageResult | null>(null);
  const [comparison, setComparison] = useState<ComparisonShape | null>(null);
  const [chain, setChain] = useState<Record<string, unknown> | null>(null);
  const [hybrid, setHybrid] = useState<HybridTriageOutput | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const runInvestigation = async (goal: string) => {
    setSubmitting(true);
    try {
      const created = await createInvestigation(goal, { scope: "console-request" });
      setInvestigation(created);
      setEvaluation(await getEvaluations());
      try {
        setComparison(await getModelComparison());
      } catch { /* model comparison unavailable */ }
    } catch (requestError) {
      message.error(errorMessage(requestError, "调查执行失败"));
    } finally {
      setSubmitting(false);
    }
  };

  const runTriageNow = async (title: string, severity: string) => {
    setSubmitting(true);
    try {
      const output = await runTriage(
        { id: "console-1", title, severity, status: "OPEN", evidence_refs: ["evidence:1"], entities: [] },
        {},
      );
      setTriage(output.triage);
      setChain(null);
    } catch (requestError) {
      message.error(errorMessage(requestError, "Triage 执行失败"));
    } finally {
      setSubmitting(false);
    }
  };

  const runChainNow = async () => {
    setSubmitting(true);
    try {
      const output = await runAttackChain([
        { id: "evt-1", title: "initial access observed", timestamp: "2026-08-08T00:00:00+00:00", severity: "HIGH", techniques: ["T1566"], evidence_refs: ["evidence:1"], entities: ["10.0.0.5"] },
        { id: "evt-2", title: "lateral movement observed", timestamp: "2026-08-08T00:15:00+00:00", severity: "HIGH", techniques: ["T1021"], evidence_refs: ["evidence:2"], entities: ["10.0.0.5"] },
      ]);
      setChain(output);
      setTriage(null);
    } catch (requestError) {
      message.error(errorMessage(requestError, "Attack Chain 分析失败"));
    } finally {
      setSubmitting(false);
    }
  };

  const runHybridNow = async (title: string, severity: string, preferReal: boolean) => {
    setSubmitting(true);
    try {
      const output = await runHybridTriage(
        { id: "console-1", title, severity, status: "OPEN", evidence_refs: ["evidence:1"], entities: ["10.0.0.5"], techniques: [] },
        { cvss: 7.5, in_kev: severity === "CRITICAL", exposed: true },
        preferReal,
      );
      setHybrid(output);
    } catch (requestError) {
      message.error(errorMessage(requestError, "Hybrid Triage 执行失败"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
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
                { title: "审批", dataIndex: "required_approval", render: (v: boolean) => (v ? <Tag color="gold">需审批</Tag> : "—") },
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
}
