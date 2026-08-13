# Phase 19 Development Report

**项目：** Cyber Agent Platform（CAP）  
**阶段：** Phase 19 — EDR Response Plugin  
**状态：** **开发完成，等待 Architect Review；不得进入 Phase 20**  
**边界结论：** Provider-neutral、Mock-only、零生产终端访问、零数据库迁移、零 EDR 专属 API。

---

## 1. Acceptance Checklist

| 验收项 | 状态 | 实现/证据 |
|---|---:|---|
| EDR Plugin | PASS | `EDRResponsePlugin` 完整生命周期 |
| EDR Adapter | PASS | 参数、Capability、Provider、Verification、Rollback |
| Mock Provider | PASS | 四项危险能力均为 `false` |
| HostAction Model | PASS | 指定 10 字段、严格模型、规范化 SHA-256 |
| host.isolate | PASS | Synthetic observed-state transition + read-back |
| host.unisolate | PASS | 可直接执行，也作为 isolate rollback |
| process.terminate | RESERVED | 模型可表示，Policy fail closed |
| collect.package | RESERVED | 模型可表示，Policy fail closed |
| Verification | PASS | desired -> read-back -> observed -> Evidence -> Audit |
| Rollback | PASS | 逆操作、绑定 token、独立 read-back/Evidence/Audit |
| Approval | PASS | Response Approval 强制；payload 不可伪造 approver |
| Manifest V2 | PASS | strict `extra=forbid`，Worker/Sandbox/Secret 边界一致 |
| Worker Runtime | PASS | 复用现有 Runtime，Manifest runtime `phase-18.1` |
| Sandbox Runtime | PASS | 网络/文件系统关闭，单并发 |
| Secret Provider | PASS | Manifest 声明 memory Provider，Mock 无 secret reference |
| Audit / Capability / Health | PASS | 复用框架，`response.edr` 注册且 HEALTHY |
| GitHub Reference Analysis | PASS | 五个强制基准均完成 |
| Production Documentation | PASS | 独立 readiness 文档 |
| Configuration Drift | PASS | 只生成 drift/Evidence/incident candidate，不自动修复 |
| Safety Case | PASS | 误隔离/误恢复/重复/丢失/离线覆盖 |
| 数据库 | PASS | 零 Migration、无 EDR 专属表 |
| API | PASS | 未新增 EDR API，复用统一 Response API |
| 应用覆盖率 >=95% | PASS | EDR 应用代码 **99%** |
| 回归 | PASS | 完整后端套件：**264 passed**；Phase 18 Manifest 兼容 + Phase 19 + Phase 14：**31 passed** |

## 2. GitHub Reference Analysis

### 2.1 Velociraptor — Agent Collection / Artifact Isolation

参考：<https://github.com/Velocidex/velociraptor>、<https://docs.velociraptor.app/docs/artifacts/>。

Velociraptor 将可执行查询封装为服务器管理的 Artifact；客户端服务器模式不允许直接裸跑 VQL，服务器编译 Artifact 与依赖后下发，参数、precondition 和来源构成安全且可测试的执行单元。其设计强调：服务端集中版本、端点侧有界执行、只回传高价值结果，并减少向可能已被攻陷客户端泄露 Artifact 元数据。

**CAP 映射：** `HostAction` 类似参数化 Artifact 的稳定请求单元；Plugin 不接受脚本/命令，只接受严格 Typed JSON；Worker/Sandbox 是执行边界；Provider read-back 只返回规范化观察与 Evidence。**差异：** Phase 19 不向端点下发任何内容，也不实现 collection。

### 2.2 Wazuh Active Response — Endpoint Response

参考：<https://documentation.wazuh.com/current/user-manual/capabilities/active-response/index.html>、<https://github.com/wazuh/wazuh>。

Wazuh Active Response 根据 rule ID/level/group trigger 在 monitored endpoint 执行脚本，并区分 stateless 与 stateful response；官方明确警告规则或响应实现不当会增加 Endpoint 风险。Stateful response 可在超时后撤销动作。

**CAP 映射：** 将 trigger 与高权限执行解耦为 Incident/Response Plan/Approval；所有 EDR 动作有明确状态、反向动作与验证；禁止任意 script。**差异：** CAP Phase 19 不自动触发、不在端点执行脚本、rollback 不使用无条件定时恢复，避免在威胁仍活跃时自动 unisolate。

