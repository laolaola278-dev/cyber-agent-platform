# ADR-0044: RBAC 是平台级能力

- 状态：Accepted
- 日期：2026-08-04

## Context

Approval、Response、Playbook、Worker 和 Plugin 跨越多个领域。若每个领域独立解释角色，权限将产生漂移；若只依赖前端隐藏操作，则可被直接 API 调用绕过。

## Decision

建立平台级 `resource.action` Permission、Role 和 User contract。Phase 21 使用本地不可变目录，至少提供 Administrator、SOC Analyst、Incident Responder、Auditor、Read Only。Authorization Middleware 对非公开路径默认拒绝；高权限路由再以 FastAPI dependency 作显式防线。

身份由可信反向代理注入。必须同时验证身份头与代理共享密钥；浏览器不得持有共享密钥。生产环境由 OIDC/企业网关替换本地默认用户映射。

## Consequences

- 后端授权独立于 UI。
- 未映射的遗留操作需要 `platform.manage`，仅 Administrator 拥有。
- 本地目录不可在线修改，降低 Phase 21 迁移和管理面风险。
- 多租户、ABAC、动态 Role 管理推迟到后续评审。
