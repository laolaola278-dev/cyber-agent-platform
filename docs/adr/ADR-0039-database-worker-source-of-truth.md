# ADR-0039：Database 作为 Worker 唯一事实源

- 状态：Accepted for Phase 18.1
- 日期：2026-08-02

## 背景

Phase 18 的 Worker Registry、Lease Manager 使用进程内字典作为运行事实，而数据库仅用于查询展示。这会造成多副本之间状态分裂：一个实例看到 Worker ONLINE，另一个实例仍看到旧状态；进程重启还会丢失注册、心跳和执行关联。

## 决策

`workers`、`worker_leases`、`sandbox_executions` 是控制面唯一 Source of Truth。所有 Register、Heartbeat、State Transition、Lease 变更、Execution History 和 Result Commit 必须经过 SQLAlchemy Repository，并在事务内完成审计。内存结构只能作为可失效读缓存，不得作为写入判定、调度判定或结果提交依据。

Worker 状态更新使用 `state_version` 条件更新；没有匹配版本时拒绝写入并要求重新读取。健康检查和调度必须从数据库读取最新状态。

## 后果

- 支持多 API/Worker 副本的一致读取与并发保护。
- 进程重启不会丢失控制面状态。
- 每次状态操作需要数据库事务，延迟高于纯内存实现。
- 测试必须覆盖 Repository、CAS 冲突和事务审计。

## 拒绝的替代方案

- 以进程内 Memory 为主、数据库异步同步：会产生双轨状态，不满足安全执行要求。
- 仅依赖 API 读库、执行仍读内存：无法保证 Result Commit 的当前所有权。
