# ADR-0041：Playbook 采用声明式、Capability-only 编排

- 状态：Accepted for Phase 20
- 日期：2026-08-04

## 背景

CAP 已分别拥有 Assessment、Detection、Incident、Response、Notification 与 Ticket 领域能力。Phase 20 的目标是编排既有能力，而不是在 Playbook 中复制业务逻辑、开发新 Plugin，或提供任意代码执行环境。SOAR DSL 一旦允许 Python、Shell、动态属性访问或任意函数调用，就会形成绕过领域 Policy、Approval、Audit 与 Sandbox 的第二执行平面。

## 决策

Playbook 使用版本化 YAML DSL v1，并在创建时通过 `yaml.safe_load` 与严格 Pydantic Typed Model 完整校验。未知字段、未知枚举、保留 Trigger、保留 Node、越界 Retry/Timeout/Parallel、未在 allowlist 中的 Capability 均 fail closed。

Node 只能映射到已有领域 Service：Assessment、Detection、Response、Notification、Ticket；Condition 使用受限 AST 解释器，只允许常量、名称、字典/序列下标、列表/元组/字典、布尔与比较操作。禁止 `eval`、`exec`、函数调用、属性访问、动态导入、Shell 和任意代码生成。

Phase 20 只启用 `manual` 与 `incident.created` Trigger，以及 Condition、Approval、Assessment、Detection、Response、Notification、Ticket Node。Delay、Parallel 和其余 Trigger 仅保留枚举，拒绝执行。`max_parallel` 固定为 1。

## 后果

- Playbook 不能绕过既有领域 Service、Capability Policy、Response Approval、Evidence 与 Audit。
- DSL 可静态验证、规范化哈希、不可变版本化与安全审查。
- 表达能力低于通用工作流/脚本平台；Phase 20 不支持用户脚本、循环、并行、延迟和任意连接器。
- 新 Trigger、Node 或 DSL 版本必须经过独立 ADR、兼容性与威胁模型审查，不能通过配置直接启用。

## 参考

该决策吸收 Shuffle 的 Workflow/Action 分离、StackStorm Orquesta 的声明式 Workflow、n8n 的 Node/Execution 可观测性，同时刻意不采用其通用脚本扩展能力，以保持 CAP 高权限响应链的最小执行面。
