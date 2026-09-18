import { Badge, Card, Space, Table, Tag, Typography } from "antd";
import type { PluginItem } from "../types";
import { statusColor } from "../api/constants";
import { ListError } from "../components/ListError";

const { Text, Title } = Typography;

interface PluginsPageProps {
  plugins: PluginItem[];
  loading: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export default function PluginsPage({ plugins, loading, error = null, onRetry }: PluginsPageProps) {
  const enabledCount = plugins.filter((row) => row.enabled).length;
  const unhealthy = plugins.filter((row) => statusColor(row.health_status) !== "success").length;

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <div>
        <Text className="eyebrow">CAP · PLATFORM</Text>
        <Title level={2} style={{ marginTop: 4 }}>Plugin Inventory</Title>
      </div>
      <ListError error={error} onRetry={onRetry} />
      <Card
        title={`共 ${plugins.length} 个插件 · 已启用 ${enabledCount} · 异常 ${unhealthy}`}
        extra={<Text type="secondary">启用状态与沙箱兼容性由平台返回，只读展示</Text>}
      >
        <Table<PluginItem>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={plugins}
          scroll={{ x: 1080 }}
          pagination={plugins.length > 20 ? { pageSize: 20, showSizeChanger: false } : false}
          locale={{ emptyText: error ? "加载失败" : "暂无插件" }}
          columns={[
            { title: "Domain", dataIndex: "domain", width: 150, ellipsis: true },
            { title: "Plugin", dataIndex: "name", width: 200, ellipsis: true },
            { title: "Version", dataIndex: "version", width: 110 },
            {
              // `enabled` decides whether the scheduler may route work here at
              // all. The platform has no enable/disable endpoint, so this is a
              // state read-out (a Tag), not a control that cannot do anything.
              title: "启用", dataIndex: "enabled", width: 90,
              render: (v: boolean) => (v ? <Tag color="green">已启用</Tag> : <Tag color="default">已停用</Tag>),
            },
            {
              title: "Health", dataIndex: "health_status", width: 130,
              render: (v: string) => <Badge status={statusColor(v) === "success" ? "success" : v === "DEGRADED" ? "warning" : "error"} text={v} />,
            },
            {
              title: "Capabilities", dataIndex: "capabilities",
              render: (v: string[]) => v.length
                ? <Space size={4} wrap>{v.slice(0, 4).map((x) => <Tag key={x}>{x}</Tag>)}{v.length > 4 && <Text type="secondary">+{v.length - 4}</Text>}</Space>
                : <Text type="secondary">—</Text>,
            },
            {
              title: "沙箱兼容", dataIndex: "sandbox_compatible", width: 110,
              render: (v: boolean) => (v ? <Tag color="green">支持</Tag> : <Tag color="default">不支持</Tag>),
            },
            {
              title: "Certified", dataIndex: "certified", width: 110,
              render: (v: boolean) => (v ? <Tag color="blue">已认证</Tag> : <Tag color="orange">未认证</Tag>),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
