import { Alert, Button, Card, Descriptions, Empty, Spin, Tag } from "antd";
import type { SettingsView } from "../types";

interface SettingsPageProps {
  settings: SettingsView | null;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export default function SettingsPage({ settings, loading, error, onRetry }: SettingsPageProps) {
  // A bare <Empty/> here used to conflate three different states: still loading,
  // genuinely nothing, and the request having failed.
  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="系统配置不可用"
        description={error}
        action={onRetry ? <Button size="small" onClick={onRetry}>重试</Button> : undefined}
      />
    );
  }
  if (!settings) {
    return (
      <Card title="System Settings">
        {loading ? <Spin style={{ display: "block", margin: "24px auto" }} /> : <Empty description="暂无配置数据" />}
      </Card>
    );
  }
  return (
    <Card title="System Settings" extra={<Tag>只读</Tag>}>
      <Alert type="info" showIcon message="配置仅展示脱敏投影，控制台不提供修改入口。" style={{ marginBottom: 20 }} />
      <Descriptions bordered column={{ xs: 1, md: 2 }}>
        {Object.entries(settings).map(([key, value]) => (
          <Descriptions.Item key={key} label={key}>
            {Array.isArray(value) ? value.join(", ") : String(value)}
          </Descriptions.Item>
        ))}
      </Descriptions>
    </Card>
  );
}
