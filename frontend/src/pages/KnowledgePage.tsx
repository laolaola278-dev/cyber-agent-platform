import { useState } from "react";
import { Button, Card, Input, Segmented, Space, Table, Tabs, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { usePageList } from "../hooks/usePageList";
import type { KnowledgeEntry, NotificationRecord, Ticket } from "../types";
import { formatTime, severityTag, statusTag } from "../api/constants";

function KnowledgeTab() {
  const [mode, setMode] = useState<"browse" | "search">("browse");
  const [query, setQuery] = useState<string>("");
  const [filters, setFilters] = useState<{ knowledge_type?: string }>({});
  const browse = usePageList<KnowledgeEntry>("/knowledge", mode === "browse" ? filters : {});
  const search = usePageList<KnowledgeEntry>(
    mode === "search" && query ? "/knowledge/search" : "/knowledge",
    mode === "search" && query ? { q: query } : {},
  );
  const list = mode === "search" && query ? search : browse;

  const columns: ColumnsType<KnowledgeEntry> = [
    { title: "类型", dataIndex: "knowledge_type", width: 100, render: (v: string) => <Tag color="geekblue">{v}</Tag> },
    { title: "外部 ID", dataIndex: "external_id", width: 130 },
    { title: "标题", dataIndex: "title", ellipsis: true },
    { title: "来源", dataIndex: "source", width: 130 },
    { title: "状态", dataIndex: "status", width: 110, render: statusTag },
    { title: "更新", dataIndex: "updated_at", width: 160, render: formatTime },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space wrap>
        <Segmented value={mode} onChange={(v) => setMode(v as "browse" | "search")} options={[{ label: "浏览", value: "browse" }, { label: "搜索", value: "search" }]} />
        {mode === "search" && (
          <Input.Search
            placeholder="检索关键词" style={{ width: 280 }}
            onSearch={(v) => setQuery(v)}
          />
        )}
        {mode === "browse" && (
          <Input.Search
            allowClear placeholder="按类型过滤（如 cve）" style={{ width: 200 }}
            onSearch={(v) => setFilters({ knowledge_type: v || undefined })}
          />
        )}
        <Button onClick={list.refresh}>刷新</Button>
      </Space>
      <Table rowKey="id" loading={list.loading} columns={columns} dataSource={list.rows} pagination={list.pagination} />
    </Space>
  );
}

function NotificationsTab() {
  const { rows, loading, pagination, refresh } = usePageList<NotificationRecord>("/notifications");
  const columns: ColumnsType<NotificationRecord> = [
    { title: "能力", dataIndex: "capability", width: 160, render: (v: string) => <Tag color="cyan">{v}</Tag> },
    { title: "严重度", dataIndex: "severity", width: 100, render: severityTag },
    { title: "优先级", dataIndex: "priority", width: 100, render: (v: string) => <Tag color="purple">{v}</Tag> },
    { title: "状态", dataIndex: "status", width: 110, render: statusTag },
    { title: "接收组", dataIndex: "recipient_group", width: 130 },
    { title: "接收人", dataIndex: "recipients", ellipsis: true, render: (v: string[]) => v.join(", ") },
    { title: "抑制原因", dataIndex: "suppression_reason", ellipsis: true, render: (v: string | null) => v ?? "—" },
    { title: "时间", dataIndex: "created_at", width: 160, render: formatTime },
  ];
  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Button onClick={refresh}>刷新</Button>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination} />
    </Space>
  );
}

function TicketsTab() {
  const [status, setStatus] = useState<string | undefined>(undefined);
  const { rows, loading, pagination, refresh } = usePageList<Ticket>("/tickets", status ? { status } : {});
  const columns: ColumnsType<Ticket> = [
    { title: "标题", dataIndex: "title", ellipsis: true },
    { title: "优先级", dataIndex: "priority", width: 100, render: (v: string) => <Tag color={v === "CRITICAL" ? "red" : v === "HIGH" ? "orange" : "blue"}>{v}</Tag> },
    { title: "状态", dataIndex: "status", width: 120, render: statusTag },
    { title: "外部引用", dataIndex: "external_reference", ellipsis: true, render: (v: string | null) => v ?? "—" },
    { title: "标签", dataIndex: "labels", width: 180, render: (labels: string[]) => labels.map((l) => <Tag key={l}>{l}</Tag>) },
    { title: "创建人", dataIndex: "created_by", width: 130 },
    { title: "创建时间", dataIndex: "created_at", width: 160, render: formatTime },
  ];
  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space wrap>
        {["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"].map((s) => (
          <Button key={s} type={status === s ? "primary" : "default"} size="small" onClick={() => setStatus(status === s ? undefined : s)}>{s}</Button>
        ))}
        <Button onClick={refresh}>刷新</Button>
      </Space>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination} />
    </Space>
  );
}

export default function KnowledgePage() {
  return (
    <Card>
      <Tabs
        items={[
          { key: "knowledge", label: "知识库", children: <KnowledgeTab /> },
          { key: "notifications", label: "通知记录", children: <NotificationsTab /> },
          { key: "tickets", label: "工单", children: <TicketsTab /> },
        ]}
      />
    </Card>
  );
}
