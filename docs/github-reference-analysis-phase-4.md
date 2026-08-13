# GitHub Reference Analysis — Phase 4

## 分析目标

Phase 4 只研究成熟项目如何表达实体、关系、生命周期、API、扩展机制和数据来源，不复制其完整业务系统。CAP 的目标是建立供 Agent、Workflow、Task、Evidence 与 Report 共用的统一 Asset Center。

## Wazuh

- 官方项目：<https://github.com/wazuh/wazuh>
- Entity：受管 Agent、端点、组、清单与安全事件。Agent 是持续注册并上报端点状态和 inventory 的受管实体。
- Relationship：Manager 聚合 Agent 数据；Agent 与端点清单、安全事件及策略状态关联。
- 生命周期：注册、连接/心跳、清单同步、断连或移除；状态变化由管理面统一观察。
- API 与扩展：通过 Wazuh API 和模块化集成访问 Agent、配置与安全数据。
- 数据模型启示：采集端产生的数据必须汇聚到平台治理实体，不能由每个采集器维护独立资产副本。
- CAP 借鉴：Agent 引用型 Asset、统一 inventory 汇聚、来源可追踪、状态与资产身份分离。
- CAP 不复制：SIEM/XDR 引擎、端点管理协议、规则集和完整事件索引平台。

## OpenCTI

- 官方项目：<https://github.com/OpenCTI-Platform/opencti>
- Entity：知识实体、观测对象、报告、身份、基础设施等；关系是一等对象，而非嵌套字段。
- Relationship：显式、有类型、可附带来源与时间信息，适合表达知识图谱。
- 生命周期：数据由 Connector 导入、归一化、关联、更新并通过来源信息保持可追溯性。
- API 与扩展：以 GraphQL API 为主要访问边界；Connector 负责外部数据交换和处理。
- 数据模型启示：实体身份、关系和 provenance 应分离建模，关系需要独立唯一性和属性。
- CAP 借鉴：有向类型关系、显式关联表、Evidence/Report provenance、未来图投影能力。
- CAP 不复制：STIX 全模型、CTI 知识本体、推理规则、Connector 生态和情报运营界面。

## DefectDojo

- 官方项目：<https://github.com/DefectDojo/django-DefectDojo>
- Entity：Product、Engagement、Test、Finding 等组成分层漏洞管理上下文。
- Relationship：Finding 归属于 Test，Test 归属于 Engagement，Engagement 归属于 Product；导入数据被绑定到稳定业务对象。
- 生命周期：导入、去重、分诊、风险接受、关闭与再发现等状态变化围绕 Finding 治理。
- API 与扩展：REST API 支持对象管理；大量 Parser/Importer 对接扫描器输出。
- 数据模型启示：外部工具结果不应直接成为平台资产身份；必须先规范化、去重，再关联稳定对象。
- CAP 借鉴：导入边界、规范化与去重、业务上下文、结果到资产的显式关联。
- CAP 不复制：漏洞工单生命周期、Finding 业务状态机、扫描器 Parser 全生态和产品治理界面。

## TheHive

- 官方项目：<https://github.com/TheHive-Project/TheHive>
- Entity：Case、Alert、Observable、Task 等支撑安全事件调查。
- Relationship：Observable 可关联 Alert/Case，并作为 Analyzer 的输入；调查产物围绕 Case 聚合。
- 生命周期：Alert 进入后可分诊、合并或提升为 Case；Observable 在调查过程中被丰富和分析。
- API 与扩展：API 提供案例与可观测对象管理；Cortex 生态承载 Analyzer/Responder 扩展。
- 数据模型启示：可观测对象需要稳定身份，分析结果和调查上下文应通过关系关联，而不是覆盖原始对象。
- CAP 借鉴：Observable 式资产关联、Evidence 保持不可变、分析结果与资产分离、插件化执行。
- CAP 不复制：Case Management、SOC 协作流程、Cortex 调度协议和 Responders 生态。

## NetBox

- 官方项目：<https://github.com/netbox-community/netbox>
- Entity：站点、设备、虚拟机、接口、IP 地址、前缀、电路等基础设施对象。
- Relationship：对象之间通过明确外键和层级关系构成基础设施拓扑，并以数据库作为 Source of Truth。
- 生命周期：对象 CRUD、校验、变更日志与权限治理集中在平台服务边界。
- API 与扩展：提供 REST 与 GraphQL API；插件可扩展模型、视图、API、导航和后台任务。
- 数据模型启示：统一权威数据源、稳定对象 ID、显式关系、变更治理和插件边界比工具私有清单更可靠。
- CAP 借鉴：Source of Truth、关系数据库约束、统一 API、软删除治理和插件优先原则。
- CAP 不复制：完整 DCIM/IPAM 模型、机架/布线/电路领域、NetBox 插件运行时和 UI。

## 综合决策

CAP 采用以下组合模式：

1. 以 NetBox 的 Source of Truth 思想建立统一 Asset 身份；
2. 以 OpenCTI 的显式关系和来源追踪表达 Asset Graph；
3. 以 DefectDojo 的导入规范化与去重约束外部工具结果；
4. 以 TheHive 的 Observable 关联方式连接 Evidence、Report 与调查上下文；
5. 以 Wazuh 的 Agent/Inventory 汇聚方式让采集端只生产事实，不持有平台级资产主数据。

Phase 4 仍采用 PostgreSQL 关系模型。图遍历需求增长后，可从 `assets` 与 `asset_relations` 构建异步图投影；关系数据库继续保留事务性 Source of Truth，图数据库不直接接受 Agent 写入。
