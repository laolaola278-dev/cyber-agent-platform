# Phase 20 Development Report

**项目：** Cyber Agent Platform（CAP）  
**阶段：** Phase 20 — SOAR Playbook Engine  
**状态：** **开发完成，等待 Architect Review；不得进入 Phase 21**  
**边界结论：** 仅编排既有领域能力；无新 Plugin、无新安全能力、无任意代码执行、无业务域直接写入。

---

## 1. Acceptance Checklist

| 验收项 | 状态 | 实现/证据 |
|---|---:|---|
| Playbook Engine 六组件 | PASS | Service、Registry、Runtime、Planner、Executor、Policy |
| 数据模型 | PASS | Playbook、Version、Trigger、Execution、StepExecution |
| YAML DSL v1 | PASS | safe_load、strict typed model、checksum、immutable version |
| Trigger | PASS | Manual、Incident Created |
| Reserved Trigger | PASS | Schedule/Finding/SecurityEvent/Approval/Response/Notification fail closed |
| Node | PASS | Condition、Approval、Assessment、Detection、Response、Notification、Ticket |
| Delay / Parallel | RESERVED | DSL 拒绝；`max_parallel=1` |
| 任意代码执行 | PASS | 无 eval/exec/call/attribute/dynamic import/shell |
| Retry / Failure / Skip | PASS | 有界重试与持久 Step 状态 |
| Timeout | PASS | Step、审批等待和 Playbook deadline |
| Approval waiting/resume | PASS | 同一 Execution、同一 Version；runner/approver distinct |
| Execution History | PASS | Execution + Step checkpoint/input/output/error/attempt |
| Compensation | PASS | Response rollback、Notification ignore、Ticket close，逆序执行 |
| Idempotency | PASS | 全局 key；事件 key = event + trigger |
| Domain authority | PASS | 只调用既有 Service，不直接写业务表 |
| API | PASS | 创建/列表/详情/运行/执行列表/执行详情/恢复 |
| Migration | CONDITIONAL PASS | 单一 head 与 PostgreSQL 离线 upgrade/downgrade DDL 通过；在线 PG 往返待集成环境 |
| Phase 20 专项 | PASS | 23 passed |
| 联合回归 | PASS | Phase 14/19/20：53 passed |
| 完整后端回归 | PASS | 287 passed |
| Ruff | PASS | app + tests + alembic 全通过 |
| 应用覆盖率 >=95% | PASS | 915 statements，43 miss，95% |
| 文档 | PASS | ADR-0041/0042、Benchmark、安全、生产、升级回滚、Runbook、指标 |

## 2. 架构基准结论

完整分析：`docs/github-reference-analysis-phase-20.md`。

- **Shuffle：** 采用 Workflow/Trigger/Action 分离；不采用自定义 Python/Script Action。
- **StackStorm/Orquesta：** 采用事件触发、声明式流程和执行审计；不采用通用 fork/join 与 Shell action。
- **n8n：** 采用 Node/Execution/output context；不采用 Code Node 与动态第三方节点。
- **Temporal：** 采用 durable history、retry、resume 和 Saga compensation；不引入 Temporal Server/Event Replay。
- **Argo Workflows：** 采用节点状态与执行历史；Phase 20 仅实现确定性顺序子集。

## 3. Playbook Engine

新增 `backend/app/playbook/`：

- `PlaybookService`：创建、查询、运行、幂等、事件 Trigger、Resume；
- `PlaybookRegistry`：解析已持久化的不可变 Version 并核对 YAML/document；
- `PlaybookPlanner`：平台 Policy 与 Playbook allowlist 校验；
- `PlaybookRuntime`：顺序 checkpoint、retry、timeout、waiting/resume、compensation；
- `PlaybookExecutor`：只将 typed Node 翻译为已有领域 Service 调用；
- `PlaybookPolicy`：runner、approver、plugin、capability、timeout、retry、parallel。

核心调用链：

```text
Trigger -> Service -> Registry -> Planner/Policy -> Runtime -> Executor -> Domain Service
```

