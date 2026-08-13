# Synthetic Response Plugin

`fake-response` is a non-destructive certification plugin for Phase 14. It validates the full Response lifecycle without invoking a firewall, WAF, EDR, shell, endpoint, network API, or arbitrary file operation.

The plugin receives only a least-privilege `ResponsePluginContext`, returns only `ResponseResult`, supports approval-governed execution, emits bounded hash-addressed evidence receipts, and supports a synthetic verified rollback.
