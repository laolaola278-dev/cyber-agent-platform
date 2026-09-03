import { useState } from "react";
import {
  Button, Card, Descriptions, Drawer, Form, Input, Modal, Select, Space, Table, Tag, Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { App } from "antd";
import { usePageList } from "../hooks/usePageList";
import { createAsset, getAsset } from "../api/client";
import type { Asset } from "../types";
import { ASSET_TYPES, formatTime } from "../api/constants";
import { errorMessage } from "../api/http";

const { Text } = Typography;

export default function AssetsPage() {
  const { message } = App.useApp();
  const [filters, setFilters] = useState<{ name?: string; asset_type?: string; environment?: string }>({});
  const { rows, loading, pagination, refresh } = usePageList<Asset>("/assets", filters);
  const [detail, setDetail] = useState<Asset | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [acting, setActing] = useState(false);
  const [form] = Form.useForm();

  const openDetail = async (id: string) => {
    setDetailOpen(true);
    try { setDetail(await getAsset(id)); }
    catch (error) { message.error(errorMessage(error, "加载资产详情失败")); }
  };

  const submitCreate = async (values: {
    asset_type: string; name: string; value: string;
    owner?: string; environment?: string; criticality?: string;
  }) => {
    setActing(true);
    try {
      await createAsset({
        asset_type: values.asset_type, name: values.name, value: values.value,
        owner: values.owner || null, environment: values.environment || null,
        criticality: values.criticality || null,
      });
      message.success("资产已登记");
      setCreateOpen(false);
      form.resetFields();
      refresh();
    } catch (error) { message.error(errorMessage(error, "创建失败")); }
    finally { setActing(false); }
  };

  const columns: ColumnsType<Asset> = [
    { title: "类型", dataIndex: "asset_type", width: 130, render: (v: string) => <Tag color="geekblue">{v}</Tag> },
    { title: "名称", dataIndex: "name", ellipsis: true },
    { title: "值", dataIndex: "value", ellipsis: true, width: 220 },
    { title: "环境", dataIndex: "environment", width: 100, render: (v: string | null) => v ?? "—" },
    { title: "关键度", dataIndex: "criticality", width: 100, render: (v: string | null) => v ?? "—" },
    { title: "标签", dataIndex: "tags", width: 200, render: (tags: string[]) => tags.slice(0, 3).map((t) => <Tag key={t}>{t}</Tag>) },
    { title: "更新时间", dataIndex: "updated_at", width: 160, render: formatTime },
    { title: "", width: 90, render: (_, row) => <Button size="small" onClick={() => void openDetail(row.id)}>详情</Button> },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Space wrap>
          <Input.Search
            allowClear placeholder="按名称搜索" style={{ width: 220 }}
            onSearch={(v) => setFilters((f) => ({ ...f, name: v || undefined }))}
          />
          <Select
            allowClear placeholder="类型" style={{ width: 140 }}
            options={ASSET_TYPES.map((t) => ({ value: t }))}
            value={filters.asset_type}
            onChange={(v) => setFilters((f) => ({ ...f, asset_type: v }))}
          />
          <Input.Search
            allowClear placeholder="环境" style={{ width: 160 }}
            onSearch={(v) => setFilters((f) => ({ ...f, environment: v || undefined }))}
          />
          <Button onClick={refresh}>刷新</Button>
          <Button type="primary" onClick={() => setCreateOpen(true)}>登记资产</Button>
        </Space>
      </Card>
      <Card title="资产清单">
        <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} pagination={pagination} />
      </Card>

      <Drawer title={detail ? `资产 · ${detail.name}` : "资产详情"} width={640} open={detailOpen} onClose={() => setDetailOpen(false)}>
        {detail ? (
          <Descriptions bordered column={1} size="small">
            <Descriptions.Item label="类型"><Tag color="geekblue">{detail.asset_type}</Tag></Descriptions.Item>
            <Descriptions.Item label="名称">{detail.name}</Descriptions.Item>
            <Descriptions.Item label="值">{detail.value}</Descriptions.Item>
            <Descriptions.Item label="规范值">{detail.canonical_value}</Descriptions.Item>
            <Descriptions.Item label="Owner">{detail.owner ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="业务单元">{detail.business_unit ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="环境">{detail.environment ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="关键度">{detail.criticality ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="风险">{detail.risk ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="标签">{detail.tags.length ? detail.tags.map((t) => <Tag key={t}>{t}</Tag>) : "—"}</Descriptions.Item>
            <Descriptions.Item label="能力">{detail.capabilities.length ? detail.capabilities.map((c) => <Tag key={c} color="cyan">{c}</Tag>) : "—"}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatTime(detail.created_at)}</Descriptions.Item>
          </Descriptions>
        ) : <Text type="secondary">加载中…</Text>}
      </Drawer>

      <Modal
        title="登记资产" open={createOpen} onCancel={() => setCreateOpen(false)}
        confirmLoading={acting} onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={submitCreate}>
          <Form.Item name="asset_type" label="类型" rules={[{ required: true, message: "请选择类型" }]}>
            <Select options={ASSET_TYPES.map((t) => ({ value: t }))} />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="资产名称" />
          </Form.Item>
          <Form.Item name="value" label="值" rules={[{ required: true, message: "请输入值（IP/域名/主机名等）" }]}>
            <Input placeholder="如 10.0.0.5 / web.example.com" />
          </Form.Item>
          <Form.Item name="owner" label="Owner">
            <Input placeholder="责任人（可选）" />
          </Form.Item>
          <Space>
            <Form.Item name="environment" label="环境">
              <Input placeholder="prod / staging（可选）" style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="criticality" label="关键度">
              <Input placeholder="high / low（可选）" style={{ width: 200 }} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </Space>
  );
}