## 4. DSL Version 与安全边界

DSL 唯一版本为 `v1`。创建时保存 source YAML、规范化 document、版本和 SHA-256 checksum。Pydantic 模型 `extra=forbid`，Playbook Version 对已存在 Execution 不可变。

Condition 使用 `ast.parse(mode="eval")` 仅解析表达式语法，但从不调用 Python `eval`。允许 Constant、Name、Subscript、List、Tuple、Dict、not、and/or 和比较；拒绝 Call、Attribute、算术、推导式及未知 AST。

安全扫描确认 `backend/app/playbook/` 中不存在 `eval`、`exec`、subprocess、dynamic import 或 Shell 路径。

## 5. Trigger 与 Node

已实现 Trigger：

```text
manual
incident.created
```

已实现 Node：

```text
condition
approval
assessment
detection
response
notification
ticket
```

Schedule、Finding Created、SecurityEvent Created、Approval Granted、Response Completed、Notification Failed，以及 Delay/Parallel 均为保留枚举，在 DSL 校验阶段 fail closed。

## 6. 执行、审批与补偿

Runtime 持久化 Execution/Step 的 status、attempt、input/output、error、开始/完成时间及 compensation result。审批缺失时进入 `WAITING_APPROVAL`；Resume 必须使用原 runner、空 input、同一 Playbook Version，并由不同的授权 approver 批准。

补偿按已完成 Step 逆序：

| 原 Node | Compensation | 权威边界 |
|---|---|---|
| Response | Rollback | `ResponseService.rollback()` |
| Notification | Ignore | 明确记录 `IGNORED`，不伪造撤回 |
| Ticket | Close | `NotificationService.close_ticket()` |

任一补偿失败，Execution 进入 `COMPENSATION_FAILED`，不伪装为已恢复。

## 7. 数据库与 Migration

Migration：`backend/alembic/versions/20260803_0018_playbook_engine.py`。

新增：

- `playbooks`
- `playbook_versions`
- `playbook_triggers`
- `playbook_executions`
- `playbook_step_executions`

未修改 Assessment、Detection、Response、Incident 业务表。迁移图唯一 head 为 `20260803_0018`。测试在 Alembic PostgreSQL 离线方言上下文中实际执行本 Migration 的 `upgrade()`/`downgrade()`，验证五表、六组外键、状态 Check、幂等/Step 唯一约束和逆序删除。

本机 Docker 服务未启动；用 SQLite 执行全历史 Migration 会在 Phase 0 的 PostgreSQL `DEFAULT now()` 处失败，尚未到达 Phase 20。未修改历史生产 Migration。真实 PostgreSQL 16：

```text
upgrade head -> downgrade 20260802_0017 -> upgrade head
```

被列为集成/发布环境强制门禁，不能视为本机已通过。

## 8. API

```text
POST /playbooks
GET /playbooks
GET /playbooks/{id}
POST /playbooks/{id}/run
GET /playbooks/executions
GET /playbooks/executions/{id}
POST /playbooks/executions/{id}/resume
```

API 使用现有依赖注入和平台错误映射，没有新增 Plugin API 或绕过领域 Service 的执行端点。

## 9. Security Boundary Analysis

完整文档：`docs/phase-20-playbook-safety-and-boundary.md`。

Playbook 只拥有自身五表；Executor 注入 Assessment/Detection/Response/Notification Service，没有对应业务 Repository 的直接写路径。Incident 只通过平台事件进入 Playbook；Playbook 不修改 Incident。Response 仍经过 Response Framework Approval/execute/rollback 权威链。

## 10. Safety Case Analysis

重点安全案例均已覆盖：任意代码、未授权响应、重复触发、同人审批、审批过期、重试副作用、部分成功、补偿失败、Reserved 功能误启用和 Incident 直接修改。测试验证 fail-closed，不以“记录错误后继续”替代拒绝。

## 11. Architecture Trade-off Analysis

