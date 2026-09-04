import { Card, Col, Descriptions, Progress, Row, Space, Statistic, Typography } from "antd";
import type { Dashboard } from "../types";

const { Text, Title, Paragraph } = Typography;

interface DashboardPageProps {
  dashboard: Dashboard | null;
}

export default function DashboardPage({ dashboard }: DashboardPageProps) {
  if (!dashboard) return null;
  return (
    <Space direction="vertical" size={20} style={{ width: "100%" }}>
      <div>
        <Text className="eyebrow">CAP / V1.0 PRODUCTIZATION</Text>
        <Title level={2}>安全运营态势</Title>
        <Paragraph type="secondary">统一聚合既有领域数据；管理台不直接访问数据库，不引入新的安全能力。</Paragraph>
      </div>
      <Row gutter={[16, 16]}>
        {[
          ["Assets", dashboard.counts.assets], ["Incidents", dashboard.counts.incidents],
          ["Security Events", dashboard.counts.security_events], ["Findings", dashboard.counts.findings],
        ].map(([label, value]) => (
          <Col xs={12} xl={6} key={String(label)}>
            <Card className="metric-card"><Statistic title={label} value={value} /></Card>
          </Col>
        ))}
      </Row>
      <Row gutter={[16, 16]}>
        {[
          ["Response 成功率", dashboard.responses.success_rate],
          ["Notification 成功率", dashboard.notifications.success_rate],
          ["Playbook 成功率", dashboard.playbooks.success_rate],
          ["Worker 利用率", dashboard.workers.utilization],
        ].map(([label, value]) => (
          <Col xs={24} md={12} xl={6} key={String(label)}>
            <Card><Text>{label}</Text><Progress percent={Math.round(Number(value) * 100)} strokeColor="#22d3ee" /></Card>
          </Col>
        ))}
      </Row>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="Worker 健康">
            <Descriptions column={2}>
              <Descriptions.Item label="在线">{dashboard.workers.healthy}/{dashboard.workers.total}</Descriptions.Item>
              <Descriptions.Item label="活动执行">{dashboard.workers.active_executions}</Descriptions.Item>
              <Descriptions.Item label="容量">{dashboard.workers.capacity}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Plugin 状态">
            <Descriptions column={2}>
              <Descriptions.Item label="健康">{dashboard.plugins.healthy}/{dashboard.plugins.total}</Descriptions.Item>
              <Descriptions.Item label="已启用">{dashboard.plugins.enabled}</Descriptions.Item>
              <Descriptions.Item label="待审批 Playbook">{dashboard.playbooks.waiting_approval}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
