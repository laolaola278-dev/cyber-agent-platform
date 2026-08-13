# ADR-0043: Web Console 使用聚合视图

- 状态：Accepted
- 日期：2026-08-04

## Context

v1.0 需要统一呈现多个已通过评审的领域能力，但 Phase 21 禁止新增安全能力和 Platform Plane，也不得让前端直连数据库。

## Decision

Web Console 仅调用 FastAPI。`/dashboard`、`/audit`、`/plugins`、`/approvals`、`/settings` 是已有模型与配置的只读投影，不拥有业务状态，不写入领域表。领域详情继续调用各自既有 API。

## Consequences

- 避免重复业务模型和跨域写入。
- Dashboard 可独立优化，但最终一致性受单次查询窗口影响。
- Console 不是授权边界；权限必须由后端执行。
- Phase 21 零 Migration。
