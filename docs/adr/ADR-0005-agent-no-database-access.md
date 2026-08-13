# ADR-0005: Agent 不直接访问数据库

- **状态**：Accepted
- **背景**：允许插件 Agent 直接访问 ORM 或 Repository 会绕过权限、审计、事务边界，并使运行形态难以替换。
- **决策**：RuntimeContext 仅提供受控能力：事件发布、日志、配置、Tool Adapter、EvidenceService、ReportService。它不提供数据库会话、Repository 或 Dispatcher。
- **后果**：Agent 代码更可移植、审计更完整、权限收敛；新增平台能力必须先设计成明确的 Context 接口。
