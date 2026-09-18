import { Card, Col, Row, Table, Tag } from "antd";
import { ListError } from "../components/ListError";
import type { PlatformUser, Role } from "../types";

interface AccessPageProps {
  roles: Role[];
  users: PlatformUser[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export default function AccessPage({ roles, users, loading, error, onRetry }: AccessPageProps) {
  // Both tables used to render with no loading prop at all, so a slow or failed
  // directory load looked exactly like an empty one.
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={12}>
        <Card title="Roles">
          <ListError error={error} onRetry={onRetry} description="角色目录" />
          <Table
            rowKey="name"
            dataSource={roles}
            loading={loading}
            pagination={false}
            scroll={{ x: "max-content" }}
            locale={{ emptyText: error ? "加载失败" : "暂无角色" }}
            columns={[
              { title: "角色", dataIndex: "name" },
              { title: "说明", dataIndex: "description" },
              { title: "权限数", dataIndex: "permissions", render: (v: string[]) => v.length },
            ]}
          />
        </Card>
      </Col>
      <Col xs={24} xl={12}>
        <Card title="Local Users">
          <ListError error={error} onRetry={onRetry} description="用户目录" />
          <Table
            rowKey="username"
            dataSource={users}
            loading={loading}
            pagination={false}
            scroll={{ x: "max-content" }}
            locale={{ emptyText: error ? "加载失败" : "暂无用户" }}
            columns={[
              { title: "用户", dataIndex: "display_name" },
              { title: "标识", dataIndex: "username" },
              { title: "角色", dataIndex: "roles", render: (v: string[]) => v.map((x) => <Tag key={x}>{x}</Tag>) },
            ]}
          />
        </Card>
      </Col>
    </Row>
  );
}
