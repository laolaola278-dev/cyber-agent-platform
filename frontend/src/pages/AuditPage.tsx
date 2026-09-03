import { useState } from "react";
import { Button, Card, DatePicker, Input, Space, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { usePageList } from "../hooks/usePageList";
import type { AuditEvent } from "../types";
import { formatTime } from "../api/constants";

const { RangePicker } = DatePicker;

export default function AuditPage() {
  const [filters, setFilters] = useState<{
    operator?: string; event_type?: string; resource?: string;
    start?: string; end?: string;
  }>({});
  const { rows, loading, pagination, refresh } = usePageList<AuditEvent>("/audit", filters, 50);

  const applyRange = (_: unknown, range: [string, string] | null) => {
    setFilters((f) => ({
      ...f,
      start: range?.[0] ? dayjs(range[0]).toISOString() : undefined,
      end: range?.[1] ? dayjs(range[1]).toISOString() : undefined,
    }));
  };

  const columns: ColumnsType<AuditEvent> = [
    { title: "时间", dataIndex: "timestamp", width: 170, render: formatTime },
    { title: "操作人", dataIndex: "operator", width: 140 },
    { title: "事件", dataIndex: "action", width: 200, render: (v: string) => <Tag color="geekblue">{v}</Tag> },
    { title: "资源", dataIndex: "resource", ellipsis: true },
    { title: "Trace ID", dataIndex: "trace_id", width: 170, ellipsis: true },
    { title: "错误", dataIndex: "error", ellipsis: true, render: (v: string | null) => v ?? "—" },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Space wrap>
          <Input.Search
            allowClear placeholder="操作人" style={{ width: 170 }}
            onSearch={(v) => setFilters((f) => ({ ...f, operator: v || undefined }))}
          />
          <Input.Search
            allowClear placeholder="事件类型" style={{ width: 190 }}
            onSearch={(v) => setFilters((f) => ({ ...f, event_type: v || undefined }))}
          />
          <Input.Search
            allowClear placeholder="资源" style={{ width: 190 }}
            onSearch={(v) => setFilters((f) => ({ ...f, resource: v || undefined }))}
          />
          <RangePicker showTime onChange={applyRange} placeholder={["开始", "结束"]} />
          <Button onClick={refresh}>刷新</Button>
        </Space>
      </Card>
      <Card title="审计事件（不可变）" extra={<Tag color="blue">audit.read</Tag>}>
        <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination} />
      </Card>
    </Space>
  );
}
