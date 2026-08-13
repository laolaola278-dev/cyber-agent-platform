# ADR-0010: 平台采用统一 Asset Center

- 状态：Accepted
- 日期：2026-07-30

## 背景

Agent、Workflow、Task、Evidence、Report、Assessment 与 Detection 都需要引用目标对象。若每个 Agent 分别维护域名、IP、主机或应用清单，相同对象会产生不同 ID、命名、标签和风险值，平台无法可靠去重、审计或聚合结果。

## 决策

CAP 建立统一 Asset Center 作为资产身份和治理元数据的唯一可信来源。Asset 使用稳定 UUID，并以 `(asset_type, canonical_value)` 作为数据库唯一身份约束。

支持的类型包括 Domain、IP、Host、Website、Application、Container、CloudResource、Repository、Document、User（预留）和 Agent（引用）。Agent、工具和 Workflow 只能通过 Asset API/Service 解析或关联资产，不得拥有独立的权威资产仓库。

统一执行链为：

```text
Workflow -> Asset -> Dispatcher -> Capability -> Runtime -> Agent
Agent -> Evidence -> Asset -> Report
```

## Canonical Identity

- Domain/Host：去除尾点并大小写归一；
- IP：使用标准 IPv4/IPv6 表示；
- Website/Repository：要求绝对 URL，规范化 scheme、hostname、默认端口和 fragment；
- 其他类型：去除首尾空白并进行大小写归一；
- URL Discovery：生成 Website、Domain、IP，并建立显式关系。

## 生命周期与治理

- Asset 不允许物理删除，DELETE API 只执行 Soft Delete；
- 默认查询排除已删除记录；
- Discovery 再次发现相同身份时可恢复软删除记录；
- Owner、Business Unit、Environment、Criticality、Risk、Tag 与 Capability 由统一模型治理；
- 创建、更新、软删除、关系、发现和关联操作发布审计事件。

## 原因

- 平台范围内保持同一对象只有一个规范身份；
- Evidence 与 Report 可跨 Agent 聚合到同一 Asset；
- Workflow 和 Task 可携带稳定目标上下文；
- 权限、风险、Owner 和环境信息不再被插件重复实现；
- 统一 API、唯一约束和审计日志能够提供治理边界。

## 后果

### 优点

- 去重与关联规则集中；
- Agent 可替换而不改变资产身份；
- 搜索、审计、风险汇总和报告生成具有一致语义；
- 未来可从统一关系数据生成图投影。

### 缺点

- Asset Center 成为关键控制面依赖；
- Canonicalization 规则变更可能产生 Breaking Change；
- 不同数据源冲突需要后续引入来源优先级和合并策略；
- 初期关系数据库不适合无限深实时图遍历。

## 被否决方案

### 每个 Agent 自己维护资产

否决。该方案会造成身份漂移、重复数据、权限绕过和不可聚合的 Evidence。

### 直接使用某个工具的资产模型

否决。Wazuh、NetBox、OpenCTI 等模型服务于不同业务边界，会把 CAP 核心与单一工具耦合。

### Phase 4 直接采用图数据库

否决。当前关系规模、查询与事务要求可由 PostgreSQL 满足；直接引入图数据库会增加双系统一致性、运维和迁移复杂度。

## 后续

- 增加来源、置信度、观察时间和字段级合并策略；
- 增加资产合并/拆分与别名能力；
- 增加基于 Outbox/CDC 的图投影；
- 在不改变 Asset UUID 的前提下演进 Canonicalization 版本。
