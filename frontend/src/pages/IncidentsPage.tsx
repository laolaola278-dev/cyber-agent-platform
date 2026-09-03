import { useState } from "react";
import {
  Button, Card, Descriptions, Drawer, Form, Input, Modal, Select, Space, Table, Tag, Timeline, Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { App } from "antd";
import { usePageList } from "../hooks/usePageList";
import { assignIncident, createIncident, getIncident, transitionIncident } from "../api/client";
import type { Incident } from "../types";
import { INCIDENT_STATUSES, PRIORITIES, SEVERITIES, formatTime, severityTag, statusTag } from "../api/constants";
import { errorMessage } from "../api/http";

const { Text } = Typography;

export default function IncidentsPage() {
  const { message } = App.useApp();
  const [filters, setFilters] = useState<{ severity?: string; status?: string; priority?: string }>({});
  const { rows, loading, pagination, refresh } = usePageList<Incident>("/incidents", filters);
  const [detail, setDetail] = useState<Incident | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [transitionTarget, setTransitionTarget] = useState<Incident | null>(null);
  const [assignTarget, setAssignTarget] = useState<Incident | null>(null);
  const [acting, setActing] = useState(false);
  const [createForm] = Form.useForm();
  const [transitionForm] = Form.useForm();
  const [assignForm] = Form.useForm();

  const openDetail = async (id: string) => {
    setDetailOpen(true);
    setDetailLoading(true);
    try { setDetail(await getIncident(id)); }
    catch (error) { message.error(errorMessage(error, "加载事件详情失败")); }
    finally { setDetailLoading(false); }
  };

  const submitCreate = async (values: Record<string, string>) => {
    setActing(true);
    try {
      await createIncident({
        title: values.title,
        description: values.description ?? "",
        severity: values.severity,
        priority: values.priority,
        source: "CONSOLE",
      });
      message.success("事件已创建");
      setCreateOpen(false);
      createForm.resetFields();
      refresh();
    } catch (error) { message.error(errorMessage(error, "创建失败")); }
    finally { setActing(false); }
  };

  const submitTransition = async (values: { status: string; actor: string; reason?: string }) => {
    if (!transitionTarget) return;
    setActing(true);
    try {
      const updated = await transitionIncident(transitionTarget.id, values);
      message.success(`已流转到 ${values.status}`);
      setTransitionTarget(null);
      transitionForm.resetFields();
      if (detailOpen && detail?.id === updated.id) setDetail(updated);
      refresh();
    } catch (error) { message.error(errorMessage(error, "流转失败")); }
    finally { setActing(false); }
  };

  const submitAssign = async (values: { actor: string; owner?: string; assignee?: string; priority?: string }) => {
    if (!assignTarget) return;
    setActing(true);
    try {
      const updated = await assignIncident(assignTarget.id, values);
      message.success("已更新指派");
      setAssignTarget(null);
      assignForm.resetFields();
      if (detailOpen && detail?.id === updated.id) setDetail(updated);
      refresh();
    } catch (error) { message.error(errorMessage(error, "指派失败")); }
    finally { setActing(false); }
  };

  const columns: ColumnsType<Incident> = [
    { title: "标题", dataIndex: "title", ellipsis: true, width: 240 },
    { title: "严重度", dataIndex: "severity", width: 100, render: severityTag },
    { title: "优先级", dataIndex: "priority", width: 80, render: (v: string) => <Tag color="purple">{v}</Tag> },
    { title: "状态", dataIndex: "status", width: 120, render: statusTag },
    { title: "负责人", dataIndex: "assignee", width: 110, render: (v: string | null) => v ?? "—" },
    { title: "来源", dataIndex: "source", width: 110 },
    { title: "创建时间", dataIndex: "created_at", width: 160, render: formatTime },
    {
      title: "操作", width: 170, render: (_, row) => (
        <Space>
          <Button size="small" onClick={() => void openDetail(row.id)}>详情</Button>
          <Button size="small" onClick={() => { setTransitionTarget(row); transitionForm.setFieldsValue({ status: undefined, actor: "console-operator" }); }}>流转</Button>
          <Button size="small" onClick={() => { setAssignTarget(row); assignForm.setFieldsValue({ actor: "console-operator" }); }}>指派</Button>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Space wrap>
          <Select
            allowClear placeholder="严重度" style={{ width: 130 }}
            options={SEVERITIES.map((s) => ({ value: s }))}
            value={filters.severity}
            onChange={(v) => setFilters((f) => ({ ...f, severity: v }))}
          />
          <Select
            allowClear placeholder="状态" style={{ width: 140 }}
            options={INCIDENT_STATUSES.map((s) => ({ value: s }))}
            value={filters.status}
            onChange={(v) => setFilters((f) => ({ ...f, status: v }))}
          />
          <Select
            allowClear placeholder="优先级" style={{ width: 100 }}
            options={PRIORITIES.map((p) => ({ value: p }))}
            value={filters.priority}
            onChange={(v) => setFilters((f) => ({ ...f, priority: v }))}
          />
          <Button onClick={refresh}>刷新</Button>
          <Button type="primary" onClick={() => setCreateOpen(true)}>新建事件</Button>
        </Space>
      </Card>
      <Card title="事件列表">
        <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination} />
      </Card>

      <Drawer
        title={detail ? `事件 · ${detail.title}` : "事件详情"}
        width={720} open={detailOpen} onClose={() => setDetailOpen(false)}
      >
        {detailLoading || !detail ? <Text type="secondary">加载中…</Text> : (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Descriptions bordered column={2} size="small">
              <Descriptions.Item label="状态">{statusTag(detail.status)}</Descriptions.Item>
              <Descriptions.Item label="严重度">{severityTag(detail.severity)}</Descriptions.Item>
              <Descriptions.Item label="优先级"><Tag color="purple">{detail.priority}</Tag></Descriptions.Item>
              <Descriptions.Item label="置信度">{detail.confidence}</Descriptions.Item>
              <Descriptions.Item label="负责人">{detail.assignee ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="Owner">{detail.owner ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="SLA 截止">{formatTime(detail.sla_due_at)}</Descriptions.Item>
              <Descriptions.Item label="创建时间">{formatTime(detail.created_at)}</Descriptions.Item>
              <Descriptions.Item label="描述" span={2}>{detail.description || "—"}</Descriptions.Item>
            </Descriptions>
            {detail.timelines.length > 0 && (
              <Card size="small" title="时间线">
                <Timeline
                  items={detail.timelines.map((entry) => ({
                    children: (
                      <div>
                        <Text strong>{entry.event_type}</Text>{" "}
                        <Text type="secondary">{formatTime(entry.created_at)} · {entry.actor}</Text>
                        <div>{entry.description}</div>
                        {entry.from_status && entry.to_status && (
                          <Text type="secondary">{entry.from_status} → {entry.to_status}</Text>
                        )}
                      </div>
                    ),
                  }))}
                />
              </Card>
            )}
            {detail.artifacts.length > 0 && (
              <Card size="small" title="Artifacts">
                {detail.artifacts.map((artifact) => (
                  <div key={artifact.id}>
                    <Tag color="cyan">{artifact.artifact_type}</Tag> {artifact.label ?? artifact.value ?? artifact.id}
                  </div>
                ))}
              </Card>
            )}
            {detail.cases.length > 0 && (
              <Card size="small" title="调查 Case">
                {detail.cases.map((c) => (
                  <div key={c.id}>{statusTag(c.status)} {c.title} · {c.assignee ?? "未指派"}</div>
                ))}
              </Card>
            )}
          </Space>
        )}
      </Drawer>

      <Modal
        title="新建事件" open={createOpen} onCancel={() => setCreateOpen(false)}
        confirmLoading={acting} onOk={() => createForm.submit()}
      >
        <Form form={createForm} layout="vertical" onFinish={submitCreate}>
          <Form.Item name="title" label="标题" rules={[{ required: true, message: "请输入标题" }]}>
            <Input placeholder="事件标题" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="事件描述（可选）" />
          </Form.Item>
          <Space>
            <Form.Item name="severity" label="严重度" rules={[{ required: true, message: "请选择严重度" }]}>
              <Select style={{ width: 140 }} options={SEVERITIES.map((s) => ({ value: s }))} />
            </Form.Item>
            <Form.Item name="priority" label="优先级">
              <Select style={{ width: 120 }} allowClear options={PRIORITIES.map((p) => ({ value: p }))} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      <Modal
        title={transitionTarget ? `流转 · ${transitionTarget.title}` : ""}
        open={transitionTarget !== null} onCancel={() => setTransitionTarget(null)}
        confirmLoading={acting} onOk={() => transitionForm.submit()}
      >
        <Form form={transitionForm} layout="vertical" onFinish={submitTransition}>
          <Form.Item name="status" label="目标状态" rules={[{ required: true, message: "请选择目标状态" }]}>
            <Select options={INCIDENT_STATUSES.map((s) => ({ value: s }))} />
          </Form.Item>
          <Form.Item name="actor" label="操作人" rules={[{ required: true, message: "请输入操作人" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="reason" label="原因">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={assignTarget ? `指派 · ${assignTarget.title}` : ""}
        open={assignTarget !== null} onCancel={() => setAssignTarget(null)}
        confirmLoading={acting} onOk={() => assignForm.submit()}
      >
        <Form form={assignForm} layout="vertical" onFinish={submitAssign}>
          <Form.Item name="actor" label="操作人" rules={[{ required: true, message: "请输入操作人" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="owner" label="Owner">
            <Input placeholder="责任人（可选）" />
          </Form.Item>
          <Form.Item name="assignee" label="处理人">
            <Input placeholder="当前处理人（可选）" />
          </Form.Item>
          <Form.Item name="priority" label="调整优先级">
            <Select allowClear style={{ width: 160 }} options={PRIORITIES.map((p) => ({ value: p }))} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
