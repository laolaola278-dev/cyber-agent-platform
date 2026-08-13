# ADR-0007: Runtime 不直接管理 Tool 实现

- 状态：Accepted
- 日期：2026-07-29
- 阶段：Phase 2.1

## 背景

Phase 2 RuntimeManager 直接接收具体 Tool Adapter，FastAPI DI 直接实例化 PlaywrightAdapter。Runtime 因而知道具体工具实现，无法通过 Registry/Manifest 动态扩展，也混合了 Agent 与 Browser 资源生命周期。

## 决策

RuntimeContext 只暴露类型化 `ServiceProvider`。Agent 通过 `resolve(ToolManager)` 请求工具；ToolManager 查询 Tool Registry 当前活动 Manifest，经 ToolFactory 创建 Adapter，负责初始化、缓存、卸载与审计。PlaywrightAdapter 再依赖 BrowserManager 管理 Browser/BrowserContext/Page。

```text
RuntimeContext
  -> ServiceProvider
  -> ToolManager
  -> Tool Registry / ToolVersion Manifest
  -> ToolFactory
  -> Tool Adapter
  -> BrowserManager
```

请求作用域 Runtime 依赖在清理阶段调用 `ToolManager.shutdown_all()`；Agent.shutdown 只释放自身引用，不关闭平台共享工具。

## 后果

- Runtime 不导入 Playwright，也不实例化具体 Adapter。
- 新 Tool 通过 Registry Manifest 和 Factory builder 扩展。
- Browser 进程与 BrowserContext 的生命周期有单一所有者。
- Tool 加载与卸载发布审计事件。

## 未采用方案

- Agent 自行创建/关闭 Playwright：资源泄漏和跨 Agent 干扰风险高。
- RuntimeManager 内硬编码 Adapter 映射：违反 Plugin First 和依赖倒置。
- 全局单例 Browser：当前请求作用域模型下隔离与清理边界不清晰；未来可由应用级 Tool Host 替换，但必须保留 BrowserContext 隔离。
