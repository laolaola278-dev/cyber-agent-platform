import { useState } from "react";
import { Button, Card, Descriptions, Drawer, Select, Space, Table, Tabs, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { usePageList } from "../hooks/usePageList";
import { getSecurityEvent } from "../api/client";
import type { DetectionTask, SecurityEvent } from "../types";
import { EVENT_STATUSES, SEVERITIES, formatTime, severityTag, statusTag } from "../api/constants";
import { errorMessage } from "../api/http";
import { App } from "antd";

function EventsTab({ message }: { message: { error: (m: string) => void } }) {
  const [filters, setFilters] = useState<{ severity?: string; status?: string }>({});
  const { rows, loading, pagination, refresh } = usePageList<SecurityEvent>("/detection/events", filters);
  const [detail, setDetail] = useState<SecurityEvent | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const openDetail = async (id: string) => {
    setDetailOpen(true);
    try { setDetail(await getSecurityEvent(id)); }
    catch (error) { message.error(errorMessage(error, "加载事件失败")); }
  };

  const columns: ColumnsType<SecurityEvent> = [
    { title: "类型", dataIndex: "event_type", width: 150, render: (v: string) => <Tag color="geekblue">{v}</Tag> },
    { title: "严重度", dataIndex: "severity", width: 100, render: severityTag },
    { title: "状态", dataIndex: "status", width: 120, render: statusTag },
    { title: "来源", dataIndex: "source", ellipsis: true, width: 160 },
    { title: "插件", dataIndex: "plugin", width: 150, ellipsis: true },
    { title: "时间", dataIndex: "timestamp", width: 160, render: formatTime },
    { title: "", width: 90, render: (_, row) => <Button size="small" onClick={() => void openDetail(row.id)}>详情</Button> },
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
          allowClear placeholder="状态" style={{ width: 140 }}
          options={EVENT_STATUSES.map((s) => ({ value: s }))}
          value={filters.status}
          onChange={(v) => setFilters((f) => ({ ...f, status: v }))}
        />
        <Button onClick={refresh}>刷新</Button>
      </Space>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination} />
      <Drawer title={detail ? `安全事件 · ${detail.event_type}` : "事件详情"} width={640} open={detailOpen} onClose={() => setDetailOpen(false)}>
        {detail ? (
          <Descriptions bordered column={1} size="small">
            <Descriptions.Item label="状态">{statusTag(detail.status)}</Descriptions.Item>
            <Descriptions.Item label="严重度 / 置信度">{severityTag(detail.severity)} {detail.confidence}</Descriptions.Item>
            <Descriptions.Item label="指纹">{detail.fingerprint}</Descriptions.Item>
            <Descriptions.Item label="来源">{detail.source}</Descriptions.Item>
            <Descriptions.Item label="插件">{detail.plugin}{detail.tool ? ` · ${detail.tool}` : ""}{detail.rule ? ` · ${detail.rule}` : ""}</Descriptions.Item>
            <Descriptions.Item label="时间">{formatTime(detail.timestamp)}</Descriptions.Item>
            <Descriptions.Item label="属性">{JSON.stringify(detail.attributes, null, 2)}</Descriptions.Item>
            {detail.references.length > 0 && (
              <Descriptions.Item label="引用">{detail.references.join(", ")}</Descriptions.Item>
            )}
          </Descriptions>
        ) : null}
      </Drawer>
    </Space>
  );
}

function TasksTab() {
  const { rows, loading, pagination, refresh } = usePageList<DetectionTask>("/detection/tasks");
  const columns: ColumnsType<DetectionTask> = [
    { title: "状态", dataIndex: "status", width: 110, render: statusTag },
    { title: "能力", dataIndex: "requested_capabilities", width: 280, render: (caps: string[]) => caps.map((c) => <Tag key={c} color="cyan">{c}</Tag>) },
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

export default function DetectionPage() {
  const { message } = App.useApp();
  return (
    <Card>
      <Tabs
        items={[
          { key: "events", label: "安全事件", children: <EventsTab message={message} /> },
          { key: "tasks", label: "检测任务", children: <TasksTab /> },
        ]}
      />
    </Card>
  );
}
