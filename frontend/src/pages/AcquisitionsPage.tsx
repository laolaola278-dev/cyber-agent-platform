import { useCallback, useEffect, useState } from "react";
import { Button, Card, Form, Input, Space, Table, Tag, Typography } from "antd";
import { App } from "antd";
import {
  createAcquisition,
  getAcquisition,
  getAcquisitionCompleteness,
  getAcquisitionEvidence,
  getAcquisitions,
} from "../api/client";
import type { AcquisitionDetail, AcquisitionSummary } from "../api/client";
import { errorMessage } from "../api/http";

const { Text, Title, Paragraph } = Typography;

export default function AcquisitionsPage() {
  const { message } = App.useApp();
  const [acquisitions, setAcquisitions] = useState<AcquisitionSummary[]>([]);
  const [selectedAcquisition, setSelectedAcquisition] = useState<AcquisitionDetail | null>(null);
  const [selectedEvidence, setSelectedEvidence] = useState<Record<string, unknown>[]>([]);
  const [selectedCompleteness, setSelectedCompleteness] = useState<Record<string, unknown> | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const loadAcquisitions = useCallback(async () => {
    try {
      const list = await getAcquisitions();
      setAcquisitions(list.items);
    } catch (requestError) {
      message.error(errorMessage(requestError, "加载采集列表失败"));
    }
  }, [message]);

  useEffect(() => { void loadAcquisitions(); }, [loadAcquisitions]);

  const runAcquisitionNow = async (values: { goal: string; url: string; asset: string; fields: string }) => {
    setSubmitting(true);
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
      message.error(errorMessage(requestError, "Data Acquisition 执行失败"));
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
      message.error(errorMessage(requestError, "加载采集详情失败"));
    }
  };

  return (
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
}
