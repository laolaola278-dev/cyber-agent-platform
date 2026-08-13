# Phase 18.1 Safety Case Analysis

## 1. Safety Claim

Phase 18.1 证明的是 Worker/Sandbox 控制面一致性和陈旧写入防护，不证明真实 OS/Kernel/Container/VM 隔离。系统在 Worker、Lease、Secret、Provider Capability 或 Result Ownership 无法证明时默认拒绝执行或提交。

## 2. Threats and Controls

| 威胁 | 控制 | 证据 | 残余风险 |
|---|---|---|---|
| API 多副本看到不同 Worker 状态 | Database 唯一事实源；Cache 可清空 | Repository/Registry 测试 | 数据库本身的 HA 不在本阶段 |
| 并发心跳覆盖新状态 | `state_version` CAS | 版本递增与条件更新 | 高冲突下需重试/退避策略 |
| 暂停后恢复的旧 Worker 提交结果 | UUID Fencing Token + Version + Owner + Expiry | stale Token/Version/Expired Commit 拒绝测试 | 数据库事务隔离级别需部署验证 |
| Retry 丢失失败历史 | 每 Attempt 独立 Sandbox Execution | FAILED → RECOVERED 关联测试 | Plugin 外部副作用仍需幂等 |
| 结果重复/终态覆盖 | 只允许 RUNNING Execution 提交终态 | Repository Commit 条件 | 跨系统输出的 exactly-once 未证明 |
| Secret 缺失后降级为空值 | Resolve fail closed | `SecretNotFound` 与失败 Audit 测试 | Memory Provider 不具备 Vault 级轮换 |
| Provider 无真实 Capability 却接受 Profile | Provider Capability 强制校验 | Runtime Contract/Policy | Memory Provider 同进程，无真实隔离 |
| Manifest 未知字段绕过策略 | V2 `extra="forbid"` | 未知字段拒绝测试 | V1 为兼容仍允许 legacy metadata |
| Worker 非法状态跳转 | 显式状态机 | 非法转换测试 | 跨事务业务组合需持续审计 |
| 安全活动不可追踪 | Transactional Audit | Worker/Lease/Sandbox/Secret Audit 测试 | 外部 Audit Sink/不可篡改存储未实现 |

## 3. Fail-Closed Paths

以下情况必须拒绝：

1. Worker 不存在、状态不可调度、Capability 不匹配或容量耗尽；
2. Worker 状态版本已变化；
3. Lease Owner、Fencing Token、Version 不匹配，Lease 非 ACTIVE 或已过期；
4. Sandbox Provider 不支持 Profile 要求的 Network、Filesystem、Secret 或 Timeout；
5. Secret Reference 缺失、禁用或 Provider 不匹配；
6. Manifest V2 出现未知字段或边界配置不一致；
7. Result Execution/Worker/Lease Identity 不一致；
8. Execution 已是终态却再次提交。

## 4. Proven Safety Properties

- Worker 控制面状态可跨进程重启从数据库恢复；
- 进程内 Cache 丢失不影响事实状态；
- 旧 Lease 持有者不能用陈旧 Token/Version 覆盖新结果；
- 每个 Sandbox Attempt 的开始与结果均可审计和追溯；
- Secret 解析失败不会退化为空值继续执行；
- 8 个既有 Plugin Manifest 继续按 V1 兼容，V2 提供严格未知字段策略；
- 五领域统一执行链在本阶段回归中保持兼容。

## 5. Explicit Non-Claims

本阶段不声称：

- `MemorySandboxProvider` 能防御恶意或失陷 Plugin；
- CPU、内存、文件系统、网络、进程、用户或 syscall 被 Kernel 隔离；
- 超时能强制终止阻塞原生线程/进程；
- Worker 与 Control Plane 具备 mTLS、Attestation 或硬件身份；
- Secret 具备 Vault/KMS 轮换、撤销、短 TTL 和内存擦除；
- 外部 Plugin 副作用达到 exactly-once；
- 当前环境已完成真实 PostgreSQL 在线升级/降级。

## 6. Safety Conclusion

Phase 18.1 可判定为 **Production Consistency Control-Plane Candidate**：一致性、Fencing、持久化、审计和 fail-closed 语义有实现与测试证据；但仍是 **Synthetic Sandbox**，不能判定为 Production Isolation Certification。