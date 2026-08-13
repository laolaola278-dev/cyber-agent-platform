# ADR-0016: Nuclei Is the First Real Assessment Plugin

- Status: Accepted
- Date: 2026-07-30

## Context

CAP's Phase 6 Assessment Framework had been validated only with a deterministic Fake Plugin. The first real tool must exercise Plugin lifecycle, capability policy, target governance, output normalization, deduplication, Knowledge mapping, reporting, and audit without forcing scanner concepts into the core.

Nuclei is template-driven, supports deterministic machine-readable JSONL, exposes stable template and matcher identities, and is widely used as a CLI integration. Its flexibility also creates a meaningful template and process trust boundary, making it a strong test of CAP's governance architecture.

## Decision

Integrate ProjectDiscovery Nuclei as the first real Assessment Plugin through a strict Plugin -> Adapter -> Sandbox boundary. Nuclei remains an external executable. CAP selects Asset-derived targets and approved local templates, while the platform owns policy, normalization, Finding persistence, lifecycle, Knowledge association, report generation, and audit.

ProjectDiscovery httpx is not integrated in this phase. Target discovery remains separate from template execution.

## Consequences

- The existing six-stage Assessment Plugin lifecycle is validated without redesign.
- JSONL is the stable anti-corruption boundary.
- Nuclei templates require local approval, integrity hashes, and request budgets.
- Nuclei-specific fields remain in Finding attributes/evidence, not core columns.
- Runtime availability of the Nuclei binary remains an operator responsibility.
- Additional scanners can reuse the same Adapter/Sandbox/Normalizer pattern.
