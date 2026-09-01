# CAP Frontend — Security Operations Console

> React + TypeScript + Vite + Ant Design 管理控制台

## 技术栈

| 层 | 技术 |
|------|------|
| 框架 | React 18 + TypeScript |
| 构建 | Vite 5 |
| UI | Ant Design 5 + `@ant-design/icons` |
| HTTP | Axios |
| 测试 | Vitest + Testing Library + MSW |

## 快速开始

```bash
cd frontend
npm install
npm run dev
```

开发服务器默认监听 `http://0.0.0.0:5173`，API 请求代理到 `/api`。

## 构建

```bash
npm run build
```

产物在 `dist/` 目录。

## 项目结构

```
frontend/
├── src/
│   ├── main.tsx           # React 入口
│   ├── App.tsx            # 应用主组件（页面路由 + 布局）
│   ├── api/
│   │   └── client.ts      # Axios API 客户端
│   ├── types.ts           # TypeScript 类型定义
│   ├── styles.css         # 全局样式
│   └── test/
│       ├── setup.ts       # 测试环境配置（jsdom + mocks）
│       └── App.test.tsx   # 组件测试
├── vite.config.ts         # Vite + Vitest 配置
├── tsconfig*.json         # TypeScript 配置
├── eslint.config.js       # ESLint 配置
├── Dockerfile             # 生产 Docker 构建
├── nginx.conf             # Nginx 反向代理配置
└── .env.example           # 环境变量模板
```

## 可用脚本

| 命令 | 说明 |
|------|------|
| `npm run dev` | 开发模式 |
| `npm run build` | 生产构建 |
| `npm run preview` | 预览构建产物 |
| `npm run lint` | ESLint 检查 |
| `npm test` | 运行测试 |
| `npm run test:watch` | 监听模式测试 |
| `npm run test:coverage` | 测试覆盖率报告 |

## 页面导航

| 路由 | 说明 |
|------|------|
| Dashboard | 安全运营态势看板 |
| Investigation | 安全调查 Agent |
| Data Acquisition | 数据采集 |
| Assets / Knowledge / Evidence | 资产 / 知识 / 证据 |
| Assessment / Detection | 评估 / 检测 |
| Incident / Response / Playbook | 事件 / 响应 / 剧本 |
| Approvals | 审批中心 |
| Audit | 审计中心 |
| Access Control | RBAC 权限 |
| Workers / Sandbox / Plugins | Worker / 沙箱 / 插件 |
| Settings | 系统设置 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VITE_API_BASE_URL` | `/api` | API 基础路径 |
| `APP_VERSION` | `1.0.0-rc1` | 应用版本 |

## Docker

```bash
# 构建镜像
docker build -t cap-frontend .

# 运行
docker run -p 8080:80 cap-frontend
```

生产环境通过 Nginx 反向代理 API 请求到后端服务（见 `nginx.conf`）。

## License

Apache License 2.0 — 参见仓库根目录 [LICENSE](../LICENSE)。