import { useState } from "react";
import { App, Button, Card, Form, Input, Modal, Space, Table, Tag, Typography } from "antd";
import type { ApprovalItem } from "../types";
import { formatTime, statusColor } from "../api/constants";
import { errorMessage } from "../api/http";
import { approveResponsePlan, rejectResponsePlan } from "../api/client";
import { ListError } from "../components/ListError";

const { Text, Title } = Typography;

interface ApprovalsPageProps {
  approvals: ApprovalItem[];
  loading: boolean;
  error?: string | null;
  onRetry?: () => void;
  /** Re-read the queue after a decision lands so the table is never stale. */
  onChanged?: () => void;
}

/** Only a plan still awaiting a decision can be moved; anything else is history. */
const DECIDABLE = "PENDING_APPROVAL";

const isExpired = (iso: string): boolean => {
  const at = Date.parse(iso);
  return Number.isFinite(at) && at < Date.now();
};

const remaining = (iso: string): string => {
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return "—";
  const seconds = Math.round((at - Date.now()) / 1000);
  if (seconds <= 0) return "已过期";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.round(minutes / 60)} 小时`;
};

export default function ApprovalsPage({
  approvals, loading, error = null, onRetry, onChanged,
}: ApprovalsPageProps) {
  const { message } = App.useApp();
  const [target, setTarget] = useState<ApprovalItem | null>(null);
  const [mode, setMode] = useState<"approve" | "reject">("approve");
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<{ approver: string; comment?: string }>();

  const openDecision = (row: ApprovalItem, next: "approve" | "reject") => {
    setTarget(row);
    setMode(next);
    form.setFieldsValue({ approver: "console-operator", comment: "" });
  };

  const submit = async (values: { approver: string; comment?: string }) => {
    if (!target) return;
    setSubmitting(true);
    try {
      if (mode === "approve") {
        await approveResponsePlan(target.plan_id, { approver: values.approver, comment: values.comment ?? "" });
        message.success(`已批准 ${target.capability}`);
      } else {
        await rejectResponsePlan(target.plan_id, { approver: values.approver, comment: values.comment ?? "" });
        message.success(`已拒绝 ${target.capability}`);
      }
      setTarget(null);
      onChanged?.();
    } catch (requestError) {
      // The platform is authoritative: an expired or already-decided plan is
      // refused here with the server's own reason rather than pre-empted by a
      // client-side guess about eligibility.
      message.error(errorMessage(requestError, "审批提交失败"));
    } finally {
      setSubmitting(false);
    }
  };

  const pending = approvals.filter((row) => row.approval_state === DECIDABLE).length;

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <div>
        <Text className="eyebrow">CAP · GOVERNANCE</Text>
        <Title level={2} style={{ marginTop: 4 }}>Approval Center</Title>
      </div>
      <ListError error={error} onRetry={onRetry} />
      <Card
        title={`待决策 ${pending} / 共 ${approvals.length}`}
        extra={<Tag color="gold">Platform authoritative</Tag>}
      >
        <Table<ApprovalItem>
          rowKey="plan_id"
          size="small"
          loading={loading}
          dataSource={approvals}
          scroll={{ x: 1180 }}
          pagination={approvals.length > 20 ? { pageSize: 20, showSizeChanger: false } : false}
          locale={{ emptyText: error ? "加载失败" : "暂无待审批计划" }}
          columns={[
            { title: "Capability", dataIndex: "capability", width: 190, ellipsis: true },
            {
              title: "风险", dataIndex: "risk_level", width: 90,
              render: (v: string) => <Tag color={v === "CRITICAL" ? "red" : v === "HIGH" ? "orange" : v === "MEDIUM" ? "gold" : "blue"}>{v}</Tag>,
            },
            { title: "申请人", dataIndex: "requested_by", width: 140, ellipsis: true, render: (v: string) => v || "—" },
            {
              title: "关联事件", dataIndex: "incident_id", width: 120, ellipsis: true,
              render: (v: string) => <Text type="secondary" title={v}>{v.slice(0, 8)}</Text>,
            },
            { title: "审批", dataIndex: "approval_state", width: 150, render: (v: string) => <Tag color={statusColor(v)}>{v}</Tag> },
            { title: "执行", dataIndex: "execution_state", width: 120, render: (v: string) => <Tag color={statusColor(v)}>{v}</Tag> },
            {
              title: "回滚", dataIndex: "rollback_state", width: 130,
              render: (v: string) => <Tag color={statusColor(v)}>{v}</Tag>,
            },
            {
              // Without this an operator works a queue item the platform has
              // already given up on: an expired approval is refused server-side.
              title: "有效期", dataIndex: "expires_at", width: 150,
              render: (v: string, row) => row.approval_state === DECIDABLE ? (
                <Tag color={isExpired(v) ? "red" : "green"}>{isExpired(v) ? "已过期" : `剩 ${remaining(v)}`}</Tag>
              ) : formatTime(v),
            },
            { title: "审批人", dataIndex: "approver", width: 140, ellipsis: true, render: (v: string | null) => v ?? "—" },
            { title: "意见", dataIndex: "comment", ellipsis: true, render: (v: string | null) => v || "—" },
            {
              title: "操作", key: "actions", fixed: "right", width: 150,
              render: (_, row) => (
                <Space size={4}>
                  <Button size="small" type="primary" disabled={row.approval_state !== DECIDABLE} onClick={() => openDecision(row, "approve")}>批准</Button>
                  <Button size="small" danger disabled={row.approval_state !== DECIDABLE} onClick={() => openDecision(row, "reject")}>拒绝</Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Modal
        open={target !== null}
        title={target ? `${mode === "approve" ? "批准" : "拒绝"} · ${target.capability}` : ""}
        okText={mode === "approve" ? "确认批准" : "确认拒绝"}
        cancelText="取消"
        confirmLoading={submitting}
        okButtonProps={{ danger: mode === "reject" }}
        onOk={() => void form.submit()}
        onCancel={() => setTarget(null)}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={(values) => void submit(values)}>
          <Form.Item name="approver" label="审批人" rules={[{ required: true, message: "请输入审批人" }]}>
            <Input placeholder="审批人账号" />
          </Form.Item>
          <Form.Item
            name="comment"
            label={mode === "approve" ? "意见（可选）" : "拒绝理由"}
            rules={mode === "reject" ? [{ required: true, message: "拒绝必须给出理由" }] : undefined}
          >
            <Input.TextArea rows={3} placeholder={mode === "approve" ? "补充说明" : "为什么拒绝这个变更"} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