### 2.3 Microsoft Defender for Endpoint — Machine Action / Isolation / Collect Package

参考：
- <https://learn.microsoft.com/en-us/defender-endpoint/api/isolate-machine>
- <https://learn.microsoft.com/en-us/defender-endpoint/api/collect-investigation-package>
- <https://learn.microsoft.com/en-us/defender-endpoint/api/machineaction>

MDE isolate 使用 `POST /machines/{id}/isolate`、Bearer token、`Machine.Isolate` 权限与必填 comment，返回 201 和异步 Machine Action。Machine Action 具有 ID、type、status、machineId、requestor、externalID、时间与 comment；状态包含 Pending/InProgress/Succeeded/Failed/TimeOut/Cancelled。collect package 使用独立 `Machine.CollectForensics` 权限，已运行时返回冲突。文档还提示 full VPN 下隔离可能切断云服务，API 有每分钟/每小时限流。

**CAP 映射：** `HostAction.id/action/status/host_id/requested_by/reason/created_at` 对齐 Machine Action；Adapter 必须把 API 接受与最终成功分离，执行后 poll/read-back；`collect.package` 仅预留；生产 readiness 明确 RBAC、限流、VPN/管理通道和 async 状态。

### 2.4 CrowdStrike Falcon — Host Action / Containment

参考：<https://developer.crowdstrike.com/api-reference/collections/hosts/>、CrowdStrike SDK GitHub 项目。

Hosts Service 以 AID 标识设备；`PerformActionV2` 支持 `contain` 和 `lift_containment`，写操作要求 Hosts WRITE，host details/online lookup 要求 Hosts READ。Containment 阻断除 Falcon cloud 与 containment policy 允许地址外的通信，lift containment 恢复正常通信。

**CAP 映射：** `host.isolate` / `host.unisolate` 是可逆、Provider-neutral 语义；单 Host Asset 精确绑定；生产 Provider 需要 READ+WRITE 分权、区域 API、Provider-cloud 存活和 read-back。**差异：** Phase 19 不使用 Falcon SDK/credential/API。

### 2.5 Open Policy Agent — Policy Decision Authorization

参考：<https://github.com/open-policy-agent/opa>、<https://www.openpolicyagent.org/docs/latest/>。

OPA 将 policy decision 与 enforcement 解耦，接收任意结构化 input 并返回结构化 decision；显式 `default allow := false` 支持 fail closed。

**CAP 映射：** Response Policy/Approval 和 `EDRPolicyProvider` 是决策层，Adapter/Provider 是执行层；缺少/未知/保留 action 均拒绝。**差异：** Phase 19 不执行 Rego、不连接 OPA；未来可替换 decision 实现，但不能让 Provider 决定审批。

## 3. Production Integration Readiness

完整文档：`docs/phase-19-edr-production-readiness.md`。

- **身份认证：** 每 tenant/environment 独立 workload identity；优先 OAuth2 client credentials/workload federation；用户、审批人、执行人、Provider requestor 分离。
- **API Token/Secret：** 仅 Manifest 中 opaque Secret reference；JIT resolve、短期 token、轮换/撤销演练；禁止写入 Plan/Evidence/log/env。
- **网络：** 只允许 Provider 区域 API FQDN，TLS/hostname/tenant-region pinning；Plugin 仍不可直连 Endpoint。
- **Timeout：** connect/request/total/polling 分开；接受异步 action 不等于成功。
- **Retry：** 仅 transport、429、显式 retryable 5xx；遵守 `Retry-After`，指数退避+jitter；401/403/4xx validation 不重试。
- **Idempotency：** `HostAction.id + checksum`；same/same 返回旧 receipt，same/different fail closed；每 host 串行或 optimistic version。
- **Upgrade：** 固定 API/SDK 版本，contract tests；disabled -> read-only -> canary -> bounded rollout；保留旧 Adapter 和 rollback path。
- **Runbook：** token compromise、Provider outage、stuck action、误隔离、误恢复、Agent offline、host missing、upgrade rollback、Evidence export。

结论：生产 Provider 必须另立 ADR/Phase，经 threat model、秘密与网络配置、vendor sandbox、灾备演练和 Architect 批准；不能通过修改配置直接启用。

## 4. Configuration Drift Analysis

流程：`Response Plan HostAction (Desired) -> Provider -> Read-back (Observed) -> Evidence -> Drift -> Incident Candidate`。

