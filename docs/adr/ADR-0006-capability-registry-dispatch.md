# ADR-0006: Capability Registry 取代直接 Agent 调度

- 状态：Accepted
- 日期：2026-07-29
- 阶段：Phase 2.1

## 背景

Phase 1 Dispatcher 直接从 Agent Registry 按状态、心跳和权限筛选 Agent。该方式要求任务或编排器知道具体 Agent 身份，无法支持 Agent 动态替换、同能力多实现和能力治理。

## 决策

任务增加 `required_capabilities`。Agent Manifest 声明 `capabilities`，注册时同步到 `Capability` 与 `AgentCapability`。Dispatcher 对声明能力的任务先解析同时具备全部能力的 Agent ID，再应用目标 Agent、状态、心跳、权限和 SchedulingStrategy。未声明能力的旧任务保留 Phase 1 行为。

```text
Task.required_capabilities
  -> Capability Registry
  -> AgentCapability intersection
  -> eligible Agent IDs
  -> status / heartbeat / permissions
  -> SchedulingStrategy
```

## 后果

- 编排器面向能力而非实现；Agent 可以动态注册和替换。
- 权限仍是授权边界，Capability 仅表示可发现的功能声明。
- 多能力查询使用 `GROUP BY/HAVING count(distinct capability)`，要求 Agent 同时满足全部能力。
- Capability 需要独立启停、风险等级与审计治理。

## 未采用方案

- 直接按 Agent 名称路由：耦合具体实现，无法形成插件生态。
- 仅使用 Manifest JSON 查询：数据库可移植性和索引治理较差。
- 使用 LLM 选择 Agent：不可确定、难审计，不适合作为控制平面调度基础。
