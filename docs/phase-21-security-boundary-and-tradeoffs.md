# Phase 21 Security Boundary and Architecture Trade-offs

## 安全边界

1. `/health`、`/ready`、`/metrics` 和 API 文档是公开技术端点；其余端点默认需要可信身份。
2. 身份头只有在代理密钥匹配时有效；未知用户 fail closed。
3. 浏览器不持有代理密钥。生产网关必须丢弃客户端自带身份头并重新注入。
4. UI 权限只改善体验，后端 Middleware/Dependency 才是权威边界。
5. Settings 只返回脱敏投影，不暴露数据库 URL、Redis URL、JWT/Proxy Secret。
6. AuditLog 继续使用既有不可变追加模型；Phase 21 只查询。
7. Dashboard/Plugin/Approval 聚合不写任何领域表。
8. Prometheus 禁止高基数标识；Trace ID 只进入 Trace/Log/Header。

## Trade-offs

- 本地固定目录 vs 动态数据库 RBAC：选择固定目录，获得零 Migration、最小攻击面；代价是不能在线管理用户。
- Middleware 映射 vs 每个 Router 逐项改造：Middleware 提供全覆盖默认拒绝，高权限路由再显式依赖；代价是路径映射需要测试维护。
- 聚合查询 vs 独立读模型表：选择直接聚合，避免数据复制；代价是大规模数据下需缓存或物化视图。
- 内置指标 vs 强耦合 Collector：指标直接暴露、Trace exporter 可选，降低部署门槛；代价是无 exporter 时无集中式 Trace 存储。
- 单体 Console vs Backstage Plugin Runtime：选择单体门户，避免新增 Platform Plane；代价是前端 bundle 较大。

## Safety Case

- 高权限动作仍受原有 Approval/Policy/Verification/Evidence/Audit 控制；RBAC 只增加外层授权，不替换领域控制。
- Phase 21 没有新增 Plugin、Provider、危险 Capability 或业务表。
- Response/Playbook 联合回归验证原有执行与补偿语义未被改变。
