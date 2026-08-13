# Phase 21 Development Report

## 1. Executive Summary

Phase 21（Web Console、RBAC 与 Observability，v1.0 Productization）已完成。

本阶段严格保持既定边界：

- 未新增安全能力。
- 未新增 Plugin 或 Provider。
- 未新增 Platform Plane。
- 未修改 Assessment、Detection、Incident、Response、Notification、Playbook 等领域写模型。
- 未新增数据库表或 Alembic Migration，Phase 21 为零 Migration。
- Web Console、RBAC、Observability 和 Release Readiness 均建立在既有平台能力之上。

最终工程门禁：

| 门禁 | 结果 |
| --- | --- |
| Phase 21 专项测试 | 32 passed |
| Phase 21 应用覆盖率 | 99.05%（要求 ≥95%） |
| Phase 14/18/18.1/19/20/21 联合回归 | 104 passed |
| 完整后端回归 | 319 passed |
| Backend Ruff | All checks passed |
| Frontend ESLint | 通过 |
| Frontend TypeScript | 通过 |
| Frontend production build | 通过 |
| Docker Compose config | 通过 |
| Alembic heads | 单一 head：20260803_0018 |
| PostgreSQL 方言离线 Upgrade/Downgrade | 通过 |
| 真实 PostgreSQL 在线 Migration 往返 | 未执行：Docker Engine 未启动 |
| Prometheus/Grafana 容器 smoke test | 未执行：Docker Engine 未启动 |

结论：Phase 21 开发与可在当前环境完成的工程验证均已完成。真实 PostgreSQL 在线 Migration 往返和监控容器 smoke test 被明确保留为 Production Entry Gate，未误报为通过。

## 2. Scope and Boundaries

### 2.1 Delivered

- v1.0 Web Console 信息架构与页面。
- 平台级本地不可变 RBAC。
- 后端独立认证与授权边界。
- Approval Center、Audit Center、Access Control、只读 Settings。
- Dashboard、Plugin Inventory 等只读聚合 API。
- Prometheus Metrics、OpenTelemetry Trace、W3C Trace Context、Structured Logging。
- Liveness、Readiness、Metrics endpoints。
- Prometheus、Grafana、Alert Rules provisioning-as-code。
- Deployment、Upgrade、Rollback、Runbook、Compatibility Matrix、Version Policy。
- ADR-0043、ADR-0044、安全边界与架构权衡分析。
- Release Readiness Checklist。

### 2.2 Explicitly Not Delivered

- 新安全 Capability、Plugin、Provider 或真实响应集成。
- 动态 RBAC 管理 API、ABAC、多租户或完整 OIDC 登录页。
- 新 Platform Plane 或 Backstage Plugin Runtime。
- 新数据库表、物化读模型或 Phase 21 Migration。
- Phase 22 内容。

## 3. Architecture Benchmark

参考分析覆盖 Grafana、Prometheus、OpenTelemetry、Backstage 和 Keycloak，详见 `docs/github-reference-analysis-phase-21.md`。

### 3.1 Grafana

采纳 Dashboard/Panel 组合模型、数据源解耦和 provisioning-as-code。未引入 Grafana Plugin Runtime、多组织和第二套复杂权限系统。

### 3.2 Prometheus

采纳抓取式 `/metrics`、Counter/Gauge/Histogram、声明式 Alert Rule 和低基数 Label。HTTP 指标 Label 只允许 method、route template、status class，不使用 User ID、Incident ID 或 Trace ID。

### 3.3 OpenTelemetry

采纳 SDK Server Span、W3C `traceparent`、Context propagation 和 `service.name` Resource。Trace ID/Span ID 进入响应头与结构化日志；OTLP exporter 为可选配置。

### 3.4 Backstage

采纳统一门户、能力分区和聚合视图思想。未引入 Backstage Backend/Plugin API，避免新增 Platform Plane 或重复 CAP Plugin 系统。

### 3.5 Keycloak

采纳 User → Role → Permission 和后端权威授权原则。Phase 21 使用本地不可变目录，生产身份验证委托给可信 OIDC/企业网关。

## 4. Web Console

前端使用 React 18、TypeScript、Vite 和 Ant Design，页面包括：

- Dashboard
- Assets
- Knowledge
- Evidence
- Assessment
- Detection
- Incident
- Response
- Playbook
- Approval Center
- Audit Center
- Access Control
- Workers
- Sandbox
- Plugin
- Settings

