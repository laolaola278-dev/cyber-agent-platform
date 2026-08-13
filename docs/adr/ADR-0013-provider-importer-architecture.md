# ADR-0013: Use Provider plus Importer Architecture

## Status

Accepted for Phase 5.

## Context

Knowledge sources differ in transport, authority, schema, release cadence, and packaging. Formats include JSON, YAML, CSV, ZIP, STIX collections, and source-specific containers. Allowing each integration to write ORM models would bypass identity, validation, version, relationship, and audit invariants.

## Decision

A Provider owns external retrieval and source-specific parsing and yields provider-neutral KnowledgeRecord objects. An Importer plugin decodes an input format. KnowledgeImporter is the only orchestrator allowed to canonicalize, create immutable versions, resolve relationships, commit, and audit.

CVEProvider, AttackProvider, KEVProvider, and VendorProvider are extension protocols. JSON is the first importer. New providers/importers register at runtime and do not modify KnowledgeImporter core.

## Consequences

Positive: Open/Closed compliance, isolated network boundaries, uniform validation, testable transactions, non-overwriting history, and reusable format support.

Negative: providers must normalize richer source structures without losing raw payload provenance, and asynchronous bulk feeds will later need batching, checkpointing, size limits, and retry policy.
