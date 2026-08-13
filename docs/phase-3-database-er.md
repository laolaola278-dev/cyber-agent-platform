# Phase 3 Database ER

```mermaid
erDiagram
    WORKFLOW_DEFINITION ||--o{ WORKFLOW_INSTANCE : instantiates
    WORKFLOW_INSTANCE ||--o{ WORKFLOW_STEP : checkpoints
    WORKFLOW_INSTANCE ||--o{ WORKFLOW_EXECUTION : records
    WORKFLOW_STEP ||--o{ WORKFLOW_EXECUTION : attempts
    TASK ||--o{ TASK_EXECUTION : dispatches
    AGENT ||--o{ TASK_EXECUTION : executes
    AGENT ||--o{ AGENT_CAPABILITY : exposes
    CAPABILITY ||--o{ AGENT_CAPABILITY : binds

    WORKFLOW_DEFINITION {
        uuid id PK
        string name
        string version
        text source_yaml
        json definition
        bool enabled
    }
    WORKFLOW_INSTANCE {
        uuid id PK
        uuid definition_id FK
        string status
        json input
        json context
        string current_node
        string trace_id
        bool cancel_requested
        datetime started_at
        datetime completed_at
        text error
    }
    WORKFLOW_STEP {
        uuid id PK
        uuid instance_id FK
        string node_id
        string node_type
        string capability
        string status
        int attempt
        int max_attempts
        int timeout_seconds
        json input
        json output
        text error
    }
    WORKFLOW_EXECUTION {
        uuid id PK
        uuid instance_id FK
        uuid step_id FK
        int attempt
        string status
        int duration_ms
        json output
        text error
    }
```

`WorkflowStep(instance_id, node_id)` 唯一，保存节点最新 checkpoint；`WorkflowExecution` 追加保存每次 attempt。Workflow 与 Task 不建立永久外键，因为 Workflow Step 通过 Capability 动态生成 Task，Agent 选择属于每次调度历史而非 Definition 静态结构。
