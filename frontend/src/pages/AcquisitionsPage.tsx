import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Card, Form, Input, Popconfirm, Space, Table, Tag, Typography } from "antd";
import { App } from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  cancelAcquisition,
  createAcquisition,
  getAcquisition,
  getAcquisitionCompleteness,
  getAcquisitionEvidence,
  getAcquisitions,
  resumeAcquisition,
} from "../api/client";
import type { AcquisitionDetail, AcquisitionSummary } from "../api/client";
import { ListError } from "../components/ListError";
import { errorMessage } from "../api/http";

const { Text, Title, Paragraph } = Typography;

/** Statuses the backend refuses to resume (409 "acquisition already terminal"). */
const TERMINAL_STATUSES = new Set(["COMPLETE", "CANCELLED", "FAILED", "BLOCKED"]);

const runStatusColor = (status: string): string =>
  status === "COMPLETE" ? "green"
    : status === "BLOCKED" ? "red"
      : status === "PARTIAL" ? "orange"
        : status === "RUNNING" ? "processing"
          : "blue";

export default function AcquisitionsPage() {
  const { message } = App.useApp();
  const [acquisitions, setAcquisitions] = useState<AcquisitionSummary[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [selected, setSelected] = useState<AcquisitionDetail | null>(null);
  const [selectedEvidence, setSelectedEvidence] = useState<Record<string, unknown>[]>([]);
  const [selectedCompleteness, setSelectedCompleteness] = useState<Record<string, unknown> | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [actingId, setActingId] = useState<string | null>(null);
  // Guards the detail fan-out: a slow response for run A must not land on top of
  // a newer selection for run B.
  const selectionId = useRef(0);

  const loadAcquisitions = useCallback(async () => {
    setListLoading(true);
    try {
      const list = await getAcquisitions();
      setAcquisitions(list.items);
      setListError(null);
    } catch (requestError) {
      setListError(errorMessage(requestError, "加载采集列表失败"));
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => { void loadAcquisitions(); }, [loadAcquisitions]);

  const selectAcquisition = async (id: string) => {
    const current = ++selectionId.current;
    // Clear the dependent regions before fetching, otherwise a failed evidence
    // request leaves the previous run's evidence on screen under the new header.
    setSelectedEvidence([]);
    setSelectedCompleteness(null);
    setDetailError(null);
    setDetailLoading(true);
    try {
      const detail = await getAcquisition(id);
      if (current !== selectionId.current) return;
      setSelected(detail);
      const evidence = await getAcquisitionEvidence(id);
      if (current !== selectionId.current) return;
      setSelectedEvidence(evidence.evidence as Record<string, unknown>[]);
      const completeness = await getAcquisitionCompleteness(id);
      if (current !== selectionId.current) return;
      setSelectedCompleteness(completeness as Record<string, unknown>);
    } catch (requestError) {
      if (current !== selectionId.current) return;
      setDetailError(errorMessage(requestError, "加载采集详情失败"));
    } finally {
      if (current === selectionId.current) setDetailLoading(false);
    }
  };

  const submitCreate = async (values: { goal: string; url: string; asset: string; fields: string }) => {
    setSubmitting(true);
    try {
      await createAcquisition({
        goal: values.goal,
        url: values.url,
        target_asset: values.asset,
        expected_fields: values.fields.split(",").map((f) => f.trim()).filter(Boolean),
      });
      await loadAcquisitions();
      setSelected(null);
      setSelectedEvidence([]);
      setSelectedCompleteness(null);
      setDetailError(null);
      message.success("采集任务已入队，等待 Worker 领取");
    } catch (requestError) {
      message.error(errorMessage(requestError, "Data Acquisition 执行失败"));
    } finally {
      setSubmitting(false);
    }
  };

  const runAction = async (id: string, kind: "resume" | "cancel") => {
    setActingId(id);
    try {
      if (kind === "resume") {
        const result = await resumeAcquisition(id);
        message.success(`已重新入队（${result.status}），将从检查点继续`);
      } else {
        const result = await cancelAcquisition(id);
        message.success(result.cancelled ? "已取消" : "已请求取消，Worker 正在终止执行");
      }
      await loadAcquisitions();
      await selectAcquisition(id);
    } catch (requestError) {
      message.error(errorMessage(requestError, kind === "resume" ? "恢复失败" : "取消失败"));
    } finally {
      setActingId(null);
    }
  };

  const columns: ColumnsType<AcquisitionSummary> = [
    { title: "Goal", dataIndex: "goal", ellipsis: true },
    { title: "Status", dataIndex: "status", width: 110, render: (v: string) => <Tag color={runStatusColor(v)}>{v}</Tag> },
    { title: "Source", dataIndex: "source_type", width: 120 },
    { title: "Strategy", dataIndex: "strategy", ellipsis: true },
    { title: "Replans", dataIndex: "replans", width: 90 },
    { title: "Bytes", dataIndex: "total_bytes", width: 100 },
    { title: "Requests", dataIndex: "total_requests", width: 90 },
    { title: "Duration(s)", dataIndex: "duration_seconds", width: 110 },
    { title: "Blocked", dataIndex: "blocked_reason", width: 130, render: (v: string) => (v !== "NONE" ? <Tag color="volcano">{v}</Tag> : "—") },
    {
      title: "操作",
      key: "actions",
      width: 150,
      fixed: "right",
      render: (_, row) => (
        <Space size={4} onClick={(event) => event.stopPropagation()}>
          <Popconfirm
            title="从检查点继续该采集？"
            description="将同一运行重新入队，从已保存的分页游标继续。"
            okText="继续"
            cancelText="取消"
            disabled={TERMINAL_STATUSES.has(row.status)}
            onConfirm={() => void runAction(row.id, "resume")}
          >
            <Button size="small" type="link" disabled={TERMINAL_STATUSES.has(row.status)} loading={actingId === row.id}>
              继续
            </Button>
          </Popconfirm>
          <Popconfirm
            title="请求取消该采集？"
            description="运行将标记为 CANCEL_REQUESTED，由 Worker 终止并释放租约。"
            okText="请求取消"
            cancelText="返回"
            disabled={TERMINAL_STATUSES.has(row.status)}
            onConfirm={() => void runAction(row.id, "cancel")}
          >
            <Button size="small" type="link" danger disabled={TERMINAL_STATUSES.has(row.status)}>
              取消
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={20} style={{ width: "100%" }}>
      <div>
        <Text className="eyebrow">CAP · DATA ACQUISITION</Text>
        <Title level={2}>Data Acquisition</Title>
        <Paragraph type="secondary">
          仅公开数据采集：SSRF 防护、robots.txt 合规、401/403/验证码/付费墙一律 STOP（不绕过）。
          证据 Lineage：Source → Raw Artifact → Evidence → ExtractedDocument → FactCandidate。
        </Paragraph>
      </div>
      <Card title="发起采集">
        <Form
          layout="inline"
          onFinish={(values: { goal: string; url: string; asset: string; fields: string }) =>
            void submitCreate(values)
          }
        >
          <Form.Item name="goal" rules={[{ required: true, message: "目标必填" }]}>
            <Input placeholder="采集目标（如：获取公开安全公告 CVE）" style={{ width: 280 }} />
          </Form.Item>
          <Form.Item
            name="url"
            rules={[
              { required: true, message: "URL 必填" },
              {
                // Fail fast in the form rather than burning a run that the
                // server-side SSRF guard is going to BLOCK anyway.
                validator: (_, value: string) => {
                  if (!value) return Promise.resolve();
                  let parsed: URL;
                  try {
                    parsed = new URL(value);
                  } catch {
                    return Promise.reject(new Error("请输入合法的绝对 URL"));
                  }
                  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
                    return Promise.reject(new Error("仅支持 http/https 协议"));
                  }
                  return Promise.resolve();
                },
              },
            ]}
          >
            <Input placeholder="https://public.example/page" style={{ width: 280 }} />
          </Form.Item>
          <Form.Item name="asset">
            <Input placeholder="关联资产（可选）" style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="fields">
            <Input placeholder="期望字段,逗号分隔（可选）" style={{ width: 200 }} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" loading={submitting}>开始采集</Button>
              <Button onClick={() => void loadAcquisitions()}>刷新</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
      <Card title="采集记录">
        <ListError error={listError} onRetry={() => void loadAcquisitions()} description="采集列表" />
        <Table<AcquisitionSummary>
          rowKey="id"
          size="small"
          loading={listLoading}
          dataSource={acquisitions}
          pagination={false}
          scroll={{ x: "max-content" }}
          locale={{ emptyText: listError ? "加载失败" : "暂无采集记录" }}
          onRow={(record) => ({
            tabIndex: 0,
            "aria-label": `查看采集运行详情：${record.goal}`,
            style: { cursor: "pointer" },
            onClick: () => void selectAcquisition(record.id),
            onKeyDown: (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                void selectAcquisition(record.id);
              }
            },
          })}
          columns={columns}
        />
      </Card>
      {selected && (
        <Card
          title={`运行详情 · ${selected.goal}`}
          loading={detailLoading}
          extra={
            !TERMINAL_STATUSES.has(selected.status) && (
              <Space>
                <Button size="small" loading={actingId === selected.id} onClick={() => void runAction(selected.id, "resume")}>
                  从检查点继续
                </Button>
                <Button size="small" danger onClick={() => void runAction(selected.id, "cancel")}>
                  请求取消
                </Button>
              </Space>
            )
          }
        >
          <ListError error={detailError} onRetry={() => void selectAcquisition(selected.id)} description="运行详情与证据" />
          <Paragraph>
            <Tag color={runStatusColor(selected.status)}>{selected.status}</Tag>{" "}
            <Text strong>{selected.source_type}</Text> / {selected.strategy}
          </Paragraph>
          {selected.blocked_reason !== "NONE" && (
            <Paragraph type="danger">BLOCKED: {selected.blocked_reason} — {selected.blocked_detail}</Paragraph>
          )}
          <Paragraph type="secondary">
            策略历史（为什么切换/停止）: {selected.strategy_history.join(" → ") || "—"}
          </Paragraph>
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
            loading={detailLoading}
            dataSource={selectedEvidence}
            locale={{ emptyText: detailError ? "证据加载失败" : "暂无证据产物" }}
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
