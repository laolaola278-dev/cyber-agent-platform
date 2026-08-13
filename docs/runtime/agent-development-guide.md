# Agent Development Guide

1. Add a trusted `manifest.yaml` with stable name, version, permissions, tools, runtime entrypoint, and policy metadata.
2. Implement `BaseAgent.initialize`, `execute`, `health_check`, and `shutdown`.
3. Use only RuntimeContext capabilities: logger, typed configuration, EventPublisher, Tool Adapter, EvidenceService, and ReportService.
4. Do not import repositories, database sessions, Dispatcher, or Playwright directly.
5. Keep permissions least-privileged and declare no capabilities not used by the Agent.

The Phase 2 Data Acquisition Agent is intentionally limited to one public HTTP(S) GET capture through Playwright. Login, cookie injection, bypass behavior, OCR, document extraction, Scrapy, and Crawl4AI are excluded.
