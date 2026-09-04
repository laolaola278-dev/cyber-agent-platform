import { Card, Table, Tag } from "antd";
import type { ApprovalItem } from "../types";
import { statusColor } from "../api/constants";

interface ApprovalsPageProps {
  approvals: ApprovalItem[];
  loading: boolean;
}

export default function ApprovalsPage({ approvals, loading }: ApprovalsPageProps) {
  return (
    <Card title="Approval Center" extra={<Tag color="gold">Platform authoritative</Tag>}>
      <Table rowKey="plan_id" loading={loading} dataSource={approvals} columns={[
        { title: "Capability", dataIndex: "capability" }, { title: "风险", dataIndex: "risk_level" },
        { title: "审批", dataIndex: "approval_state", render: (v: string) => <Tag color={statusColor(v)}>{v}</Tag> },
        { title: "执行", dataIndex: "execution_state", render: (v: string) => <Tag color={statusColor(v)}>{v}</Tag> },
        { title: "审批人", dataIndex: "approver", render: (v: string | null) => v ?? "待审批" },
        { title: "意见", dataIndex: "comment", render: (v: string | null) => v || "—" },
      ]} />
    </Card>
  );
}
