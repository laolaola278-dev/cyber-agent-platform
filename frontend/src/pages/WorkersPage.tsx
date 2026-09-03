import { useEffect, useState } from "react";
import { Badge, Button, Card, Descriptions, Drawer, Space, Statistic, Table, Tabs, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { App } from "antd";
import { usePageList } from "../hooks/usePageList";
import { listWorkers } from "../api/client";
import type { SandboxExecution, Worker } from "../types";
import { formatTime, statusTag } from "../api/constants";
import { errorMessage } from "../api/http";

function WorkersTab({ message }: { message: { error: (m: string) => void } }) {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listWorkers()
      .then((data) => setWorkers(data))
      .catch((error) => message.error(errorMessage(error, "加载 Worker 失败")))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const columns: ColumnsType<Worker> = [
    { title: "名称", dataIndex: "name", ellipsis: true },
    { title: "状态", dataIndex: "status", width: 110, render: (v: string) => <Badge status={v === "ONLINE" ? "success" : v === "OFFLINE" ? "error" : "warning"} text={v} /> },
    { title: "运行时", dataIndex: "runtime_version", width: 120 },
    { title: "并发", key: "concurrency", width: 110, render: (_, row) => `${row.active_executions}/${row.max_concurrency}` },
    { title: "能力", dataIndex: "capabilities", render: (caps: string[]) => caps.map((c) => <Tag key={c} color="cyan">{c}</Tag>) },
    { title: "最后心跳", dataIndex: "last_heartbeat_at", width: 170, render: formatTime },
  ];

  const online = workers.filter((w) => w.status === "ONLINE").length;

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space size="large">
        <Statistic title="Worker 总数" value={workers.length} />
        <Statistic title="在线" value={online} valueStyle={{ color: "#52c41a" }} />
        <Statistic title="活动执行" value={workers.reduce((sum, w) => sum + w.active_executions, 0)} />
      </Space>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={workers} pagination={false} />
    </Space>
  );
}

function SandboxTab() {
  const { rows, loading, pagination, refresh } = usePageList<SandboxExecution>("/sandbox");
  const [detail, setDetail] = useState<SandboxExecution | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const columns: ColumnsType<SandboxExecution> = [
    { title: "插件", dataIndex: "plugin_name", width: 160, render: (v, row) => <Space direction="vertical" size={0}><span>{v}</span><Tag>{row.plugin_version}</Tag></Space> },
    { title: "操作", dataIndex: "operation", width: 150 },
    { title: "提供者", dataIndex: "provider", width: 130 },
    { title: "状态", dataIndex: "status", width: 110, render: statusTag },
    { title: "超时", dataIndex: "timed_out", width: 80, render: (v: boolean) => (v ? <Tag color="red">是</Tag> : "—") },
    { title: "开始", dataIndex: "started_at", width: 170, render: formatTime },
    { title: "结束", dataIndex: "finished_at", width: 170, render: formatTime },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Button onClick={refresh}>刷新</Button>
      <Table
        rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination}
        onRow={(row) => ({ onClick: () => setDetail(row) })}
      />
      <Drawer title={detail ? `沙箱执行 · ${detail.plugin_name}` : ""} width={640} open={detailOpen} onClose={() => setDetailOpen(false)}>
        {detail ? (
          <Descriptions bordered column={1} size="small">
            <Descriptions.Item label="执行 ID">{detail.execution_id}</Descriptions.Item>
            <Descriptions.Item label="Worker">{detail.worker_id}</Descriptions.Item>
            <Descriptions.Item label="Profile">{detail.profile_id ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="操作 / 提供者">{detail.operation} / {detail.provider}</Descriptions.Item>
            <Descriptions.Item label="状态">{statusTag(detail.status)}</Descriptions.Item>
            <Descriptions.Item label="结果元数据">{JSON.stringify(detail.result_metadata, null, 2)}</Descriptions.Item>
            <Descriptions.Item label="错误">{detail.error ?? "—"}</Descriptions.Item>
          </Descriptions>
        ) : null}
      </Drawer>
    </Space>
  );
}

export default function WorkersPage() {
  const { message } = App.useApp();
  return (
    <Card>
      <Tabs
        items={[
          { key: "workers", label: "Workers", children: <WorkersTab message={message} /> },
          { key: "sandbox", label: "沙箱执行", children: <SandboxTab /> },
        ]}
      />
    </Card>
  );
}
