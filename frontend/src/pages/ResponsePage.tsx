import { useState } from "react";
import {
  Button, Card, Descriptions, Drawer, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { App } from "antd";
import { usePageList } from "../hooks/usePageList";
import {
  approveResponsePlan, executeResponsePlan, getResponsePlan, listResponsePlugins,
  rejectResponsePlan, rollbackResponsePlan,
} from "../api/client";
import type { ResponsePlan } from "../types";
import { APPROVAL_STATES, EXECUTION_STATES, RISK_LEVELS, formatTime, statusTag } from "../api/constants";
import { errorMessage } from "../api/http";
import { useEffect } from "react";

const { Text } = Typography;

export default function ResponsePage() {
  const { message } = App.useApp();
  const [filters, setFilters] = useState<{ approval_state?: string; execution_state?: string }>({});
  const { rows, loading, pagination, refresh } = usePageList<ResponsePlan>("/response/plans", filters);
  const [detail, setDetail] = useState<ResponsePlan | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [plugins, setPlugins] = useState<{ name: string }[]>([]);
  const [decisionTarget, setDecisionTarget] = useState<ResponsePlan | null>(null);
  const [decisionMode, setDecisionMode] = useState<"approve" | "reject">("approve");
  const [rollbackTarget, setRollbackTarget] = useState<ResponsePlan | null>(null);
  const [acting, setActing] = useState(false);
  const [decisionForm] = Form.useForm();
  const [rollbackForm] = Form.useForm();

  useEffect(() => {
    listResponsePlugins()
      .then((data) => setPlugins(data))
      .catch(() => setPlugins([]));
  }, []);

  const openDetail = async (id: string) => {
    setDetailOpen(true);
    try { setDetail(await getResponsePlan(id)); }
    catch (error) { message.error(errorMessage(error, "加载响应计划失败")); }
  };

  const doExecute = async (plan: ResponsePlan) => {
    setActing(true);
    try {
      const updated = await executeResponsePlan(plan.id, { actor: "console-operator" });
      message.success("已下发执行");
      if (detailOpen && detail?.id === updated.id) setDetail(updated);
      refresh();
    } catch (error) { message.error(errorMessage(error, "执行失败")); }
    finally { setActing(false); }
  };

  const submitDecision = async (values: { approver: string; comment?: string }) => {
    if (!decisionTarget) return;
    setActing(true);
    try {
      const updated = decisionMode === "approve"
        ? await approveResponsePlan(decisionTarget.id, { approver: values.approver, comment: values.comment ?? "" })
        : await rejectResponsePlan(decisionTarget.id, { approver: values.approver, comment: values.comment ?? "" });
      message.success(decisionMode === "approve" ? "已批准" : "已拒绝");
      setDecisionTarget(null);
      decisionForm.resetFields();
      if (detailOpen && detail?.id === updated.id) setDetail(updated);
      refresh();
    } catch (error) { message.error(errorMessage(error, "审批失败")); }
    finally { setActing(false); }
  };

  const submitRollback = async (values: { actor: string; reason: string }) => {
    if (!rollbackTarget) return;
    setActing(true);
    try {
      const updated = await rollbackResponsePlan(rollbackTarget.id, values);
      message.success("已发起回滚");
      setRollbackTarget(null);
      rollbackForm.resetFields();
      if (detailOpen && detail?.id === updated.id) setDetail(updated);
      refresh();
    } catch (error) { message.error(errorMessage(error, "回滚失败")); }
    finally { setActing(false); }
  };

  const columns: ColumnsType<ResponsePlan> = [
    { title: "能力", dataIndex: "target_capability", width: 150, render: (v: string) => <Tag color="cyan">{v}</Tag> },
    { title: "风险", dataIndex: "risk_level", width: 90, render: (v: string) => <Tag color={v === "CRITICAL" || v === "HIGH" ? "red" : "orange"}>{v}</Tag> },
    { title: "审批", dataIndex: "approval_state", width: 140, render: statusTag },
    { title: "执行", dataIndex: "execution_state", width: 120, render: statusTag },
    { title: "申请人", dataIndex: "requested_by", width: 120 },
    { title: "过期时间", dataIndex: "expires_at", width: 160, render: formatTime },
    {
      title: "操作", width: 280, render: (_, row) => (
        <Space>
          <Button size="small" onClick={() => void openDetail(row.id)}>详情</Button>
          {row.approval_state === "PENDING_APPROVAL" && (
            <>
              <Button size="small" type="primary" onClick={() => { setDecisionTarget(row); setDecisionMode("approve"); decisionForm.setFieldsValue({ approver: "console-operator" }); }}>批准</Button>
              <Button size="small" danger onClick={() => { setDecisionTarget(row); setDecisionMode("reject"); decisionForm.setFieldsValue({ approver: "console-operator" }); }}>拒绝</Button>
            </>
          )}
          {row.approval_state === "APPROVED" && row.execution_state !== "SUCCEEDED" && row.execution_state !== "RUNNING" && (
            <Popconfirm title="确认下发执行该响应计划？" onConfirm={() => void doExecute(row)}>
              <Button size="small" loading={acting}>执行</Button>
            </Popconfirm>
          )}
          {row.execution_state === "SUCCEEDED" && row.rollback_state === "AVAILABLE" && (
            <Button size="small" onClick={() => { setRollbackTarget(row); rollbackForm.setFieldsValue({ actor: "console-operator" }); }}>回滚</Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Space wrap>
          <Select
            allowClear placeholder="审批状态" style={{ width: 160 }}
            options={APPROVAL_STATES.map((s) => ({ value: s }))}
            value={filters.approval_state}
            onChange={(v) => setFilters((f) => ({ ...f, approval_state: v }))}
          />
          <Select
            allowClear placeholder="执行状态" style={{ width: 140 }}
            options={EXECUTION_STATES.map((s) => ({ value: s }))}
            value={filters.execution_state}
            onChange={(v) => setFilters((f) => ({ ...f, execution_state: v }))}
          />
          <Button onClick={refresh}>刷新</Button>
        </Space>
        {plugins.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Text type="secondary">已注册响应插件：</Text>
            {plugins.map((p) => <Tag key={p.name}>{p.name}</Tag>)}
            <Text type="warning" style={{ marginLeft: 8 }}>响应平面 provider 为 mock-only（known limitation），操作走真实审批/审计链路</Text>
          </div>
        )}
      </Card>
      <Card title="响应计划">
        <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination} />
      </Card>

      <Drawer title={detail ? `响应计划 · ${detail.target_capability}` : "响应计划详情"} width={720} open={detailOpen} onClose={() => setDetailOpen(false)}>
        {detail ? (
          <Space direction="vertical" size={14} style={{ width: "100%" }}>
            <Descriptions bordered column={2} size="small">
              <Descriptions.Item label="能力"><Tag color="cyan">{detail.target_capability}</Tag></Descriptions.Item>
              <Descriptions.Item label="风险">{detail.risk_level}</Descriptions.Item>
              <Descriptions.Item label="审批">{statusTag(detail.approval_state)}</Descriptions.Item>
              <Descriptions.Item label="执行">{statusTag(detail.execution_state)}</Descriptions.Item>
              <Descriptions.Item label="回滚">{statusTag(detail.rollback_state)}</Descriptions.Item>
              <Descriptions.Item label="支持回滚">{detail.supports_rollback ? "是" : "否"}</Descriptions.Item>
              <Descriptions.Item label="申请人">{detail.requested_by}</Descriptions.Item>
              <Descriptions.Item label="过期">{formatTime(detail.expires_at)}</Descriptions.Item>
              <Descriptions.Item label="理由" span={2}>{detail.reason}</Descriptions.Item>
              <Descriptions.Item label="参数" span={2}><pre style={{ margin: 0, fontSize: 12 }}>{JSON.stringify(detail.parameters, null, 2)}</pre></Descriptions.Item>
            </Descriptions>
            {detail.approvals.length > 0 && (
              <Card size="small" title="审批记录">
                {detail.approvals.map((a) => (
                  <div key={a.id}>{statusTag(a.decision)} {a.approver} · {formatTime(a.decided_at)} {a.comment ? `· ${a.comment}` : ""}</div>
                ))}
              </Card>
            )}
            {detail.executions.length > 0 && (
              <Card size="small" title="执行记录">
                {detail.executions.map((e) => (
                  <div key={e.id}>{statusTag(e.state)} {e.actor} · {formatTime(e.started_at)}{e.error ? ` · ${e.error}` : ""}</div>
                ))}
              </Card>
            )}
            {detail.rollbacks.length > 0 && (
              <Card size="small" title="回滚记录">
                {detail.rollbacks.map((r) => (
                  <div key={r.id}>{statusTag(r.state)} {r.actor} · {r.reason}</div>
                ))}
              </Card>
            )}
          </Space>
        ) : <Text type="secondary">加载中…</Text>}
      </Drawer>

      <Modal
        title={decisionTarget ? `${decisionMode === "approve" ? "批准" : "拒绝"} · ${decisionTarget.target_capability}` : ""}
        open={decisionTarget !== null} onCancel={() => setDecisionTarget(null)}
        confirmLoading={acting} onOk={() => decisionForm.submit()}
      >
        <Form form={decisionForm} layout="vertical" onFinish={submitDecision}>
          <Form.Item name="approver" label="审批人" rules={[{ required: true, message: "请输入审批人" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="comment" label={decisionMode === "approve" ? "意见（可选）" : "拒绝理由"}>
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={rollbackTarget ? `回滚 · ${rollbackTarget.target_capability}` : ""}
        open={rollbackTarget !== null} onCancel={() => setRollbackTarget(null)}
        confirmLoading={acting} onOk={() => rollbackForm.submit()}
      >
        <Form form={rollbackForm} layout="vertical" onFinish={submitRollback}>
          <Form.Item name="actor" label="操作人" rules={[{ required: true, message: "请输入操作人" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="reason" label="回滚原因" rules={[{ required: true, message: "请输入回滚原因" }]}>
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
