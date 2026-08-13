# Phase 6 Database ER

```mermaid
erDiagram
    TASK ||--|| ASSESSMENT_TASK : extends
    ASSESSMENT_PLUGIN ||--o{ ASSESSMENT_CAPABILITY : provides
    CAPABILITY ||--o{ ASSESSMENT_CAPABILITY : governs
    ASSESSMENT_PLUGIN ||--o{ ASSESSMENT_TASK : selected_for
    ASSESSMENT_TASK ||--o{ FINDING : produces
    FINDING o|--o{ FINDING : duplicate_of
    FINDING ||--o{ FINDING_REFERENCE : references
    FINDING ||--o{ FINDING_EVIDENCE : supports
    EVIDENCE ||--o{ FINDING_EVIDENCE : captured_as
    FINDING ||--o{ FINDING_KNOWLEDGE : explains
    KNOWLEDGE ||--o{ FINDING_KNOWLEDGE : identifies
    KNOWLEDGE_VERSION ||--o{ FINDING_KNOWLEDGE : pins
    FINDING ||--o{ FINDING_ASSET : affects
    ASSET ||--o{ FINDING_ASSET : impacted

    ASSESSMENT_TASK {
      uuid id PK
      uuid task_id FK UK
      uuid plugin_id FK
      string status
      json requested_capabilities
      json policy
      json plan
      json result_summary
      datetime started_at
      datetime finished_at
    }
    FINDING {
      uuid id PK
      uuid assessment_task_id FK
      uuid duplicate_of_id FK
      string fingerprint
      string title
      string severity
      string confidence
      text description
      text affected_asset
      string plugin
      string tool
      string rule
      string risk_level
      float risk_score
      string status
      json attributes
    }
    FINDING_KNOWLEDGE {
      uuid finding_id FK
      uuid knowledge_id FK
      uuid knowledge_version_id FK
    }
```
