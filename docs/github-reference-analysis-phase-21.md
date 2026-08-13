# Phase 21 Architecture Benchmark

## Grafana

采纳 Dashboard/Panel 的组合模型、数据源解耦和 provisioning-as-code；CAP 提供只读 Overview Dashboard JSON。未采纳 Grafana Plugin Runtime、多组织和复杂权限树，避免把 Console 变成第二套 Grafana。

## Prometheus

采纳 Counter/Gauge/Histogram、抓取式 `/metrics`、录制前置的低基数 Label 和声明式 Alert Rule。HTTP 仅使用 method、route template、status class；禁止 User ID、Incident ID、Trace ID 作为 Label。

## OpenTelemetry

采纳 Trace、Server Span、Context propagation、W3C `traceparent` 和 `service.name` Resource。Trace ID 同时进入响应头和结构化日志。Exporter 可选配置，未配置时不阻塞应用。

## Backstage

采纳统一门户的信息架构与按能力分区导航；Console 作为既有领域服务的聚合视图。未采纳 Backstage Backend/Plugin API，避免新增 Platform Plane 或重复 Plugin 系统。

## Keycloak

采纳 User → Role → Permission 的聚合模型和后端授权原则。Phase 21 使用本地不可变身份目录；生产身份验证委托给可信 OIDC/反向代理，CAP 不在浏览器中持有代理密钥。未引入 Keycloak Authorization Services 的资源策略引擎和管理 API。

## CAP 最终选择

- Console：API-backed read projection，不直连数据库。
- RBAC：固定五角色、`resource.action` 权限、默认拒绝。
- Identity：可信代理注入，客户端头被覆盖。
- Metrics：低基数 Prometheus exposition + rules。
- Trace：OpenTelemetry SDK + W3C context + structured logs。
- Release：Docker Compose profile、Grafana/Prometheus 声明式配置。

官方参考：

- https://grafana.com/docs/grafana/latest/introduction/
- https://prometheus.io/docs/practices/instrumentation/
- https://prometheus.io/docs/practices/naming/
- https://opentelemetry.io/docs/concepts/signals/traces/
- https://backstage.io/docs/plugins/
- https://www.keycloak.org/docs/latest/authorization_services/
