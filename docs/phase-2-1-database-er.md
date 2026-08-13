# CAP Phase 2.1 数据库 ER 图

```mermaid
erDiagram
    AGENT ||--o{ AGENT_VERSION : versions
    AGENT ||--o{ AGENT_CAPABILITY : provides
    CAPABILITY ||--o{ AGENT_CAPABILITY : bound_by
    AGENT ||--o| AGENT_RUNTIME : owns
    AGENT ||--o{ TASK_EXECUTION : executes
    AGENT ||--o{ EVIDENCE : produces
    AGENT ||--o{ REPORT : generates
    TOOL ||--o{ TOOL_VERSION : versions
    TASK ||--o{ TASK_EXECUTION : executions
    TASK ||--o{ EVIDENCE : collects
    TASK ||--o| REPORT : summarizes

    AGENT {
        uuid id PK
        varchar name UK
        varchar version
        json permissions
        json capabilities
        json tools
        varchar minimum_runtime_version
        varchar platform_version
        varchar sdk_version
        varchar status
        timestamptz heartbeat_time
    }
    CAPABILITY {
        uuid id PK
        varchar name UK
        text description
        varchar risk_level
        boolean enabled
    }
    AGENT_CAPABILITY {
        uuid id PK
        uuid agent_id FK
        uuid capability_id FK
        json configuration
    }
    TASK {
        uuid id PK
        varchar task_type
        json input
        json required_permissions
        json required_capabilities
        uuid target_agent_id
        varchar status
    }
    TOOL {
        uuid id PK
        varchar name UK
        varchar version
        varchar tool_type
        json runtime_requirements
        varchar status
    }
    TOOL_VERSION {
        uuid id PK
        uuid tool_id FK
        varchar version
        json manifest
        boolean is_active
    }
    AGENT_RUNTIME {
        uuid id PK
        uuid agent_id FK UK
        varchar manifest_path
        varchar entrypoint
        varchar status
        json last_health
    }
    EVIDENCE {
        uuid id PK
        uuid task_id FK
        uuid agent_id FK
        varchar evidence_type
        varchar sha256
        varchar content_type
        varchar object_storage_path
        varchar html_hash
        varchar content_hash
        varchar screenshot_path
    }
    REPORT {
        uuid id PK
        uuid task_id FK UK
        uuid agent_id FK
        json json_content
        text markdown_content
        text html_content
        varchar status
    }
```

## 兼容性说明

- `Agent.capabilities` 保留 Manifest 快照，`AgentCapability` 是可查询、可治理的规范化绑定。
- `Task.required_capabilities` 默认空数组，旧任务继续按 Phase 1 规则调度。
- Evidence 保留 `html_hash`、`content_hash`、`screenshot_path`，同时增加通用 `evidence_type`、`sha256`、`content_type`、`object_storage_path`。
- Report 保留 JSON/Markdown，并增加 HTML 输出。
