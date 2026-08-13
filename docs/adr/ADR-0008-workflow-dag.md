# ADR-0008: Workflow 采用持久化 DAG

- 状态：Accepted
- 日期：2026-07-30

## 背景

CAP 需要以声明式 Workflow 协调 Capability、Dispatcher、Runtime 与多个 Agent，并支持重试、超时、取消和断点恢复。Workflow 不能把具体 Agent 名称写入定义，也不能依赖 LLM 生成执行控制流。

## 决策

Phase 3 Workflow Definition 使用有向无环图（DAG）：

- YAML 只描述 Node、Edge、Condition、Retry 与 Timeout；
- Definition 与 Runtime 分离，创建时完成结构校验；
- 每个 Node 结束后将 Step 和 Execution History 持久化；
- Runtime 根据已完成 Step 计算下一就绪 Node；
- AgentNode 只提交 Capability，由 Dispatcher 解析 Agent；
- Node Handler 通过 NodeRegistry 插件化；
- Phase 3 串行执行就绪节点，模型允许后续扩展同层并行执行。

## 原因

DAG 能提供确定性拓扑、可验证依赖、无环终止保证和清晰的 checkpoint 边界。相较于硬编码顺序或 LLM 路由，DAG 更易审计、恢复和进行权限分析。

## 参考

- LangGraph：采用图定义/运行时分离、节点、条件边、checkpoint 思路；不采用循环图和 LLM Memory。
- Temporal：采用持久化执行历史、Activity 式节点边界和重试思想；不复制 event replay runtime。
- Argo Workflows：采用声明式 DAG、Task、Retry Strategy 思路；不绑定 Kubernetes CRD。
- Shuffle：采用安全自动化 Workflow 与 App/Action 扩展思想；不复制其完整 SOAR 产品。

## 后果

- 优点：确定、可审计、可恢复、插件化、适合 Capability-first 调度。
- 限制：Phase 3 不支持循环、子工作流、动态 fan-out/fan-in 和并行调度。
- 风险：Definition 版本不可变策略目前由 name/version 唯一约束保证，尚未提供签名与发布审批。
