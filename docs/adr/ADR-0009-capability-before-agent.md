# ADR-0009: Workflow 必须先声明 Capability，再解析 Agent

- 状态：Accepted
- 日期：2026-07-30

## 背景

若 Workflow 直接保存或调用 Agent，流程定义会与具体部署实例、版本、健康状态和权限绑定，破坏 Plugin First、Capability Registry 与 Dispatcher 的边界。

## 决策

AgentNode 只声明一个稳定 Capability。执行链固定为：

```text
WorkflowRuntime
  -> AgentNodeHandler
  -> TaskService.execute_capability
  -> TaskDispatcher
  -> CapabilityRepository
  -> Agent eligibility + SchedulingStrategy
  -> RuntimeService
  -> Agent
```

Workflow Runtime 和 Node Handler 均不得查询、持有或实例化 Agent。Permission、Agent status、heartbeat、targeting 与 scheduling 继续由 Dispatcher 统一治理。

## 原因

- Capability 是业务意图；Agent 是动态执行提供者；
- Agent 可热更新、扩缩容或下线，不应导致 Workflow Definition 变化；
- Dispatcher 已拥有权限、健康与调度策略，Workflow 不应复制这些规则；
- 可保证每次执行选择过程可审计。

## 参考

- OpenAI Agents SDK：参考 Runner/Context/Tool/Handoff 的边界分离；不采用模型决定 Handoff。
- LangGraph：参考 Node 与运行上下文分离；不让节点隐式选择不可审计资源。
- Shuffle：参考 Workflow Action 通过应用能力扩展；不把某个运行实例固化到流程定义。

## 后果

- 优点：Workflow 可移植，Agent 可动态替换，权限治理集中。
- 限制：若同一 Capability 有多个语义版本，当前字符串名称不足以表达兼容范围。
- 后续：Capability Version、QoS、成本、风险级别和亲和性应进入 Planner/Dispatcher 策略，而不是写入 Workflow Runtime。
