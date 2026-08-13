# Agent Manifest

Every trusted Agent directory contains `manifest.yaml`:

```yaml
name: data-acquisition-agent
version: 1.0.0
minimum_runtime_version: 1.0.0
platform_version: 0.2.1
sdk_version: 1.0.0
permissions: [crawl.public, tool.playwright, evidence.write, report.write]
capabilities: [crawl.html, browser.render, evidence.generate]
tools: [playwright]
runtime:
  entrypoint: agent:DataAcquisitionAgent
network_policy:
  allowed_methods: [GET]
  public_web_only: true
```

ManifestLoader parses YAML, validates it with Pydantic, translates it into the Registry contract, and supports version updates. RuntimeService discovers matching Agent names only under platform-configured trusted manifest directories. Registration synchronizes capability bindings; runtime/platform/SDK version fields are persisted for compatibility governance.
