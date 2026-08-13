# ADR-0003：为什么采用 Version History

- 状态：Accepted
- 日期：2026-07-29
- 决策范围：Agent / Tool 定义治理

## 背景

Agent 与 Tool 的 manifest 会随权限、入口点、资源限制和配置模式变化。仅覆盖主表会丢失历史，导致无法解释任务使用了哪一版定义，也无法满足审计和回滚准备要求。

## 决策

稳定身份与版本历史分离：

- `agents`、`tools` 保存稳定身份和当前版本；
- `agent_versions`、`tool_versions` 追加不可变 manifest；
- `(resource_id, version)` 唯一；
- Version API 按 `created_at DESC, id DESC` 提供确定性分页；
- 当前版本变化发布事件并进入审计日志。

## 结果

可以追踪定义演进、支持审计取证，并为未来回滚和任务版本固定提供数据基础。代价是增加存储和一致性规则；Phase 1.1 不实现回滚功能，只提供历史记录与查询接口。
