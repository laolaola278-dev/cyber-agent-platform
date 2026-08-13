# Contributing to CAP

CAP v1 is under API and feature freeze. Contributions to `release/1.0` are limited to reviewed bug fixes, release engineering, production certification, documentation, packaging, and deployment changes.

## Required checks

```bash
uv sync --project backend --extra dev --frozen
uv run --project backend ruff check backend/app backend/tests benchmarks/phase22
uv run --project backend pytest backend/tests -p no:cacheprovider
npm ci --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
```

A change to the documented v1 API, database models, migrations, platform planes, or plugin inventory requires Architect approval and the versioning process in `docs/api-freeze-v1.md`. Never commit secrets, generated `.env`, coverage data, build outputs, or credentials.
