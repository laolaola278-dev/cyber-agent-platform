# CAP v1 API Freeze and Breaking Change Policy

## Frozen public API

As of `1.0.0-rc1`, the public API is the generated OpenAPI contract containing 124 operations plus documented SDK and Plugin Manifest contracts. The freeze covers paths, HTTP methods, operation semantics, request and response schemas, required fields, enum values, status/error meaning, authorization requirements, Playbook DSL `v1`, and Plugin/Provider compatibility contracts.

## Allowed during RC

- Correct an implementation that violates the existing contract.
- Add tests, documentation, packaging, deployment controls, or observability that do not change public behavior.
- Clarify descriptions without changing schema or semantics.
- Apply security fixes; if compatibility changes, issue a new RC and document impact.

## Breaking changes

Removing or renaming a path, method, field, enum, capability, or error behavior; making an optional field required; narrowing accepted input; weakening or changing authorization semantics; or changing durable DSL/Manifest meaning is breaking.

After `1.0.0`, incompatible changes require the next MAJOR version. Compatible additions require MINOR; compatible bug fixes require PATCH. Deprecation requires documentation and at least one compatible MINOR release before removal. Published artifacts are immutable.

## Review procedure

1. Generate OpenAPI before and after the change.
2. Review semantic differences, not only textual differences.
3. Obtain Architect and Security approval for any public contract change.
4. Update CHANGELOG, release notes, SDK/Plugin compatibility, and migration guidance.
5. Issue a new version; never overwrite an existing tag, image, Chart, or release asset.
