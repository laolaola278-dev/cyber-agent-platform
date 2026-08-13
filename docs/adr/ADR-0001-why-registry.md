# ADR-0001：为什么采用 Registry

- 状态：Accepted
- 日期：2026-07-29
- 决策范围：Phase 1 / Phase 1.1

## 背景

CAP 需要接入由不同团队维护、具有不同版本、权限和运行约束的专业 Agent 与 Tool Adapter。若编排器直接持有实现清单，平台会与插件实现耦合，无法进行统一发现、版本治理、健康检查、权限校验和审计。

## 决策

采用 Agent Registry 与 Tool Registry 作为控制面的稳定身份与元数据来源：

- 主表保存稳定身份及当前有效版本；
- 版本表保存不可变 manifest 历史；
- Runtime 与 Orchestrator 只依赖 Registry 暴露的查询契约；
- Registry 变更发布平台事件，由 AuditSubscriber 统一记录。

## 结果

优点：实现动态发现、统一治理、版本追踪和编排解耦。代价：需要维护身份与版本一致性，并在后续引入持久事件总线时处理跨事务一致性。Phase 1.1 使用数据库事务内的进程内事件订阅，Outbox 留作后续经 Architect 批准的演进项。
