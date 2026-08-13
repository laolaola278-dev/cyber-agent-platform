# CAP Phase 0 工程交付报告

## 交付范围

本次交付严格限定为平台基础设施，不包含爬虫、漏洞扫描、IDS、WAF、攻击载荷、靶场或任何外部安全工具执行能力。

## 实现清单

### 后端

- FastAPI 应用工厂、OpenAPI、Swagger 和 ReDoc
- 配置层：PostgreSQL、Redis、密钥、日志和 CORS
- SQLAlchemy 2.x 异步 Engine / Session
- 5 个 ORM 模型：Agent、Tool、Task、TaskExecution、AuditLog
- Agent / Task Schema、Repository、Service、依赖注入和 Router
- `GET /health`
- `GET /agents`、`POST /agents`
- `GET /tasks`、`POST /tasks`
- Agent 名称与版本冲突返回 HTTP 409
- X-Request-ID 中间件

### 数据库

- Alembic 异步迁移环境
- Phase 0 初始化迁移
- UUID 主键、外键、索引、唯一约束和删除规则
- Mermaid ER 图

### 前端

- React、TypeScript、Vite、Ant Design
- 暗色企业级控制台布局
- 后端健康状态、Agent 数量和 Task 数量概览
- Agent 列表与注册表单
- Task 列表与创建表单
- Nginx SPA 与 `/api` 反向代理配置

### 基础设施

- Backend、Frontend 多阶段 Dockerfile
- PostgreSQL 16、Redis 7、Backend、Frontend Compose 服务
- 可选 pgAdmin profile
- 健康检查、持久卷和独立网络
- 根环境变量模板、Git 忽略规则和 Makefile

## 架构决策

1. HTTP Router 不直接访问 ORM；所有用例经 Service 和 Repository。
2. Service 持有事务边界，Repository 只封装持久化操作。
3. 使用可移植的 SQLAlchemy `Uuid`，以便 PostgreSQL 生产环境和 SQLite 测试共享模型。
4. Agent/Tool 采用 `(name, version)` 唯一键，为未来兼容性和灰度发布保留空间。
5. TaskExecution 明确关联 Agent 与 Task；Task 删除时级联执行记录，Agent 被引用时限制删除。
6. Redis 在 Phase 0 只完成连接配置与容器编排，不虚构队列或锁能力。
7. 前端容器使用相对 `/api`，由 Nginx 转发到 backend，避免把容器内部地址暴露给浏览器。

## Architect Review 重点

建议 Architect 重点评审以下决策，再下发 Phase 1 Prompt：

- Agent Manifest 和 Tool Adapter 的正式契约、版本协商与签名机制。
- `permissions` / `tools` JSON 在 Phase 1 是否规范化为策略表和关联表。
- 状态字段是否采用数据库枚举、检查约束或领域状态机。
- 事件总线选型：NATS JetStream 或 RabbitMQ。
- 身份系统选型：内建 JWT、OIDC，或企业 IdP 集成。
- 审批状态机、授权目标范围和不可变审计日志的事务一致性。
- API 版本化、幂等键、游标分页和错误码规范。
- Runtime Adapter 的容器隔离、网络策略、镜像签名和资源配额。

## 验收结果

- Python 编译检查：通过。
- Pytest：4 项测试全部通过，覆盖健康检查、Agent 注册/列表/重复冲突、Task 创建/列表。
- Ruff：全部检查通过。
- Black：49 个 Python 文件格式检查通过。
- Alembic：PostgreSQL 离线 DDL 生成成功，初始化迁移可解析至 `20260729_0001`。
- Docker Compose：配置解析成功，服务依赖、网络、卷与健康检查有效。
- Docker 镜像构建：本机 Docker Desktop daemon 未运行，无法连接 `dockerDesktopLinuxEngine`，因此未完成镜像级验收。
- Frontend：依赖安装与构建结果见最终工程验收记录。

## 暂未实现（符合 Phase 0 边界）

- Tool Registry API
- Agent 实例启动或容器调度
- Task 执行器与工作流引擎
- Redis 消息、缓存或分布式锁
- 用户、租户、RBAC / ABAC、审批
- AuditLog 自动写入和防篡改策略
- Kubernetes、CI/CD 和生产可观测性
- 任何具体网络安全能力

## 启动

```bash
cp .env.example .env
docker compose up --build
```

- Console: http://localhost:8080
- API: http://localhost:8000
- Swagger: http://localhost:8000/docs

完整操作说明见根目录 `README.md`。
