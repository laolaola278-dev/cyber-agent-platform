import { useState } from "react";
import { Button, Card, Descriptions, Drawer, Space, Table, Tabs, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { App } from "antd";
import { usePageList } from "../hooks/usePageList";
import { getPlaybook, getPlaybookExecution, resumePlaybookExecution } from "../api/client";
import type { Playbook, PlaybookExecution } from "../types";
import { formatTime, statusTag } from "../api/constants";
import { errorMessage } from "../api/http";

const { Text } = Typography;

function PlaybooksTab({ message }: { message: { success: (m: string) => void; error: (m: string) => void } }) {
  const { rows, loading, pagination, refresh } = usePageList<Playbook>("/playbooks");
  const [detail, setDetail] = useState<Playbook | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const openDetail = async (id: string) => {
    setDetailOpen(true);
    try { setDetail(await getPlaybook(id)); }
    catch (error) { message.error(errorMessage(error, "加载 Playbook 失败")); }
  };

  const columns: ColumnsType<Playbook> = [
    { title: "名称", dataIndex: "name", ellipsis: true },
    { title: "版本", dataIndex: "version", width: 80 },
    { title: "启用", dataIndex: "enabled", width: 80, render: (v: boolean) => <Tag color={v ? "green" : "default"}>{v ? "是" : "否"}</Tag> },
    { title: "描述", dataIndex: "description", ellipsis: true },
    { title: "更新时间", dataIndex: "updated_at", width: 160, render: formatTime },
    { title: "", width: 90, render: (_, row) => <Button size="small" onClick={() => void openDetail(row.id)}>详情</Button> },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Button onClick={refresh}>刷新</Button>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination} />
      <Drawer title={detail ? `Playbook · ${detail.name}` : "Playbook 详情"} width={640} open={detailOpen} onClose={() => setDetailOpen(false)}>
        {detail ? (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Descriptions bordered column={2} size="small">
              <Descriptions.Item label="名称">{detail.name}</Descriptions.Item>
              <Descriptions.Item label="版本">{detail.version}</Descriptions.Item>
              <Descriptions.Item label="启用">{detail.enabled ? "是" : "否"}</Descriptions.Item>
              <Descriptions.Item label="更新">{formatTime(detail.updated_at)}</Descriptions.Item>
              <Descriptions.Item label="描述" span={2}>{detail.description ?? "—"}</Descriptions.Item>
            </Descriptions>
            <Card size="small" title="文档（YAML 结构）">
              <pre style={{ margin: 0, fontSize: 12, maxHeight: 480, overflow: "auto" }}>{JSON.stringify(detail.document, null, 2)}</pre>
            </Card>
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
}

function ExecutionsTab({ message }: { message: { success: (m: string) => void; error: (m: string) => void } }) {
  const { rows, loading, pagination, refresh } = usePageList<PlaybookExecution>("/playbooks/executions");
  const [detail, setDetail] = useState<PlaybookExecution | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [acting, setActing] = useState(false);

  const openDetail = async (id: string) => {
    setDetailOpen(true);
    try { setDetail(await getPlaybookExecution(id)); }
    catch (error) { message.error(errorMessage(error, "加载执行详情失败")); }
  };

  const doResume = async (row: PlaybookExecution) => {
    setActing(true);
    try {
      const updated = await resumePlaybookExecution(row.id, { actor: "console-operator" });
      message.success("已恢复执行");
      if (detailOpen && detail?.id === updated.id) setDetail(updated);
      refresh();
    } catch (error) { message.error(errorMessage(error, "恢复失败")); }
    finally { setActing(false); }
  };

  const columns: ColumnsType<PlaybookExecution> = [
    { title: "状态", dataIndex: "status", width: 120, render: statusTag },
    { title: "触发", dataIndex: "trigger_type", width: 120 },
    { title: "发起人", dataIndex: "actor", width: 130 },
    { title: "当前步骤", dataIndex: "current_step", ellipsis: true, render: (v: string | null) => v ?? "—" },
    { title: "错误", dataIndex: "error", ellipsis: true, render: (v: string | null) => v ?? "—" },
    { title: "开始时间", dataIndex: "started_at", width: 160, render: formatTime },
    {
      title: "", width: 150, render: (_, row) => (
        <Space>
          <Button size="small" onClick={() => void openDetail(row.id)}>详情</Button>
          {row.status === "FAILED" && (
            <Button size="small" loading={acting} onClick={() => void doResume(row)}>恢复</Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Button onClick={refresh}>刷新</Button>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination} />
      <Drawer title="执行详情" width={720} open={detailOpen} onClose={() => setDetailOpen(false)}>
        {detail ? (
          <Space direction="vertical" size={14} style={{ width: "100%" }}>
            <Descriptions bordered column={2} size="small">
              <Descriptions.Item label="状态">{statusTag(detail.status)}</Descriptions.Item>
              <Descriptions.Item label="触发">{detail.trigger_type}</Descriptions.Item>
              <Descriptions.Item label="发起人">{detail.actor}</Descriptions.Item>
              <Descriptions.Item label="当前步骤">{detail.current_step ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="开始">{formatTime(detail.started_at)}</Descriptions.Item>
              <Descriptions.Item label="完成">{formatTime(detail.completed_at)}</Descriptions.Item>
              {detail.error && <Descriptions.Item label="错误" span={2}><Text type="danger">{detail.error}</Text></Descriptions.Item>}
            </Descriptions>
            {detail.steps.length > 0 && (
              <Card size="small" title="步骤">
                <Table
                  rowKey="id" size="small" pagination={false} dataSource={detail.steps}
                  columns={[
                    { title: "步骤", dataIndex: "step_id" },
                    { title: "类型", dataIndex: "node_type", width: 100 },
                    { title: "能力", dataIndex: "capability", render: (v: string | null) => v ? <Tag color="cyan">{v}</Tag> : "—" },
                    { title: "状态", dataIndex: "status", width: 110, render: statusTag },
                    { title: "尝试", key: "attempt", width: 80, render: (_, s) => `${s.attempt}/${s.max_attempts}` },
                    { title: "错误", dataIndex: "error", ellipsis: true },
                  ]}
                />
              </Card>
            )}
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
}

export default function PlaybooksPage() {
  const { message } = App.useApp();
  return (
    <Card>
      <Tabs
        items={[
          { key: "playbooks", label: "Playbook", children: <PlaybooksTab message={message} /> },
          { key: "executions", label: "执行记录", children: <ExecutionsTab message={message} /> },
        ]}
      />
    </Card>
  );
}