1. 严格 DSL 换取静态验证，放弃通用脚本灵活性。
2. 顺序执行降低吞吐量，换取确定性和小爆炸半径。
3. 领域 Service 复用保留 Policy/Audit，避免直接数据库耦合。
4. 五表持久化增加运维成本，但提供执行历史和审批恢复。
5. Saga compensation 适配外部副作用，但必须接受补偿可能失败。
6. Request-scoped in-memory Event Bus 适合 Phase 20 验证，不等于生产 durable broker。

## 12. Production Integration Readiness

完整文档：`docs/phase-20-playbook-production-readiness.md`。

生产前重点：PostgreSQL 在线往返、持久 Event Bus/Outbox、多副本 execution claim/lease/fencing、IAM 职责分离、Capability approval matrix、故障注入、数据库 failover、补偿演练和 Architect/SOC/DBA/SRE 签署。

## 13. Upgrade / Rollback / Runbook / Monitoring

生产文档已包含：

- DSL v1 升级兼容规则；
- immutable Version 与 checksum；
- Migration upgrade/downgrade 流程；
- Downgrade 删除历史的高风险警告；
- RUNNING 卡住、WAITING_APPROVAL、FAILED/TIMED_OUT、duplicate trigger、migration failure Runbook；
- Execution/Step duration、retry、approval wait、timeout、compensation、trigger match、idempotency replay 指标。

## 14. 测试与覆盖率

```text
Phase 20 专项：23 passed
Phase 14/19/20 联合回归：53 passed
完整后端回归：287 passed
Ruff（app/tests/alembic）：All checks passed
Playbook 应用覆盖率：95%（915 statements，43 miss）
```

覆盖率明细：

```text
API route             90%
Models                100%
Contracts              95%
Executor              100%
Planner               100%
Policy                100%
Registry              100%
Runtime                88%
Service                95%
Repository            100%
TOTAL                  95%
```

Windows 执行包装器偶尔在 pytest 明确显示全部通过后返回非零 shell code；完整摘要无 failure，coverage report 的 `--fail-under=95` 返回 0。

## 15. Known Limitations

1. Trigger 仅 manual、incident.created。
2. Delay/Parallel 不可执行，固定单并发。
3. 不支持脚本、循环、通用 DAG、动态连接器。
4. Event Bus 非持久、非跨进程。
5. 无多副本 Execution lease/fencing 和崩溃自动接管。
6. Notification 已发送后只能 Ignore compensation。
7. 真实 PostgreSQL 全历史在线往返待集成环境执行。
8. Production IAM/RBAC、负载与灾备演练不属于本机功能门禁。

## 16. Release Readiness Checklist

| 项目 | 结果 |
|---|---:|
| 功能与安全 DSL | PASS |
| Domain Service boundary | PASS |
| Approval separation | PASS |
| Retry/Timeout/History/Resume | PASS |
| Compensation | PASS |
| Reserved fail closed | PASS |
| Migration source/dialect/reversibility | PASS |
| PostgreSQL online round trip | REQUIRED BEFORE PRODUCTION |
| Durable event delivery | REQUIRED BEFORE PRODUCTION |
| Multi-replica execution ownership | REQUIRED BEFORE PRODUCTION |
| 95% coverage | PASS |
| Full backend regression | PASS |
| Ruff | PASS |
| Architect Review | PENDING |

## 17. Architect Review 重点

1. 是否接受 Capability-only DSL 与无脚本边界；
2. Approval Step 与 Response Framework 双重权威关系；
3. 顺序执行和 `max_parallel=1` 是否符合 Phase 20 风险偏好；
4. 五表持久化、不可变 Version 与全局 idempotency key；
5. Response/Notification/Ticket 补偿语义；
6. request-scoped Event Bus 是否仅作为阶段验证而不进入生产；
7. PostgreSQL 在线 Migration 往返、多副本 ownership 与 durable event 作为生产硬门禁。

**最终结论：Phase 20 功能开发和本地质量门禁已完成。开发立即停止，等待 Architect Review；未进入 Phase 21。**
