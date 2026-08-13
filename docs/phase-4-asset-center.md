# Phase 4 Asset & Inventory Center

## 定位

Asset Center 是 CAP 内部唯一可信的资产身份与治理中心。它不替代 CMDB、DCIM、CTI、SIEM 或漏洞管理平台，而是为 Agent、Capability、Workflow、Task、Evidence 与 Report 提供统一引用对象。

## 领域边界

```text
External Source / Tool
        |
        v
AssetResolver / Tool Adapter
        |
        v
AssetService -> AssetRepository -> PostgreSQL
        |              |
        |              +-> Asset / Relation / Tag / Provenance
        v
EventPublisher -> AuditSubscriber -> AuditLog
```

- `AssetRegistry`：控制允许的 AssetType 与 AssetRelationType；
- `AssetResolver`：只做 URL 规范化和 DNS 解析，不执行端口探测、认证绕过或漏洞扫描；
- `AssetService`：拥有身份、CRUD、Soft Delete、关系、发现、Evidence/Report 关联与审计；
- `AssetRepository`：提供软删除感知查询、分页、搜索和关联读取；
- API/ORM 不承载业务策略。

## 统一身份

数据库通过 `(asset_type, canonical_value)` 阻止重复资产。原始 `value` 用于保留输入表达，`canonical_value` 用于身份判断。

| 类型 | 规范化规则 |
|---|---|
| Domain / Host | trim、移除尾点、casefold |
| IP | `ipaddress.ip_address` 标准表示 |
| Website / Repository | 绝对 URL、scheme/host 小写、移除默认端口与 fragment、空 path 变为 `/` |
| 其他 | trim、casefold |

Discovery 链：

```text
URL
  -> Website
  -> references -> Domain
  -> resolves_to -> IP (0..n)
```

DNS Resolver 通过 Protocol 注入；系统实现在线程执行器中调用 `getaddrinfo`，避免阻塞异步事件循环。IPv4/IPv6 结果先规范化、去重并确定性排序。

## 资产关系

`AssetRelation` 是有向类型边，边可保存 `properties`。当前关系包括：

- Domain `resolves_to` IP
- Website `hosted_on` Host
- Container `runs_on` Host
- Application `deployed_in` Container
- Website `references` Domain
- 通用 `related_to`

Service 拒绝自环，并对重复关系返回既有记录；数据库唯一约束作为并发竞态的最终保护。

## 治理字段

Asset 提供：Tag、Owner、Business Unit、Environment、Criticality、Risk、Capabilities 与 Properties。查询支持名称/值、类型、Tag、Owner、Risk、Environment 和精确 Capability 成员。

Soft Delete 通过 `deleted_at`、`deleted_by` 表达。普通 GET/Search 不返回已删除 Asset；Discovery 重新观察到同一 canonical identity 时恢复原 UUID，从而保持历史关联稳定。

## Workflow 集成

```text
WorkflowInstance.asset_id
        |
        v
WorkflowRuntime / NodeContext
        |
        v
TaskService.execute_capability
        |
        v
Task.asset_id -> Dispatcher -> Runtime -> Agent
                                  |
                                  v
                    EvidenceService(asset_id)
                                  |
                                  v
                     AssetEvidence -> Report
                                          |
                                          v
                                    AssetReport
```

Phase 4 保持 `Task.asset_id` 和 `WorkflowInstance.asset_id` 可空，以兼容历史数据与无固定目标的通用任务；一旦提供 `asset_id`，Service 必须验证资产存在且未软删除，并完整传播上下文。

## Audit

以下写操作发布平台事件并由 AuditSubscriber 持久化：

- AssetCreated
- AssetUpdated
- AssetSoftDeleted
- AssetRelationCreated
- AssetDiscovered
- AssetEvidenceLinked
- AssetReportLinked

事件至少包含 trace ID、aggregate ID、actor、resource 和操作 payload。API 验证错误会先转换为 JSON-safe 结构再写审计列。

