# Phase 7 Database ER

```mermaid
erDiagram
    TASK ||--|| ASSESSMENT_TASK : extends
    ASSESSMENT_PLUGIN ||--o{ ASSESSMENT_TASK : executes
    ASSET ||--o{ ASSESSMENT_TASK : scopes
    ASSESSMENT_TASK ||--o{ FINDING : produces
    FINDING ||--o{ FINDING_HISTORY : snapshots
    FINDING ||--o{ FINDING_COMMENT : discusses
    FINDING ||--o{ FINDING_TRANSITION : transitions
    ASSESSMENT_TASK ||--|| ASSESSMENT_REPORT : aggregates
    ASSESSMENT_PLUGIN ||--o{ ASSESSMENT_REPORT : identifies
    ASSET ||--o{ ASSESSMENT_REPORT : scopes
    FINDING ||--o{ FINDING_KNOWLEDGE : maps
    KNOWLEDGE ||--o{ FINDING_KNOWLEDGE : explains
    KNOWLEDGE_VERSION ||--o{ FINDING_KNOWLEDGE : pins
    FINDING ||--o{ FINDING_EVIDENCE : supports
    EVIDENCE ||--o{ FINDING_EVIDENCE : captures

    FINDING {
      uuid id PK
      uuid assessment_task_id FK
      uuid duplicate_of_id FK
      string fingerprint
      string severity
      string confidence
      string risk_level
      float risk_score
      string status
      json attributes
    }
    FINDING_HISTORY {
      uuid id PK
      uuid finding_id FK
      string actor
      string action
      string from_status
      string to_status
      text reason
      json snapshot
    }
    FINDING_COMMENT {
      uuid id PK
      uuid finding_id FK
      string author
      text body
    }
    FINDING_TRANSITION {
      uuid id PK
      uuid finding_id FK
      string from_status
      string to_status
      string actor
      text reason
      string trace_id
    }
    ASSESSMENT_REPORT {
      uuid id PK
      uuid assessment_task_id FK UK
      uuid plugin_id FK
      uuid asset_id FK
      string trace_id
      string status
      json summary
      json content
    }
```

## Phase 7 changes

- Finding status constraint migrates legacy `OPEN`, `MITIGATED`, and `ACCEPTED` values to the explicit lifecycle.
- `finding_history` is append-only change provenance.
- `finding_transitions` stores accepted state transitions and trace identity.
- `finding_comments` reserves human triage collaboration without coupling it to transitions.
- `assessment_reports` stores one immutable platform aggregation per AssessmentTask.
- Migration head: `20260731_0010`; downgrade maps new states back to the Phase 6 set before restoring the old constraint.
