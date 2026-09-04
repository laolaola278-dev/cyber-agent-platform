import { Alert, Card, Descriptions, Empty, Tag } from "antd";
import type { SettingsView } from "../types";

interface SettingsPageProps {
  settings: SettingsView | null;
}

export default function SettingsPage({ settings }: SettingsPageProps) {
  if (!settings) return <Empty />;
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
