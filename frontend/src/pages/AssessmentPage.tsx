import { useState } from "react";
import { Button, Card, Descriptions, Drawer, Form, Input, Modal, Select, Space, Table, Tabs, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { App } from "antd";
import { usePageList } from "../hooks/usePageList";
import { getFinding, transitionFinding } from "../api/client";
import type { AssessmentTask, Finding } from "../types";
import { FINDING_STATUSES, SEVERITIES, formatTime, severityTag, statusTag } from "../api/constants";
import { errorMessage } from "../api/http";

function FindingsTab({ message }: { message: { success: (m: string) => void; error: (m: string) => void } }) {
  const [filters, setFilters] = useState<{ severity?: string; status?: string }>({});
  const { rows, loading, pagination, refresh } = usePageList<Finding>("/assessment/findings", filters);
  const [detail, setDetail] = useState<Finding | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [transitionTarget, setTransitionTarget] = useState<Finding | null>(null);
  const [form] = Form.useForm();
  const [acting, setActing] = useState(false);

  const openDetail = async (id: string) => {
    setDetailOpen(true);
    try { setDetail(await getFinding(id)); }
    catch (error) { message.error(errorMessage(error, "加载 Finding 失败")); }
  };

  const submitTransition = async (values: { status: string; actor: string; reason?: string }) => {
    if (!transitionTarget) return;
    setActing(true);
    try {
      await transitionFinding(transitionTarget.id, values);
      message.success(`Finding 已流转到 ${values.status}`);
      setTransitionTarget(null);
      form.resetFields();
      refresh();
    } catch (error) { message.error(errorMessage(error, "处置失败")); }
    finally { setActing(false); }
  };

  const columns: ColumnsType<Finding> = [
    { title: "标题", dataIndex: "title", ellipsis: true, width: 260 },
    { title: "严重度", dataIndex: "severity", width: 100, render: severityTag },
    { title: "状态", dataIndex: "status", width: 130, render: statusTag },
    { title: "影响资产", dataIndex: "affected_asset", ellipsis: true, width: 180 },
    { title: "风险分", dataIndex: "risk_score", width: 90, render: (v: number) => v.toFixed(2) },
    { title: "插件", dataIndex: "plugin", width: 140, ellipsis: true },
    { title: "发现时间", dataIndex: "created_at", width: 160, render: formatTime },
    {
      title: "", width: 150, render: (_, row) => (
        <Space>
          <Button size="small" onClick={() => void openDetail(row.id)}>详情</Button>
          <Button size="small" onClick={() => { setTransitionTarget(row); form.setFieldsValue({ actor: "console-operator" }); }}>处置</Button>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space wrap>
        <Select
          allowClear placeholder="严重度" style={{ width: 130 }}
          options={SEVERITIES.map((s) => ({ value: s }))}
          value={filters.severity}
          onChange={(v) => setFilters((f) => ({ ...f, severity: v }))}
        />
        <Select
          allowClear placeholder="状态" style={{ width: 150 }}
          options={FINDING_STATUSES.map((s) => ({ value: s }))}
          value={filters.status}
          onChange={(v) => setFilters((f) => ({ ...f, status: v }))}
        />
        <Button onClick={refresh}>刷新</Button>
      </Space>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination} />
      <Drawer title={detail ? `Finding · ${detail.title}` : "Finding 详情"} width={640} open={detailOpen} onClose={() => setDetailOpen(false)}>
        {detail ? (
          <Space direction="vertical" size={14} style={{ width: "100%" }}>
            <Descriptions bordered column={2} size="small">
              <Descriptions.Item label="状态">{statusTag(detail.status)}</Descriptions.Item>
              <Descriptions.Item label="严重度">{severityTag(detail.severity)}</Descriptions.Item>
              <Descriptions.Item label="置信度">{detail.confidence}</Descriptions.Item>
              <Descriptions.Item label="风险">{detail.risk_level}（{detail.risk_score.toFixed(2)}）</Descriptions.Item>
              <Descriptions.Item label="影响资产" span={2}>{detail.affected_asset}</Descriptions.Item>
              <Descriptions.Item label="描述" span={2}>{detail.description}</Descriptions.Item>
              <Descriptions.Item label="插件/工具" span={2}>{detail.plugin}{detail.tool ? ` · ${detail.tool}` : ""}{detail.rule ? ` · ${detail.rule}` : ""}</Descriptions.Item>
              <Descriptions.Item label="发现时间" span={2}>{formatTime(detail.created_at)}</Descriptions.Item>
            </Descriptions>
            {detail.references.length > 0 && (
              <div>{detail.references.map((ref) => <Tag key={ref} color="blue">{ref}</Tag>)}</div>
            )}
          </Space>
        ) : null}
      </Drawer>
      <Modal
        title={transitionTarget ? `处置 · ${transitionTarget.title}` : ""}
        open={transitionTarget !== null} onCancel={() => setTransitionTarget(null)}
        confirmLoading={acting} onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={submitTransition}>
          <Form.Item name="status" label="目标状态" rules={[{ required: true, message: "请选择目标状态" }]}>
            <Select options={FINDING_STATUSES.map((s) => ({ value: s }))} />
          </Form.Item>
          <Form.Item name="actor" label="操作人" rules={[{ required: true, message: "请输入操作人" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="reason" label="原因">
            <Input.TextArea rows={2} placeholder="如：误报确认依据 / 修复说明" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

function TasksTab() {
  const { rows, loading, pagination, refresh } = usePageList<AssessmentTask>("/assessment/tasks");
  const columns: ColumnsType<AssessmentTask> = [
    { title: "状态", dataIndex: "status", width: 110, render: statusTag },
    { title: "能力", dataIndex: "requested_capabilities", width: 260, render: (caps: string[]) => caps.map((c) => <Tag key={c} color="cyan">{c}</Tag>) },
    { title: "开始", dataIndex: "started_at", width: 160, render: formatTime },
    { title: "结束", dataIndex: "finished_at", width: 160, render: formatTime },
    { title: "错误", dataIndex: "error", ellipsis: true, render: (v: string | null) => v ?? "—" },
    { title: "创建", dataIndex: "created_at", width: 160, render: formatTime },
  ];
  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Button onClick={refresh}>刷新</Button>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination} />
    </Space>
  );
}

export default function AssessmentPage() {
  const { message } = App.useApp();
  return (
    <Card>
      <Tabs
        items={[
          { key: "findings", label: "Findings", children: <FindingsTab message={message} /> },
          { key: "tasks", label: "评估任务", children: <TasksTab /> },
        ]}
      />
    </Card>
  );
}
