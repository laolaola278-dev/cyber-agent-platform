# ADR-0004: Runtime 与 Dispatcher 解耦

- **状态**：Accepted
- **背景**：Dispatcher 的职责是任务选择、状态机与调度审计；它不应感知 Agent 模块、生命周期或外部工具。
- **决策**：Dispatcher 仅调用通过 DI 注入的 RuntimeService；RuntimeManager 负责 Agent 的加载、启动、执行、健康检查、重载、停止与销毁。
- **后果**：可替换运行形态（进程内、容器或远程 Worker）而不改调度逻辑；增加了明确的 Runtime API 和持久化模型。
