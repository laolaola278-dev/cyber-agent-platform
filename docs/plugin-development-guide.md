# Plugin Development Guide

CAP Plugins declare capabilities through versioned YAML Manifests and execute behind Platform interfaces. A Plugin must not access platform database sessions directly, bypass Sandbox/approval, or create an alternative Plane.

## Required workflow

1. Select an existing capability domain and Provider interface.
2. Define a versioned Manifest with deterministic identifiers, inputs, outputs, timeout, network, and secret requirements.
3. Implement the Provider Adapter behind the existing interface.
4. Use Sandbox and Secret Provider contracts; never embed credentials.
5. Fail closed on timeout, cancellation, malformed output, or unavailable dependency.
6. Add unit, contract, safety, rollback/idempotency, and audit tests.
7. Document production dependencies and limitations in the Plugin README.

Phase 23 freezes the existing Plugin inventory and does not accept new Plugins. Changes to Manifest or capability compatibility follow `docs/api-freeze-v1.md`.
