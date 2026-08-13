# ADR-0011: Asset Graph 采用关系数据库模型

- 状态：Accepted
- 日期：2026-07-30

## 背景

Phase 4 需要表达 Domain `resolves_to` IP、Website `references` Domain、Website `hosted_on` Host、Container `runs_on` Host、Application `deployed_in` Container 等有向关系，同时保证资产、关系、Evidence 和 Report 关联的一致性。

## 决策

以 PostgreSQL 作为事务性 Source of Truth：

- `assets` 保存规范身份和治理字段；
- `asset_relations` 保存有向类型边及边属性；
- `asset_tags` 保存规范化 Tag；
- `asset_evidence` 和 `asset_reports` 保存 provenance；
- `tasks.asset_id` 与 `workflow_instances.asset_id` 传播资产上下文。

关系使用 `(source_asset_id, target_asset_id, relation_type)` 唯一约束。Asset 身份使用 `(asset_type, canonical_value)` 唯一约束。关键外键使用 `RESTRICT`，防止资产被物理删除后留下孤立上下文。

## 原因

- 当前平台已经使用 SQLAlchemy、Alembic 与 PostgreSQL；
- 单跳关系、按属性搜索和来源关联是 Phase 4 的主要查询；
- 关系数据库能同时提供唯一约束、外键、事务和可逆迁移；
- Evidence/Report/Task/Workflow 已位于同一事务边界；
- 不增加新的基础设施和双写一致性风险。

## 关系语义

关系是有方向且有类型的，反向关系不能隐式推断为同一事实。当前注册类型为：

- `resolves_to`
- `hosted_on`
- `runs_on`
- `deployed_in`
- `references`
- `related_to`

自环被 Service 拒绝；重复边由 Service 幂等检查和数据库唯一约束共同保护。关系属性用于保存 discovery 来源等非身份数据。

## 后果

### 优点

- 与现有事务、审计和迁移体系一致；
- 数据完整性由数据库约束兜底；
- SQL 查询和分页适合当前管理 API；
- 可通过 Adapter/Repository 保持上层不依赖具体存储。

### 缺点

- 多跳、最短路径和大规模邻域分析需要递归 SQL，复杂度随关系规模增长；
- 关系类型目前由 Registry 与字符串列共同约束，数据库没有类型表；
- 边的有效期、来源、置信度和版本尚未成为一等字段；
- 并发“先查后写”仍依赖唯一约束处理竞态。

## 图数据库演进方案

1. PostgreSQL 始终保存 Asset 主记录、治理字段和事务写入；
2. 通过 Outbox/CDC 发布 Asset 与 AssetRelation 变更；
3. Graph Projection Worker 幂等写入图数据库；
4. 图数据库只服务多跳遍历、路径分析和拓扑可视化；
5. API 通过 `AssetGraphRepository` 接口选择关系查询或图查询；
6. 投影落后或不可用时，关键 CRUD 和单跳查询仍由 PostgreSQL 提供；
7. 禁止 Agent 直接写图数据库，避免出现第二个 Source of Truth。

## Breaking Change 规则

新增关系类型属于向后兼容扩展；重命名关系类型、改变方向语义或修改 canonical identity 规则属于 Breaking Change，必须通过迁移和兼容读取策略发布。
