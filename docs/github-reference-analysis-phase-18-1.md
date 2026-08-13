# Phase 18.1 GitHub Architecture Reference Analysis

## 1. 目的与边界

本分析只为 Phase 18 Architect Review 的 Critical/Major 修复提供设计依据，不引入 Kubernetes、etcd、Temporal、Nomad 或 Vault 运行时依赖，不新增 Plugin 或业务能力。

## 2. Kubernetes Lease

- 官方项目：<https://github.com/kubernetes/kubernetes>
- 参考契约：`coordination.k8s.io/Lease` 的 `holderIdentity`、`renewTime`、`leaseDurationSeconds`，以及 API Resource Version 的乐观并发语义。
- CAP 采用：Worker Lease 持久化 owner、renewed/expires time、version；状态更新使用数据库条件更新。
- CAP 不采用：Kubernetes API Server、Controller、Scheduler、CRD。

## 3. etcd

- 官方项目：<https://github.com/etcd-io/etcd>
- 参考契约：Revision、Transaction Compare/Then/Else、Lease TTL/Expiry/Revoke、Watch。
- CAP 采用：`state_version`/Lease `version` 作为 CAS 条件；过期 Lease 不再接受结果；Fencing Token 防止旧持有者恢复后写入。
- CAP 不采用：etcd 集群、Raft、Watch Stream。

## 4. Temporal

- 官方项目：<https://github.com/temporalio/temporal>
- 架构文档：<https://github.com/temporalio/temporal/tree/main/docs/architecture>
- 参考契约：服务端持久化执行历史、Worker 与控制面分离、任务结果回传、Heartbeat、Retry/Recovery。
- CAP 采用：每次 Sandbox Attempt 均持久化；失败重试和恢复以独立 Execution 记录关联；结果提交必须通过当前 Lease 验证。
- CAP 不采用：Event Sourcing Replay、Task Queue、Durable Timer、Temporal Server。

## 5. Nomad

- 官方项目：<https://github.com/hashicorp/nomad>
- 参考契约：Client Registration、Node/Allocation Status、Capability/Constraint-aware Placement、Task Event 与 Restart/Replacement History。
- CAP 采用：Worker Registry、状态机、Heartbeat/Stale Detection、Capability/Capacity 调度、Execution Attempt History。
- CAP 不采用：Nomad Server/Client、Raft、Job/Allocation API、Driver。

## 6. HashiCorp Vault

- 官方项目：<https://github.com/hashicorp/vault>
- 参考契约：Secret Lease TTL、Renew、Revoke、Lease 失效后的访问拒绝，以及审计但不泄露 Secret Value。
- CAP 采用：Secret Reference、解析成功/失败审计、缺失 Secret fail closed；Lease 释放/过期后拒绝旧 Token。
- CAP 不采用：Vault Server、Dynamic Secret、真实 Rotation/Revoke Backend。

## 7. 采用结论

| 设计问题 | 采用的参考原则 | CAP 实现 |
|---|---|---|
| 多副本事实一致性 | Kubernetes/etcd 控制面持久化与 CAS | Database 为唯一 Source of Truth，Memory 仅作可失效 Cache |
| 旧 Worker 陈旧写入 | etcd Revision、Lease Expiry | Owner + Fencing Token + Version + Expiry 同时校验 |
| 执行恢复可追溯 | Temporal History、Nomad Task Events | 每次 Attempt 独立持久化并记录 Recovery Link |
| Worker 生命周期 | Kubernetes Node/Lease、Nomad Client | 严格状态机、Heartbeat、Stale Detection |
| Secret 缺失 | Vault fail closed 与审计 | 缺失或 Provider 不匹配直接拒绝并记录 Audit |

Phase 18.1 的实现保持 Provider-neutral：吸收成熟系统的一致性契约，但不把核心平台绑定到任一外部调度器、KV、工作流或 Secret 产品。