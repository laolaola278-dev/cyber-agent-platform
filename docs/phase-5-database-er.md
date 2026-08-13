# Phase 5 Database ER

```mermaid
erDiagram
    KNOWLEDGE_SOURCE ||--o{ KNOWLEDGE : owns
    KNOWLEDGE ||--o{ KNOWLEDGE_VERSION : versions
    KNOWLEDGE ||--o{ KNOWLEDGE_RELATION : source
    KNOWLEDGE ||--o{ KNOWLEDGE_RELATION : target
    ASSET ||--o{ ASSET_KNOWLEDGE : references
    EVIDENCE ||--o{ EVIDENCE_KNOWLEDGE : supports
    REPORT ||--o{ REPORT_KNOWLEDGE : freezes
    KNOWLEDGE ||--o{ ASSET_KNOWLEDGE : linked
    KNOWLEDGE ||--o{ EVIDENCE_KNOWLEDGE : linked
    KNOWLEDGE ||--o{ REPORT_KNOWLEDGE : linked
    KNOWLEDGE_VERSION ||--o{ ASSET_KNOWLEDGE : pinned
    KNOWLEDGE_VERSION ||--o{ EVIDENCE_KNOWLEDGE : pinned
    KNOWLEDGE_VERSION ||--o{ REPORT_KNOWLEDGE : pinned

    KNOWLEDGE_SOURCE {
        uuid id PK
        string name UK
        string provider_type
        text base_url
        boolean enabled
        json configuration
    }
    KNOWLEDGE {
        uuid id PK
        uuid source_id FK
        string knowledge_type
        string external_id
        string current_version
        string current_content_hash
        string title
        text description
        json references
        string status
        json attributes
    }
    KNOWLEDGE_VERSION {
        uuid id PK
        uuid knowledge_id FK
        string version
        string content_hash
        json payload
        datetime source_updated_at
        datetime imported_at
    }
    KNOWLEDGE_RELATION {
        uuid id PK
        uuid source_knowledge_id FK
        uuid target_knowledge_id FK
        string relation_type
        string source_name
        json properties
    }
    ASSET_KNOWLEDGE {
        uuid asset_id FK
        uuid knowledge_id FK
        uuid knowledge_version_id FK
    }
    EVIDENCE_KNOWLEDGE {
        uuid evidence_id FK
        uuid knowledge_id FK
        uuid knowledge_version_id FK
    }
    REPORT_KNOWLEDGE {
        uuid report_id FK
        uuid knowledge_id FK
        uuid knowledge_version_id FK
    }
```

Core uniqueness:

- Knowledge: `(source_id, knowledge_type, external_id)`
- Version snapshot: `(knowledge_id, version, content_hash)`
- Relation: `(source_knowledge_id, target_knowledge_id, relation_type)`
- Cross-domain links: `(owner_id, knowledge_id)`