漂移包括 observed isolation state 不符、host missing、Agent offline、last action ID 不符。验证结果写入 `drift_detected`、`incident_candidate`、`desired_state`、`observed_state`，并明确 `auto_remediation=false`。Plugin 不访问 Incident Service；本阶段不自动创建/修改 Incident，不自动补偿或修复。此设计防止错误 desired state 被无审批地持续施加。

## 5. Safety Case Analysis

| 风险 | 避免 | 恢复 |
|---|---|---|
| 误隔离 | 单一 HOST Asset，UUID 精确相等，checksum，Approval，read-back | 核对身份后批准 unisolate，保留独立 Evidence/Audit |
| 误恢复 | 只能执行显式 unisolate 或绑定 token 的 inverse rollback | 重新评估风险，新建并审批 isolate Plan；不静默重隔离 |
| 重复执行 | ID/checksum idempotency；冲突内容 fail closed | 返回 prior receipt，不重复改变状态 |
| 主机丢失 | Provider inventory 不存在即失败，不推断成功 | 进入 inventory drift / alternate containment runbook |
| Agent 离线 | execute fail closed；verify 要求 online | Agent 恢复后重新人工决策，或使用替代控制面 |
| accepted 未生效 | success 必须 read-back + last action ID | 标记 failed，保留 Evidence，人工升级 |

## 6. Security Boundary Analysis

Plugin 不能直接访问 Endpoint，因为 lifecycle context 仅提供 immutable IDs、actor、typed parameters、rollback token 和 certified permissions；没有 DB session、Incident/Asset service、secret、network client、filesystem writer、shell。Provider 独占连接，才能集中控制 tenant/region、认证、限流、timeout/retry、vendor error mapping 和 read-back，并避免 Plugin 绕过 policy 或扩张 scope。

Approval 必须存在：isolate 可能造成业务中断和管理锁死；unisolate 可能恢复攻击者连通性。现有 Response Framework 管理权威审批记录和 distinct approver；`HostAction.approved_by` 在请求 JSON 中必须为空，不能伪造。Provider 不决定审批。

## 7. Architecture Trade-off Analysis

1. **HostAction Typed JSON vs EDR 表：** 选 Typed JSON，复用 Response Plan/Execution/Evidence/Audit；代价是复杂 vendor query 不适合 SQL。Phase 19 无此需求。
2. **同步 read-back vs 异步 job model：** Mock 可同步验证；接口保留 status/receipt，生产 Provider 需 poll 异步 Machine Action。避免把 HTTP 201/202 当成功。
3. **单 capability + action enum vs 每 action capability：** 选 `response.edr` + action allowlist，保持统一 API/Plugin；Policy 再对动作逐项 fail closed。
4. **Rollback token vs 单靠 Plan state：** 双重约束，token 绑定 Plan/Incident/action/host/version/checksum，降低错误补偿风险。
5. **不自动 drift remediation：** 降低恢复速度，换取防止错误 desired state 自动破坏终端的安全边界。
6. **Mock in-memory Provider：** 不能证明 vendor SDK/网络可靠性，但能证明平台治理链、验证、回滚和安全边界。

## 8. EDR Plugin

新增 `backend/app/plugins/edr/`：

- `EDRResponsePlugin.initialize/plan/validate/execute/verify/rollback/shutdown/health`；
- capability `response.edr`；permissions 仅 execute/verify/rollback；
- Mandatory Approval、single Host scope、reserved action/provider-owned action 拒绝；
- 输出 EDR execution/rollback Evidence；
- 无 DB/Incident/Asset/Report/secret/network/shell/filesystem 依赖。

## 9. EDR Adapter

新增 `backend/app/tools/edr/adapter.py`：解析 `host_action`，校验 Host Asset scope 和 action，调用 Provider，执行 desired/observed verification，创建 inverse rollback，报告 drift。顶层 `tools/edr/manifest.yaml` 描述工具边界。

## 10. Mock Provider

`MockEDRProvider` 为 app-scoped in-memory observed state：

```text
network_access    = false
production_access = false
filesystem_write  = false
shell_execute     = false
```

它没有 HTTP client、socket、SDK、credential、subprocess 或 Endpoint 地址。支持 seed/offline/missing/drift 注入以验证安全路径；不访问真实终端。

## 11. HostAction Model