Dashboard 展示：Asset、Incident、SecurityEvent、Finding 数量，Playbook 执行状态，Worker 健康和利用率，Plugin 状态，Response 成功率和 Notification 成功率。

Console 只通过 API 读取或调用既有领域能力，不直连数据库，不拥有新的业务状态。浏览器 API Client 不持有可信代理 Secret；本地 Compose 演示由 Nginx 服务端模板注入身份，生产必须替换为可信 OIDC/企业网关。

生产构建已完成，并拆分为应用入口、React、HTTP 和 Ant Design vendor chunk。Ant Design 独立 chunk 约 1 MB，仍产生 chunk-size 性能提示，但不影响构建正确性；后续可在独立评审后使用页面级 lazy loading 继续优化。

## 5. RBAC

### 5.1 Roles

| Role | Purpose |
| --- | --- |
| Administrator | 全平台管理权限 |
| SOC Analyst | 分析安全数据、执行 Assessment/Detection、准备受治理动作 |
| Incident Responder | 执行已批准 Response、Rollback 和 Playbook |
| Auditor | 读取平台状态、Settings、RBAC 和不可变 Audit |
| Read Only | 读取日常运营状态，不访问 Audit、Settings、RBAC |

### 5.2 Permission Model

权限采用 `resource.action`：

- Asset：`asset.read`、`asset.write`
- Knowledge：`knowledge.read`、`knowledge.write`
- Evidence：`evidence.read`
- Assessment：`assessment.read`、`assessment.execute`
- Detection：`detection.read`、`detection.execute`
- Incident：`incident.read`、`incident.write`
- Response：`response.read`、`response.plan`、`response.execute`、`response.rollback`
- Playbook：`playbook.read`、`playbook.write`、`playbook.execute`
- Worker：`worker.read`
- Sandbox：`sandbox.read`
- Plugin：`plugin.read`
- Approval：`approval.read`、`approval.decide`
- Notification：`notification.read`、`notification.send`
- Ticket：`ticket.read`、`ticket.write`
- Audit：`audit.read`
- Settings：`settings.read`
- RBAC：`rbac.read`
- 遗留控制面：`platform.manage`

### 5.3 Authentication and Authorization Boundary

- 除 `/health`、`/ready`、`/metrics` 和 API 文档外，其他路径默认需要可信身份。
- 身份必须同时包含用户头和可信代理认证头。
- 代理 Secret 使用常量时间比较。
- 缺少认证、伪造身份或未知用户返回 401。
- 权限不足返回 403。
- 未明确映射的遗留控制面操作要求 `platform.manage`，仅 Administrator 拥有。
- 高权限 Response、Approval、Playbook 路由在 Middleware 外继续使用显式 FastAPI dependency，形成纵深防御。
- 前端隐藏按钮不是安全边界。

## 6. Read-only Productization APIs

新增 API：

```text
GET /roles
GET /permissions
GET /users
GET /dashboard
GET /audit
GET /plugins
GET /approvals
GET /settings
```

- `/dashboard` 聚合既有 Asset、Incident、SecurityEvent、Finding、PlaybookExecution、Worker、Plugin、ResponseExecution 和 NotificationExecution。
- `/audit` 支持 operator、event type、resource、plugin、incident、worker、start/end time 和分页。
- `/plugins` 统一投影 Assessment、Detection、Response 和 Notification Plugin Inventory。
- `/approvals` 聚合 ResponsePlan 与最新 ResponseApproval。
- `/settings` 只返回脱敏投影，不返回数据库 URL、Redis URL、JWT Secret 或 Proxy Secret。

## 7. Observability

### 7.1 Prometheus Metrics

HTTP 指标：

- `cap_http_requests_total`
- `cap_http_request_duration_seconds`
- `cap_http_requests_in_progress`
- `cap_info`

业务指标：

- `cap_execution_count`
- `cap_execution_duration_seconds`
- `cap_worker_utilization_ratio`
- `cap_queue_depth`
- `cap_plugin_success_ratio`
- `cap_approval_latency_seconds`
- `cap_playbook_success_ratio`

Business Gauge 名称使用固定 allowlist，未知指标 fail closed。

### 7.2 Trace and Logging

- OpenTelemetry SDK Server Span。
- W3C `traceparent` 接收与传播。
- 响应头返回 `traceparent`、`X-Trace-ID`、`X-Span-ID`。
- Structured Log 记录 method、route template、status、duration、trace ID 和 span ID。
- 5xx Span 标记为 Error。
- OTLP endpoint 未配置时不阻塞应用。

