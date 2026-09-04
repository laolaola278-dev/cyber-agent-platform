import { Card, Col, Row, Table, Tag } from "antd";
import type { PlatformUser, Role } from "../types";

interface AccessPageProps {
  roles: Role[];
  users: PlatformUser[];
}

export default function AccessPage({ roles, users }: AccessPageProps) {
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={12}>
        <Card title="Roles">
          <Table rowKey="name" dataSource={roles} pagination={false} columns={[
            { title: "角色", dataIndex: "name" }, { title: "说明", dataIndex: "description" },
            { title: "权限数", dataIndex: "permissions", render: (v: string[]) => v.length },
          ]} />
        </Card>
      </Col>
      <Col xs={24} xl={12}>
        <Card title="Local Users">
          <Table rowKey="username" dataSource={users} pagination={false} columns={[
            { title: "用户", dataIndex: "display_name" }, { title: "标识", dataIndex: "username" },
            { title: "角色", dataIndex: "roles", render: (v: string[]) => v.map((x) => <Tag key={x}>{x}</Tag>) },
          ]} />
        </Card>
      </Col>
    </Row>
  );
}