字段严格为：`id, host_id, action, status, version, checksum, requested_by, approved_by, reason, created_at`。额外字段拒绝；`host_id` 必须为 canonical Asset UUID；checksum 为 canonical desired content 的 SHA-256；Provider receipt 独立保存执行后 status/observed state。请求 `approved_by` 不作为权威审批信息，必须为空；权威值在 Response Approval/Audit。

## 12. 数据库变化（如有）

**无。零 Migration。** HostAction 存于现有 `ResponsePlan.parameters/plan` JSON；复用 Response Plan、Approval、Execution、Rollback、Evidence、Audit 表。未新增 EDR 业务表或控制面字段。

## 13. ER 图（如有）

无新 ER 图，既有 ER 不变：`Incident -> ResponsePlan -> Execution/Rollback/Evidence/Approval`。HostAction 是 ResponsePlan JSON value，不是 Entity/Table。

## 14. API

未新增 EDR 专属执行 API。已验证复用：

- `POST /response/plans`
- `POST /response/plans/{id}/approve`
- `POST /response/plans/{id}/execute`
- `POST /response/plans/{id}/rollback`
- `GET /response/plugins`

## 15. 测试情况

- Phase 19 专项：**14 passed**。
- Phase 18 Manifest V1/V2 兼容 + Phase 19 + Phase 14 关键回归：**31 passed**。
- 完整后端测试套件：**264 passed**。
- Ruff：**All checks passed**。
- EDR 应用覆盖率：**99%（382 statements，5 miss）**，超过 95% DoD；`fail-under=95` 门禁通过。
- 覆盖：isolate/unisolate、直接反向 rollback、read-back、Evidence/Audit、idempotency、same-ID conflict、missing/offline、drift no-auto-fix、strict schema、checksum、scope、reserved action、bad token、health、Manifest V2、API registration。

最终全量回归发现并修复了一处历史测试兼容问题：Phase 18 测试原先将 Manifest 数量固定为 8 且只按 V1 解析；新增 EDR V2 Manifest 后已更新为 9，并通过运行时版本分派器同时校验 V1/V2。生产代码和框架核心未因此改动。当前 Windows wrapper 偶尔在 pytest 明确显示全部通过后返回非零 shell code；覆盖率门禁返回 `Exit Code 0`，且完整测试摘要无 failure。

## 16. Known Issues

1. Mock Provider 状态是进程内存，进程重启即消失；符合 Synthetic 范围，不可用于生产。
2. 生产 EDR 异步 action polling、webhook、真实 rate limits、vendor error taxonomy 未实现。
3. `process.terminate` / `collect.package` 仅预留并 fail closed。
4. Drift 仅作为 Evidence metadata/Incident Candidate signal；尚无平台级 candidate ingestion workflow。
5. 本阶段未验证真实 VPN、管理通道、EDR cloud allowlist 或 agent platform compatibility。

## 17. Technical Debt

- 未来生产 ADR 需要 Provider Protocol 抽象、异步 status poller、per-host distributed lock、Provider API version contract suite。
- Authoritative approver 可在未来由 Response Runtime 以只读投影注入 action Evidence，但不得由 Plugin 查询数据库。
- Incident Candidate 需未来统一事件契约承接，不能在 EDR Plugin 内实现。
- 如启用 package collection，需要对象存储、size/hash/retention/malware handling 独立安全设计。

## 18. Plugin Certification Checklist

| 项目 | 结果 |
|---|---:|
| Manifest V2 | PASS |
| Worker Runtime | PASS |
| Sandbox Runtime | PASS |
| Secret Provider | PASS（Mock 无 secret value/reference） |
| Approval | PASS |
| Verification | PASS |
| Rollback | PASS |
| Health | PASS |
| Audit | PASS |
| Capability | PASS |
| Production Documentation | PASS |

## 19. Architect Review 准备说明

建议 Review 聚焦：

1. 是否接受 `HostAction` 作为 Response Plan Typed JSON 且零 Migration；
2. Plugin/Adapter/Provider 三段边界是否足以承载未来 live Provider；
3. `approved_by` 由 Approval/Audit 权威持有、请求 JSON 禁止伪造的语义；
4. isolate/unisolate 双向 rollback 与 token 绑定是否符合风险预期；
5. drift 只发 Incident Candidate signal、不自动修复；
6. Manifest V2 的 network/filesystem/secret false 是否准确证明 Mock-only；
7. 生产 Provider 必须另立 ADR/Phase，不得配置直开。

**最终结论：Phase 19 DoD 已满足。开发立即停止，等待 Architect Review；未进入 Phase 20。**