### 7.3 Health and Monitoring

- `/health`：进程存活。
- `/ready`：数据库 `select(1)` readiness 检查。
- `/metrics`：Prometheus text exposition。
- Prometheus alerts：Backend Down、高 5xx 比例、高 p95 latency。
- Grafana Overview Dashboard：Request rate、5xx rate、p95 latency。

## 8. Release Engineering

- Docker Compose 增加 RBAC、Metrics、Tracing 和 OTel 配置。
- `observability` profile 包含 Prometheus 2.55.1 和 Grafana 11.3.1。
- Prometheus/Grafana 均使用 provisioning-as-code。
- Nginx 在服务端注入本地演示身份，不把 Secret 编译进浏览器。
- `.env.example` 包含 RBAC、Metrics、Tracing、OTel 和 Grafana 示例配置。
- `deployment/README.md` 包含部署、升级、回滚、Runbook、Compatibility Matrix、告警和 Known Limitations。
- Phase 21 版本策略保持后端 API 和既有领域数据模型兼容。

## 9. ADR and Security Analysis

- `docs/adr/ADR-0043-web-console-aggregate-view.md`：Console 使用 API-backed read projection，Phase 21 零 Migration。
- `docs/adr/ADR-0044-rbac-platform-capability.md`：RBAC 为平台级能力，默认拒绝，可信代理身份，浏览器不得持有代理 Secret。
- `docs/phase-21-security-boundary-and-tradeoffs.md`：记录安全边界、Safety Case 和架构权衡。

Safety Case：Phase 21 的 RBAC 只增加外层授权，不替换 Response/Playbook 原有 Approval、Policy、Verification、Evidence、Audit 和 Compensation 控制。

## 10. Verification Evidence

### 10.1 Phase 21 Tests and Coverage

```text
32 passed
TOTAL 423 statements, 4 missed
Coverage: 99.05%
Required: 95%
```

覆盖模块：

- `app.auth.rbac`
- `app.api.routes.productization`
- `app.services.productization`
- `app.observability`
- `app.middleware.authorization`
- `app.middleware.observability`

### 10.2 Regression

```text
Phase 14/18/18.1/19/20/21: 104 passed
Full backend: 319 passed
```

### 10.3 Static and Frontend Gates

```text
Backend Ruff: All checks passed
Frontend ESLint: passed
Frontend TypeScript: passed
Vite production build: passed
3045 modules transformed
```

### 10.4 Database and Deployment Gates

```text
Docker Compose config: passed
Alembic single head: 20260803_0018
PostgreSQL dialect offline upgrade: passed
PostgreSQL dialect offline downgrade: passed
```

## 11. Known Limitations

- 本地 User/Role/Permission 为不可变目录，无在线用户管理写 API。
- Compose `CAP_DEFAULT_USER` 只用于单用户本地演示，不能视为生产多用户认证。
- Phase 21 不提供多租户、ABAC、Keycloak Adapter 或完整 OIDC 登录页。
- OTLP exporter 未配置时没有集中式 Trace 存储。
- Dashboard 是当前状态聚合，不替代 Prometheus 历史趋势。
- 直接聚合在大规模数据下可能需要缓存或物化视图，但本阶段不新增表。
- Ant Design vendor chunk 较大，后续可用页面级懒加载优化。

## 12. Production Entry Gates

当前 Docker Engine 未启动：

```text
failed to connect to dockerDesktopLinuxEngine
```

因此以下事项必须在 Staging/Production 发布前完成：

1. 启动可用 Docker Engine 或连接真实 PostgreSQL 16 环境。
2. 备份数据库。
3. 执行真实 `alembic upgrade head`。
4. 验证 `alembic current` 为 `20260803_0018`。
5. 执行 API smoke test 和关键领域联合回归。
6. 在可回滚环境验证 downgrade/restore 流程。
7. 启动 `observability` profile，验证 Prometheus target、Alert Rules 和 Grafana Dashboard。
8. 替换全部默认 Secret，由生产 OIDC/企业网关覆盖客户端身份头。

## 13. Final Decision

Phase 21 Definition of Done 已满足：Web Console、RBAC、Observability、Release Readiness、文档、专项测试、覆盖率和完整回归均完成。

Phase 21 在此停止，等待 Architect Review。未进入 Phase 22。
