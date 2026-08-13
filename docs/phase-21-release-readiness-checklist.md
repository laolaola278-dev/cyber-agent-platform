# Phase 21 Release Readiness Checklist

## Architecture

- [x] 不新增安全能力、Plugin、Platform Plane
- [x] Web Console 仅通过 API 访问数据
- [x] Dashboard/Audit/Settings 为只读聚合
- [x] Phase 21 零 Migration

## Security

- [x] 五个固定角色与细粒度权限
- [x] 非公开 API 默认拒绝
- [x] 高权限 Response/Approval/Playbook 后端授权
- [x] 浏览器不持有可信代理 Secret
- [x] Settings 脱敏
- [x] Audit 查询支持用户、资源、Plugin、Incident、Worker、事件与时间

## Observability

- [x] Prometheus Counter/Gauge/Histogram
- [x] 低基数 Label
- [x] Execution/Duration/Worker/Queue/Plugin/Approval/Playbook 指标
- [x] OpenTelemetry SDK Server Span
- [x] W3C Trace Context
- [x] Structured Log correlation
- [x] Liveness/Readiness
- [x] Grafana provisioning 与 Alert Rules

## Engineering

- [x] Frontend TypeScript、ESLint、Build
- [x] Backend Ruff
- [x] Phase 21 专项测试：32 passed
- [x] Phase 14/18/18.1/19/20/21 联合回归：104 passed
- [x] 完整后端回归：319 passed
- [x] Phase 21 应用覆盖率：99.05%（门禁 ≥95%）
- [x] Backend Ruff：All checks passed
- [x] Frontend ESLint：通过
- [x] Frontend TypeScript 与 Vite production build：通过
- [x] Docker Compose config：通过
- [x] Alembic 单一 head：`20260803_0018`
- [x] PostgreSQL 方言离线 Upgrade/Downgrade：通过
- [ ] 真实 PostgreSQL 在线 Migration 往返（Docker Engine 未启动，Production Entry Gate）
- [ ] Prometheus/Grafana 容器 smoke test（Docker Engine 未启动，Production Entry Gate）

## Operations

- [x] Deployment Guide
- [x] Upgrade Guide
- [x] Rollback Guide
- [x] Runbook
- [x] Compatibility Matrix
- [x] Version Policy
- [x] Known Limitations
