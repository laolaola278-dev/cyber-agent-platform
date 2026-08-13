# CAP Python SDK Guide

The standalone SDK is in `sdk/python` and is versioned `1.0.0-rc1`. It provides Agent base contracts and tool adapters without importing Backend persistence or service implementations.

```bash
python -m pip install ./sdk/python
```

The package metadata is maintained in `sdk/python/pyproject.toml`; the package name is `cap-agent-sdk` and its import namespace is `cap_agent_sdk`. The SDK is intentionally independent from `backend/app`: do not install it by adding the Backend source directory to `PYTHONPATH`. For a local editable development install, use `python -m pip install -e ./sdk/python` in an isolated virtual environment.

Implement an Agent by extending `BaseAgent`, declaring deterministic metadata/capabilities, and returning SDK contract models. Tool access must use `ToolAdapter`; direct platform database access and embedded secrets are prohibited.

SDK compatibility follows the v1 API freeze. Compatible additions require a MINOR release after 1.0.0; incompatible contract changes require a MAJOR release. Validate SDK code against the target CAP RC before production deployment.
