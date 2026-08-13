# ToolManager、Capability Registry 与 Browser 生命周期

## Runtime 服务解析

`RuntimeContext` 包含任务、Trace ID、日志、配置、事件发布器、Agent ID 和 `ServiceProvider`。Agent 不直接持有数据库 Session，也不从 Runtime 获取具体 Tool 实现。

```python
manager = runtime.services.resolve(ToolManager)
adapter = await manager.load("playwright")
evidence = runtime.services.resolve(EvidenceService)
```

## Tool 生命周期

1. RuntimeService 根据受信 Agent Manifest 幂等引导 Tool Registry 定义。
2. ToolManager 按名称读取启用的 Tool 与当前活动 ToolVersion Manifest。
3. ToolFactory 根据 `adapter` 标识创建 Adapter。
4. ToolManager 初始化并缓存 Adapter，发布 ToolLoaded。
5. 请求清理或显式卸载时关闭 Adapter，发布 ToolUnloaded。

Tool Manifest 的 `runtime_requirements` 包含：

- `adapter`：Factory builder 标识；
- `capabilities`：Tool 提供的能力声明；
- `config`：初始化配置。

## Browser 生命周期

```text
ToolManager
  owns PlaywrightAdapter
    owns BrowserManager
      owns one Browser
      tracks N isolated BrowserContexts
        each owns Page
```

每次 `execute()` 创建独立 BrowserContext，在 `finally` 中关闭。交互式 `open()/close()` 也以 Context 为隔离单位。Adapter.shutdown 关闭活动 Context、Browser 和 Playwright process。

## Capability 调度

Agent 注册时，Manifest 的 capability 名称被幂等写入 Capability，并重建 AgentCapability 绑定。任务声明 required_capabilities 后，Dispatcher 先求同时满足全部能力的 Agent ID，再应用状态、心跳、权限和原有调度策略。空能力列表保持 Phase 1 兼容。

## 安全边界

- PlaywrightAdapter 仅允许绝对 HTTP(S) URL 和 GET；
- 拒绝 cookies、headers、credentials、proxy 注入；
- 不包含验证码绕过、认证绕过、WAF 绕过或隐身代理能力；
- 内网/环回/云元数据 SSRF 阻断仍是后续必须完成的安全技术债。
