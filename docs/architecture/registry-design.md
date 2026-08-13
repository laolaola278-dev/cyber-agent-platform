# Registry 设计

## 边界

Registry 管理 Agent 与 Tool 的稳定身份、当前状态和版本清单，不负责加载 Python 类、启动容器或执行工具。

## Agent Registry

- `Agent`：稳定身份、当前版本、权限、Tool 引用、运行要求、网络/资源/审批策略、健康状态。
- `AgentVersion`：版本 Manifest 的追加记录。
- `AgentHeartbeat`：运行实例健康信息的追加记录。
- `AgentRegistryService`：注册、版本更新、查询、修改、删除和 heartbeat 事务边界。

## Tool Registry

- `Tool`：稳定身份、当前版本、类型、所需权限、配置 Schema、运行要求和启用状态。
- `ToolVersion`：Tool Adapter Manifest 的追加记录。
- `ToolRegistryService`：注册、版本更新、查询和禁用。

## 扩展原则

- 服务依赖 Repository 与 EventBus 接口，不依赖具体 Agent/Tool 实现。
- 新 Agent/Tool 不需要修改 Registry 分支逻辑，只需提交符合 Schema 的 Manifest。
- Phase 1 使用 JSON 保存策略和 Manifest 快照；后续可由 Architect 决定是否规范化策略表。
