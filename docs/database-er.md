# CAP Phase 1 数据库 ER 图

```mermaid
erDiagram
    AGENT ||--o{ AGENT_VERSION : versions
    AGENT ||--o{ AGENT_HEARTBEAT : heartbeats
    AGENT ||--o{ TASK_EXECUTION : executes
    TOOL ||--o{ TOOL_VERSION : versions
    TASK ||--o{ TASK_EXECUTION : executions
    TASK ||--o{ TASK_LOG : logs
    TASK_EXECUTION ||--o{ EXECUTION_LOG : logs

    AGENT {
        uuid id PK
        varchar name UK
        varchar version
        text description
        varchar author
        json permissions
        json tools
        json runtime
        json network_policy
        json resource_limit
        json approval_policy
        varchar status
        varchar health_status
        timestamptz heartbeat_time
        timestamptz created_at
        timestamptz updated_at
    }
    AGENT_VERSION {
        uuid id PK
        uuid agent_id FK
        varchar version
        json manifest
        boolean is_active
        timestamptz created_at
        timestamptz updated_at
    }
    AGENT_HEARTBEAT {
        uuid id PK
        uuid agent_id FK
        varchar health_status
        json details
        timestamptz timestamp
    }
    TOOL {
        uuid id PK
        varchar name UK
        varchar version
        varchar tool_type
        text description
        json required_permissions
        json config_schema
        json runtime_requirements
        varchar status
        timestamptz created_at
        timestamptz updated_at
    }
    TOOL_VERSION {
        uuid id PK
        uuid tool_id FK
        varchar version
        json manifest
        boolean is_active
        timestamptz created_at
        timestamptz updated_at
    }
    TASK {
        uuid id PK
        varchar name
        varchar task_type
        varchar status
        json input
        json required_permissions
        uuid target_agent_id
        timestamptz created_at
        timestamptz updated_at
    }
    TASK_EXECUTION {
        uuid id PK
        uuid task_id FK
        uuid agent_id FK
        varchar status
        varchar trace_id
        timestamptz start_time
        timestamptz end_time
        json result
        text logs
    }
    TASK_LOG {
        uuid id PK
        uuid task_id FK
        varchar level
        text message
        varchar trace_id
    }
    EXECUTION_LOG {
        uuid id PK
        uuid execution_id FK
        varchar level
        text message
        timestamptz timestamp
    }
    AUDIT_LOG {
        uuid id PK
        varchar operator
        varchar action
        varchar resource
        json details
        timestamptz timestamp
    }
```

## 约束说明

- `Agent.name` 与 `Tool.name` 是稳定唯一身份；当前版本在主表，历史 Manifest 追加至 Version 表。
- `(agent_id, version)` 与 `(tool_id, version)` 唯一。
- Task 删除时级联 TaskExecution/TaskLog/ExecutionLog；被执行记录引用的 Agent 限制删除。
- Heartbeat 是追加记录，同时更新 Agent 当前健康快照。
- `target_agent_id` 在 Phase 1 是可选调度偏好，未设数据库外键，以便未来支持逻辑 Agent 池；由 Service 校验实际 Agent。
