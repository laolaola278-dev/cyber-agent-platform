import { Badge, Card, Table, Tag } from "antd";
import type { PluginItem } from "../types";
import { statusColor } from "../api/constants";

interface PluginsPageProps {
  plugins: PluginItem[];
  loading: boolean;
}

export default function PluginsPage({ plugins, loading }: PluginsPageProps) {
  return (
    <Card title="Plugin Inventory">
      <Table rowKey="id" loading={loading} dataSource={plugins} columns={[
        { title: "Domain", dataIndex: "domain" }, { title: "Plugin", dataIndex: "name" }, { title: "Version", dataIndex: "version" },
        { title: "Health", dataIndex: "health_status", render: (v: string) => <Badge status={statusColor(v) === "success" ? "success" : "default"} text={v} /> },
        { title: "Capabilities", dataIndex: "capabilities", render: (v: string[]) => v.map((x) => <Tag key={x}>{x}</Tag>) },
        { title: "Certified", dataIndex: "certified", render: (v: boolean) => (v ? "Yes" : "No") },
      ]} />
    </Card>
  );
}
