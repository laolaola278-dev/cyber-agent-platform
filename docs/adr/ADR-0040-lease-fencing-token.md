# ADR-0040：Lease Fencing Token 是结果提交的强制条件

- 状态：Accepted for Phase 18.1
- 日期：2026-08-02

## 背景

TTL 和 Owner 校验不能阻止暂停后恢复的旧 Worker 提交结果。旧 Worker 可能在 Lease 已过期或由其他执行者接管后继续运行；如果只检查 Owner 或执行 ID，陈旧结果仍可能覆盖新结果。

## 决策

每次 Lease 获取生成不可复用的 UUID Fencing Token，并连同 `version` 持久化。Worker 续租、释放和提交 Sandbox Result 时必须同时提供：

- Lease ID
- Owner
- Fencing Token
- 期望 Version

数据库只在 Lease 为 ACTIVE、未过期、Owner/Token/Version 全部匹配时接受 Result Commit。Token 不通过 API Schema、日志或审计详情泄露。Lease 过期、释放或 CAS 冲突后，旧 Token 立即失效。

## 后果

- 暂停、网络分区或延迟 Worker 的陈旧结果会被拒绝。
- Result Commit 与 Lease 验证必须位于同一个数据库事务。
- Retry 使用独立 Sandbox Execution History，但由同一个有效 Lease 约束。
- 审计记录 Lease 版本和状态，不记录 Token 值。

## 参考

该决策吸收 Kubernetes Lease 的 holder/renew/TTL/resourceVersion、etcd transaction/revision 和 Vault lease revoke 的失效语义；CAP 使用关系数据库条件更新实现相同的一致性目标。