## API

| Method | Path | 用途 |
|---|---|---|
| GET | `/assets` | 分页和多条件搜索 |
| POST | `/assets` | 创建规范资产 |
| POST | `/assets/discover` | URL → Website/Domain/IP |
| GET | `/assets/{id}` | 获取活动资产 |
| PUT | `/assets/{id}` | 更新治理字段或身份值 |
| DELETE | `/assets/{id}` | Soft Delete，返回 204 |
| GET | `/assets/{id}/relations` | 查询入边与出边 |
| POST | `/assets/{id}/relations` | 创建有向关系 |
| GET | `/assets/{id}/evidence` | 查询关联 Evidence |
| GET | `/assets/{id}/reports` | 查询关联 Report |

### 创建示例

```json
{
  "asset_type": "DOMAIN",
  "name": "Example Domain",
  "value": "Example.COM.",
  "owner": "security-team",
  "business_unit": "platform",
  "environment": "production",
  "criticality": "high",
  "risk": "medium",
  "tags": ["external", "production"],
  "capabilities": ["crawl.html", "dns.resolve"],
  "properties": {}
}
```

### 响应核心字段

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "asset_type": "DOMAIN",
  "name": "Example Domain",
  "value": "Example.COM.",
  "canonical_value": "example.com",
  "tags": ["external", "production"],
  "deleted_at": null
}
```

## 部署与配置

- Asset Center 与现有 FastAPI 后端同进程部署；
- PostgreSQL 是生产 Source of Truth；SQLite 仅用于隔离自动化测试；
- Alembic revision `20260730_0007` 必须在应用启动前执行；
- DNS 使用系统解析器，生产环境应由网络策略限制可解析范围和出口；
- 不新增图数据库、消息中间件或独立微服务；
- 未来拆分时保持 `AssetService`、Repository 和 EventPublisher 接口边界。

## Architecture Trade-off Analysis

### 为什么采用统一 Asset

平台需要跨 Agent 聚合同一目标的执行历史、Evidence、Report 与风险信息。统一 UUID 和 canonical identity 能让调度、审计与报告共享稳定上下文。

### 为什么不允许 Agent 自己维护资产

Agent 是可动态注册和替换的执行插件，不是治理控制面。私有资产库会导致重复身份、字段冲突、权限绕过、审计断裂和跨 Agent 结果不可关联。

### 优点

- 统一去重、搜索、治理和审计；
- Agent 生命周期与资产生命周期解耦；
- Evidence/Report provenance 清晰；
- 数据库约束提供最终一致性保护；
- 可渐进构建资产图。

### 缺点与风险

- Asset Center 可用性会影响所有 Asset-aware 执行；
- Canonicalization 错误会放大为平台级身份问题；
- 多源字段冲突和并发合并策略尚未完整实现；
- JSON Capability 搜索适合当前规模，未来可能需要规范化关联表；
- Service 的部分幂等操作采用先查后写，并发下仍需捕获唯一约束冲突并转换为幂等成功。

### Graph Database 演进

关系数据库继续负责事务写入和 Source of Truth。通过 Outbox/CDC 将 Asset/Relation 事件投影到图数据库，图侧只负责多跳查询、路径分析和拓扑展示。所有写操作仍经 AssetService，禁止 Agent 或图数据库成为第二权威源。

## Technical Debt

- Discovery 已对自动创建/恢复 Asset、自动创建关系和聚合发现分别发布审计事件；自动 Evidence/Report 关联也发布 Asset 关联事件；
- Evidence/Report 关联的并发幂等仍依赖唯一约束，尚未实现方言一致的冲突吸收；
- Capability 仍保存为 JSON 数组，规模增长后应迁移为规范化关联；
- 缺少字段来源、置信度、首次/最近观察时间、别名、合并/拆分能力；
- 缺少授权范围与资产级 Permission Policy。
