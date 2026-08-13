# CAP v1 文档索引

> 版本基线：`1.0.0-rc1`。本文是文档入口，不是新的 API 合同。

## 先读什么

| 读者 | 推荐顺序 | 目标 |
| --- | --- | --- |
| Architect / Security | [RC 发布说明](releases/v1.0.0-rc1.md) → [已知问题](known-issues.md) → [生产检查清单](deployment/production-checklist.md) → [API 冻结策略](api-freeze-v1.md) | 判断 RC 是否具备受控评审与生产准入条件 |
| 平台开发者 | [架构](architecture.md) → [API 指南](api-guide.md) → [SDK 指南](sdk-guide.md) → [插件开发指南](plugin-development-guide.md) | 遵守平台边界、稳定接口和扩展契约 |
| 集成方 | [API 指南](api-guide.md) → [API 冻结策略](api-freeze-v1.md) → [FAQ](faq.md) | 接入认证、错误、相关性和版本兼容约束 |
| 运维 / SRE | [部署索引](../deployment/README.md) → [生产检查清单](deployment/production-checklist.md) → [运维指南](operations-guide.md) → [Runbook](runbook.md) | 部署、观测、升级、回滚和故障处置 |
| 评估 / 本地试用 | [单节点部署](deployment/single-node.md) → [Docker Compose](deployment/docker-compose.md) | 在非生产场景启动和验证 CAP |

## 产品与架构

- [项目 README](../README.md)：版本状态、能力范围、快速启动和质量门禁。
- [架构说明](architecture.md)：Platform、Domain、Execution、Presentation 四个平面及安全边界。
- [路线图](roadmap.md)：1.0 finalization 的剩余生产认证门禁，以及 1.0.0 之后的版本政策。
- [ADR 集合](adr/)：不可由单篇指南替代的架构决策记录。

## 稳定接口与扩展

- [API 指南](api-guide.md)：OpenAPI 入口、操作分组、认证、错误和追踪相关性。
- [API Freeze v1](api-freeze-v1.md)：冻结范围、RC 期间允许的修复、破坏性变更审查流程。
- [Python SDK 指南](sdk-guide.md)：`sdk/python` 独立包、Agent/ToolAdapter 使用边界和兼容策略。
- [Plugin Development Guide](plugin-development-guide.md)：Manifest、Provider、Sandbox、Secret Provider、安全失败和测试要求。
- [FAQ](faq.md)：生产认证、外部依赖、密钥、API 文档、插件边界等常见问题。

## 部署与运行

- [部署索引](../deployment/README.md)：Compose、Helm、Prometheus、Grafana 与运行指南入口。
- [单节点部署](deployment/single-node.md)：评估、集成和受控低风险 staging；不等同于 HA 生产认证。
- [Docker Compose](deployment/docker-compose.md)：健康门禁、配置、生命周期和验证。
- [Production Checklist](deployment/production-checklist.md)：发布前必须逐项留痕的治理、安全、数据恢复和部署门禁。
- [Upgrade](deployment/upgrade.md)：备份、staging、Chart/image 固定、迁移和升级后验证。
- [Rollback](deployment/rollback.md)：冻结高影响操作、Helm 回滚、兼容性判断和证据保留。
- [Backup & Restore](deployment/backup-restore.md)：备份、恢复演练和 RPO/RTO 证据要求。
- [Operations Guide](operations-guide.md)：日常检查、变更控制和安全运营。
- [Runbook](runbook.md)：Backend、Readiness、认证、队列/Worker、延迟和发布故障处置。

## 认证状态与证据边界

`1.0.0-rc1` 适合受控 staging 和 Architect Review，当前不是无条件生产认证。生产准入至少需要补齐或正式接受以下证据：

1. PostgreSQL/Redis 真实目标环境的连接池、锁、迁移、重启恢复与容量验证。
2. Kubernetes/Helm 安装、滚动升级、回滚、探针、PDB 和 disruption 验证。
3. 外部负载工具与目标容量下的 API P95/P99 验证；Phase 22 的高并发风险必须关闭或经授权接受。
4. 镜像、SBOM、Trivy、后端依赖和 secrets/misconfiguration 扫描证据。
5. 精确发布版本、镜像/Chart digest、备份恢复、RPO/RTO，以及 Architect、Security、Operations、License 审批记录。

详见 [RC 发布说明](releases/v1.0.0-rc1.md)、[已知问题](known-issues.md) 和 [Production Checklist](deployment/production-checklist.md)。未执行的验证不得写成“已通过”；静态 Helm/Compose 校验也不等于目标环境认证。

## 版本与更新规则

- `1.0.0-rc1` 的已发布资产不可原地覆盖；任何修正应生成新的 RC。
- RC 阶段只接受不改变公共语义的 bug 修复、测试、文档、打包、部署和认证工作。
- `1.0.0` 之后，兼容 bug 修复为 PATCH，兼容公共新增为 MINOR，不兼容变更为 MAJOR。
- API、SDK、Plugin Manifest、Provider 和 Playbook DSL 的兼容性以 [API Freeze v1](api-freeze-v1.md) 为准。
