# Cyber Agent Platform（CAP）企业级架构全景介绍

## ——从 Principal Security Architect 视角对平台定位、设计、工程与演进的系统说明

> 本文不是产品宣传稿，也不是 README 式功能清单。本文尝试回答一个更基础的问题：Cyber Agent Platform（CAP）到底是什么类型的软件系统，它为什么需要被设计成一个平台，它的架构边界在哪里，它相对于传统安全工具、SOC、SOAR、SIEM、脚本自动化和 AI Agent 的根本差异是什么。

> 文中关于当前实现的描述以 CAP v1.0.0-rc1 的代码、接口、插件、部署资产、测试和 Phase 19 至 Phase 24 交付文档为基准。需要特别强调：当前版本是 Release Candidate，不等同于已经完成生产认证的 v1.0.0 GA。Phase 24 认证报告已经明确记录，真实 PostgreSQL、Docker、Helm、外部压力、Soak、镜像/SBOM 和目标环境恢复证据仍是正式发布门禁。

---

## 一、项目为什么存在：CAP 要解决的不是“缺少一个扫描器”

### 1.1 安全运营系统的真正问题是能力碎片化

企业安全建设通常不是从零开始。一个组织可能已经拥有资产管理系统、漏洞扫描器、网络检测系统、终端检测平台、工单系统、消息通知系统、云平台、身份系统、日志平台和若干自动化脚本。问题在于，这些系统通常各自拥有自己的对象模型、执行方式、权限体系和审计方式。

扫描器有自己的 Target、Template、Finding；网络检测系统有自己的 Alert、Flow、Rule、Event；终端平台有自己的 Host、Process、Response Action；工单系统有 Ticket 和 Workflow；SOAR 平台有 Playbook 和 Action；SIEM 有 Event、Correlation 和 Rule；脚本则往往只有一个入口函数和一份日志。它们可以分别工作，却难以自然地组成一个边界清晰、责任明确、可以审计和回滚的安全运营闭环。

企业因此经常出现以下问题：

1. 同一个资产在不同系统中使用不同标识，导致漏洞、检测告警、事件和处置动作无法稳定关联。
2. 同一个安全事件在多个工具中重复建模，结果是告警、事件、工单和证据之间无法区分。
3. 工具集成直接写在业务服务中，新增一个厂商或替换一个工具就需要修改核心代码。


4. 自动化脚本可以执行动作，却无法证明谁批准了动作、动作针对什么对象、动作使用了什么凭据、动作是否成功、失败后如何补偿。
5. Agent 能够理解上下文并调用工具，却经常越过平台边界直接访问数据库、读取密钥或执行没有审计的外部动作。
6. 只要某一个工具接口发生变化，就会把变化传播到 API、工作流、数据库、前端和报告层。
7. 系统可以“跑起来”，但不能回答企业最关心的问题：这个结果是否可信，证据是否完整，处置是否经过授权，错误是否能被限制在安全边界内。

CAP 的出发点，就是把问题从“如何再接入一个安全工具”提升为“如何治理一组可组合的安全能力”。

### 1.2 传统 SOC 的局限

传统 SOC 通常以人员、流程、日志和工具集合为核心。它能够汇总告警、建立值班流程和进行人工调查，但很多 SOC 的技术底座仍然是多个系统的拼接，而不是一个统一的能力控制平面。

传统 SOC 的主要局限不是没有数据，而是缺少结构化的能力治理：

- 资产与事件的身份关联往往依赖规则、字段约定或人工经验。
- 告警聚合和事件处置之间缺少统一的领域语义。
- 处置动作可能由不同系统分别执行，审批和审计链条不一致。
- 工具接入的质量取决于集成工程师，而不是平台级接口合同。
- 一些 SOC 能做集中展示，却不能对 Worker、Sandbox、Secret、Rollback 和 Provider 的行为建立统一控制。
- 人工调查的知识难以沉淀为可复用的知识对象和证据链。

CAP 不是要替代整个 SOC 的人员组织、管理制度和所有现有安全产品。它更适合作为 SOC 的能力治理与执行编排层：把异构能力放在统一的接口、执行、安全、审计和证据框架下，同时保留外部产品作为 Provider 或数据源。

### 1.3 传统 SOAR 的局限

SOAR 的核心价值是 Playbook 编排、Action 执行和工单联动。但传统 SOAR 实现常见几个问题：

第一，Playbook 只能编排已经被平台以特定方式包装的 Action。若底层 Action 没有统一的能力模型，Playbook 很容易退化为脚本调用器。

第二，Action 的安全语义经常不够明确。比如“封禁 IP”“隔离终端”“修改 WAF 规则”都属于高影响动作，但不同 Action 是否需要审批、是否支持回滚、是否具有幂等性、失败后如何重试，可能没有一致的合同。

第三，SOAR 中的上下文通常以一次 Playbook 执行的变量形式存在，难以与统一资产、知识、证据、Finding、SecurityEvent 和 Incident 建立长期关系。

第四，部分 SOAR 依赖低代码配置和脚本扩展，短期提高了接入速度，长期却可能形成隐式耦合：业务人员能拖拽节点，但系统不一定能够静态验证权限、数据流向、超时、补偿链和证据要求。

CAP 对 Playbook 的定位不是“替代一段脚本的流程图”，而是将 Playbook 视为平台能力的声明式编排层。Playbook 只能调用已经被治理的 Capability，执行由 Runtime、Worker、Sandbox、Approval、Audit 和 Evidence 共同控制。这样做的结果是：编排层不再直接拥有任意执行权，安全边界位于平台能力合同，而不是位于流程图的外观。

### 1.4 漏洞扫描器的局限

Nuclei、ZAP 或其他漏洞扫描器可以在特定任务上非常有效，但它们解决的是检测问题中的一部分，不是企业安全运营的完整问题。

扫描器通常擅长：

- 根据 Target 和规则执行探测；
- 输出漏洞或风险发现；
- 提供原始请求、响应、规则和严重性信息；
- 在某些场景中支持模板和自动化扫描。

但扫描器通常不负责：

- 资产全生命周期管理；
- 多源知识统一；
- 检测结果与真实安全事件的区分；
- Incident 状态和责任归属；
- 高影响 Response 的审批、回滚和验证；
- 跨工具的证据血缘；
- 企业级 RBAC、Worker 租约、Sandbox 和审计一致性。

因此 CAP 不把扫描器“升级”为平台，而是把扫描器放在 Assessment Plugin 或 Provider Adapter 边界后面。扫描器仍然负责它最擅长的探测，CAP 负责将探测结果纳入统一的资产、Finding、Knowledge、Evidence 和运营流程。

### 1.5 脚本自动化的局限

脚本的优势是快、直接、灵活；它可以迅速把多个 API 串起来。但脚本的默认语义往往是“执行优先”，不是“治理优先”。典型脚本可能存在以下问题：

- 凭据直接写在环境变量、配置文件或代码中；
- 输入对象缺乏统一校验；
- 没有权限模型，谁能运行脚本取决于机器权限；
- 重试不可控，可能造成重复动作；
- 失败处理停留在异常捕获，缺少补偿或回滚；
- 日志中有时间戳，却没有统一 Request ID、Trace ID、Audit Event 和 Evidence Lineage；
- 脚本升级通常无法证明与旧版本的兼容性；
- 它可以调用数据库和外部工具，平台却无法阻止其绕过审批。

CAP 并不否定脚本。脚本可以被封装为 Provider 或 Sandbox 内的受控实现，但它不再直接代表平台能力。脚本执行必须接受输入、权限、密钥、网络、超时、输出和审计的统一治理。

### 1.6 Agent 和 AI Agent 的局限

Agent 的价值是理解任务、分解问题、选择能力并处理非结构化上下文。AI Agent 还可以通过自然语言推理、知识检索和工具调用减少分析人员的重复劳动。

但 Agent 本身不是安全平台。它可能遇到：

- 工具权限过大；
- 规划结果不可预测；
- 外部动作缺少审批；
- 数据和密钥边界不清；
- 推理链与审计链混淆；
- 失败后无法恢复；
- 状态放在内存中，进程重启后无法继续；
- Agent 直接访问数据库或基础设施，造成架构绕过；
- 生成的计划在语义上正确，但在安全上不一定可执行。

CAP 的重要取舍是：Agent 可以成为专业能力的动态注入者、分析者或规划者，但不应自动成为平台的最终权限中心。Agent 必须通过稳定的 SDK、Capability、ToolAdapter、Runtime 和平台授权边界工作。推理可以辅助决策，平台仍然负责授权、执行、审计、证据和回滚。

### 1.7 CAP 的必要性

CAP 存在的原因不是“把所有安全工具重新开发一遍”，而是建立一个安全能力的控制平面。它把不同工具和 Agent 放到同一套抽象下：

- Asset 说明动作作用于什么对象；
- Capability 说明平台允许做什么；
- Plugin 说明如何提供能力；
- Provider 说明如何连接具体产品或服务；
- Worker 说明由谁执行；
- Sandbox 说明在哪些限制下执行；
- Approval 说明谁批准高影响动作；
- Evidence 说明结果如何被证明；
- Audit 说明系统如何留下不可忽略的责任记录；
- Playbook 说明已治理能力如何组合。

这个控制平面使 CAP 成为平台，而不是工具集合。

---

## 二、整体设计思想：把安全能力变成可治理的长期资产

CAP 的设计原则不是口号，而是用于约束代码依赖、数据边界和运行时行为的工程规则。

### 2.1 Platform First

Platform First 表示平台级治理能力优先于单个领域能力和单个工具集成。一个新的漏洞扫描器、网络检测插件或 EDR Provider 不能因为自身功能强大，就直接决定平台的身份、权限、数据模型或执行方式。

平台首先提供统一的注册、能力发现、运行时、租约、Sandbox、审计、RBAC、观测和证据边界。领域模块在这些基础上提供 Assessment、Detection、Response、Playbook 等专业能力。

采用这一原则的原因是：企业安全平台的长期成本主要来自边界失控，而不是来自少写了一个 API。若每个插件都可以独立拥有数据、权限和执行线程，平台会逐渐变成多个相互依赖的子系统。

### 2.2 Plugin First

Plugin First 表示外部工具的具体差异应尽可能停留在插件、Adapter 和 Provider 中，而不是扩散到平台核心。

新增能力时，优先寻找已有的 Capability 和 Provider Interface。如果 Nuclei、ZAP、Suricata、Zeek、WAF、Firewall 或 EDR 可以实现既有合同，就通过插件接入；只有当平台确实缺少稳定能力抽象时，才讨论是否需要扩展平台接口。

这个原则带来的结果是：核心平台依赖稳定合同，工具适配依赖具体 Provider。平台不是厂商产品的集合，而是厂商产品可以被替换和组合的治理环境。

### 2.3 Capability First

Capability First 是 CAP 的核心抽象之一。平台不是直接暴露“某个工具的某个命令”，而是先定义平台允许的能力，例如 Assessment、Detection、Host Action、Block Indicator、Create Ticket、Send Notification、Run Playbook Step 等。

Capability 需要明确：

- 能力身份和版本；
- 输入模型；
- 输出模型；
- 权限要求；
- 是否涉及副作用；
- 是否需要审批；
- 超时和取消语义；
- 网络和密钥要求；
- 幂等性和重试条件；
- 证据和审计要求。

工具只是实现 Capability 的一种方式。这样 Playbook 和 Agent 面向的是稳定能力，而不是某个工具的私有 API。

### 2.4 Interface First

Interface First 指先定义领域合同、端口和协议，再实现服务、插件和适配器。CAP 使用 SDK contracts、Provider contracts、Sandbox contracts、Worker contracts、Plugin Manifest 和 API schemas 将接口显式化。

接口优先并不是为了写更多抽象，而是为了把变化隔离在边界内。一个具体 Provider 可以失败、超时、降级或替换，但不能改变平台对于请求、结果、错误、审计和权限的基本理解。

### 2.5 Security by Default

Security by Default 表示未配置时采用更安全的行为，而不是更方便的行为。CAP 的实际体现包括：

- 生产环境拒绝仓库占位密钥；
- 生产环境拒绝 Debug；
- API 文档在发布资产中默认关闭；
- 后端 RBAC 权威于前端按钮隐藏；
- 缺少可信身份或代理密钥时默认拒绝；
- Sandbox 网络访问、能力和密钥均需显式声明；
- 高影响动作进入 Approval 流程；
- 插件不能直接访问平台数据库；
- Worker 使用 Lease 和 Fencing 防止旧执行者继续写入；
- Provider 输出不符合合同或依赖不可用时 fail closed。

企业系统不能把“开发环境方便”直接当作“生产环境合理”。安全默认值是减少配置漂移和人为疏忽的重要手段。

### 2.6 Audit Everything

Audit Everything 不是把所有日志都保存下来，而是对具有责任意义的行为形成结构化、可关联、可检索的审计事件，包括：

- 谁发起了请求；
- 通过什么身份验证；
- 调用了什么能力；
- 作用于哪个资源；
- 使用了哪个插件、Provider 和 Worker；
- 是否经过审批；
- 结果是什么；
- 是否产生了证据；
- 是否执行回滚或补偿；
- 失败、超时和重试发生在何处。

运行日志解决故障排查，Audit 解决责任追踪。两者不能简单等同。

### 2.7 Configuration First

Configuration First 表示部署参数、Provider 参数、Sandbox Profile、密钥引用、RBAC 边界和可观测性开关通过配置表达，而不是硬编码进业务逻辑。配置不是为了无限增加选项，而是为了让环境差异显式化、可审计和可验证。

CAP 的 Compose、Helm、`.env.example`、values schema、Plugin Manifest 和 Settings projection 都体现了这一思想。生产部署不应依赖工程师记住某段隐藏命令。

### 2.8 Source of Truth

CAP 将 PostgreSQL 作为持久状态的权威来源。Worker、Runtime、Redis 和缓存可以提供协调、队列或暂态性能支持，但不能代替数据库成为最终状态来源。

这一原则尤其重要于：

- Worker Lease 和执行状态；
- Playbook Execution；
- Approval；
- Incident 状态；
- Evidence 记录；
- 审计事件；
- 迁移版本。

没有 Source of Truth 的系统，在发生重启、网络分区、Worker 重复执行或人工介入后，很难判断真实状态。

### 2.9 Worker + Sandbox

Worker 负责承接异步、耗时、可重试和可能产生副作用的执行任务；Sandbox 负责限制执行环境。两者共同解决“平台 API 不应直接执行一切”的问题。

Worker 需要有心跳、租约、状态转换、容量和 Fencing。Sandbox 需要有网络、超时、进程终止、密钥和资源策略。把两者放在平台层，是为了让 Nuclei、ZAP、EDR 或网络检测插件具有统一的执行安全模型。

### 2.10 Approval + Rollback

企业安全响应的难点通常不是能不能调用 API，而是何时可以调用、谁可以批准、失败后如何恢复。例如隔离主机、封禁 IP、修改 WAF、阻断防火墙策略都可能影响业务。

CAP 将 Approval、Policy、Verification、Evidence 和 Rollback 作为响应域的组成部分。审批不是前端按钮，而是后端的授权和持久状态；回滚不是一句“撤销”，而是需要知道原始状态、动作结果、补偿能力和验证结果。

---

## 三、整体架构：五个逻辑平面与一个插件生态

CAP 的架构可以从五个逻辑平面理解。它们是职责上的边界，不一定等价于五个独立部署集群。

### 3.1 Platform Plane

Platform Plane 是整个系统的控制平面，负责提供所有领域共同需要的基础能力：

- Registry：注册 Agent、Tool、Capability、Plugin 和 Provider；
- Runtime：加载 Manifest、解析依赖、建立执行上下文；
- RBAC：认证后的身份、角色和权限判断；
- Worker：任务领取、租约、心跳、重试和执行记录；
- Sandbox：进程、网络、超时、密钥和资源限制；
- Audit：责任事件和审计写入；
- Observability：Metrics、Trace、Structured Logging；
- Source of Truth：持久状态和迁移版本；
- API：对外提供稳定的 HTTP 合同。

Platform Plane 不应该知道某个 WAF 厂商的私有字段，也不应该直接编排一个具体命令。它治理能力，而不是拥有每个工具的业务细节。

### 3.2 Knowledge Plane

Knowledge Plane 负责将安全知识从一次执行结果中分离出来，形成可以版本化、引用、导入和查询的知识资产。知识来源可以包括漏洞知识、厂商信息、规则说明、技术文档、证据解释和外部 Provider 的导入内容。

Knowledge 与 Finding 不同：Finding 是对具体资产或目标的发现；Knowledge 是用于解释、分类、关联和辅助决策的相对稳定知识。一个 Finding 可以引用多个 Knowledge 版本，一个 Knowledge 可以服务多个 Finding，但不能把每次扫描结果直接当作永久知识。

CAP 的 Knowledge Center 还承担知识导入、来源、版本和解析边界，使未来 AI Planner 或调查 Agent 使用的是有来源、可追溯的上下文，而不是无来源的文本片段。

### 3.3 Security Plane

Security Plane 是专业安全业务域的集合，包括：

- Asset：被保护和被评估的对象；
- Assessment：主动评估、扫描和安全检查；
- Detection：规则、流和信号分析；
- Telemetry：网络、终端、日志和指标等遥测流；
- Incident：经过判断后需要运营处理的安全事件；
- Response：对事件或资产实施的治理动作；
- Notification：通知、工单和外部协同；
- Evidence：证明发现、判断、动作和结果的证据。

Security Plane 不把所有输入都直接提升为 Incident。Telemetry 是原始或标准化流，Detection 是检测逻辑，SecurityEvent 是检测结果，Incident 是需要组织流程处理的运营对象。这个分层避免告警泛滥和状态语义混乱。

### 3.4 Governance Plane

Governance Plane 负责约束“什么可以做、谁可以做、是否已经批准、结果是否可审计”。它包含：

- RBAC 和权限目录；
- Approval；
- Audit；
- Policy；
- Capability 兼容性；
- Manifest 约束；
- 版本冻结和变更管理；
- 生产检查清单；
- Evidence Lineage；
- 可观测性和发布证据。

Governance Plane 的价值在于让业务动作不再只依赖调用方自觉。即使请求来自一个 Agent 或自动化 Playbook，也必须进入后端治理边界。

### 3.5 Presentation Plane

Presentation Plane 主要是 Web Console 和稳定的 v1 API。Console 提供 Dashboard、Asset、Knowledge、Evidence、Assessment、Detection、Incident、Response、Playbook、Approval、Audit、RBAC、Worker、Sandbox、Plugin 和 Settings 等运营视图。

Presentation Plane 不拥有新的业务真相。它通过 API 调用平台能力，不直接访问数据库，也不应把隐藏按钮当作授权机制。页面可以根据权限显示或隐藏操作，但最终的 401/403、审批、审计和动作检查由 Backend 决定。

### 3.6 Plugin Ecosystem

Plugin Ecosystem 位于平台边界之外，但通过 Manifest、Capability 和 Provider Interface 进入平台。插件可以实现：

- Assessment：Nuclei、ZAP 等；
- Detection：Suricata、Zeek 等；
- Response：WAF、Firewall、EDR 等；
- Notification：消息、邮件、工单等；
- Telemetry：日志、流和终端遥测等。

插件生态不是“随意加载代码”。每个插件必须声明能力、输入、输出、超时、网络、Secret、兼容版本和安全边界。平台拥有插件的注册、调用、审计和执行治理权。

### 3.7 平面之间如何协同

一个典型协同过程如下：

1. Presentation Plane 接收用户请求。
2. Governance Plane 识别身份、检查角色和权限。
3. Platform Plane 将请求解析为 Capability 和 Runtime Context。
4. Security Plane 选择领域服务，例如 Assessment 或 Response。
5. Registry 找到符合 Manifest 的 Plugin 和 Provider。
6. Worker 领取任务并获得 Lease/Fencing Token。
7. Sandbox 按 Profile 执行外部工具或 Provider。
8. 结果回到 Security Plane，经 Normalizer 转换为 Finding、SecurityEvent 或 ResponseExecution。
9. Knowledge Plane 提供解释、分类和关联知识。
10. Evidence 和 Audit 保存结果、来源、动作和责任链。
11. Observability 输出指标、Trace 和结构化日志。
12. Presentation Plane 展示当前状态和可操作结果。

每一层都有自己的责任。这样可以避免控制器、插件、前端和脚本互相越权。

---

## 四、平台核心能力：每个模块承担什么责任

### 4.1 Runtime

Runtime 是 CAP 的执行上下文和能力调度基础。它负责把一个请求或 Playbook 节点转换成可执行的 Runtime Context，包括调用者身份、Capability、Plugin Manifest、Provider、输入、超时、网络策略、Secret 引用、审计上下文和 Trace 信息。

Runtime 不应该成为另一个业务服务。它的职责是执行合同、解析兼容性和生命周期，而不是决定漏洞等级或 Incident 处置策略。它把“能力是什么”和“如何执行”分开。

### 4.2 Workflow

Workflow 用于表达有向依赖的多步处理流程，适合描述确定性的节点关系和任务依赖。它负责计划、节点依赖、状态和执行顺序。

Workflow 与 Playbook 有联系但不完全相同。Workflow 更偏向一般性的任务编排和 DAG 结构；Playbook 更偏向安全运营场景中的声明式能力组合、审批等待、补偿链和持久执行。二者都不能绕过 Worker、Sandbox、RBAC 和 Audit。

### 4.3 Worker

Worker 是平台中的异步执行主体。它负责：

- 从任务源领取工作；
- 创建和维护 Lease；
- 发送 Heartbeat；
- 使用 Fencing Token 防止旧 Worker 写入；
- 执行超时和取消；
- 记录 Task、TaskExecution 和错误状态；
- 按策略重试；
- 在失败或 Worker 不健康时恢复可重试任务。

Worker 的设计解决了进程和状态之间的矛盾。任务不是因为某个进程消失就永久消失，也不能因为网络抖动而被多个 Worker 无条件重复执行。

### 4.4 Sandbox

Sandbox 为 Provider、插件和工具执行提供受限环境。它可以表达：

- 是否允许网络访问；
- 允许访问哪些目标；
- 最大运行时间；
- 进程终止方式；
- 密钥允许以什么方式注入；
- 资源和并发限制；
- 输出大小和格式约束。

CAP 采用 Sandbox 的原因是，安全工具也可能存在漏洞、误配置或不可控输入。平台不能把所有第三方工具都当作可信库直接导入主进程。

### 4.5 Registry

Registry 是平台能力和运行对象的目录。它可以记录 Agent、Tool、Capability、Plugin、Provider、Manifest、状态和兼容性。

Registry 的作用不是简单存一张插件列表，而是回答：

- 当前平台有哪些能力；
- 某个能力由哪些实现提供；
- 实现是否启用、健康和兼容；
- 输入输出合同是什么；
- 调用需要什么权限和资源；
- 是否是生产可用 Provider 还是测试 Provider。

### 4.6 Telemetry

Telemetry 负责接入、处理和持久化安全运营中不断产生的遥测流。它关注的是流、检查点、背压、重放和消费进度，不等同于 Detection。

Telemetry 层需要处理：

- 流来源和来源身份；
- 批量或逐条输入；
- 标准化时间、主体和字段；
- 消费 checkpoint；
- queue/backpressure；
- replay 和幂等性；
- Provider 断开和恢复。

Telemetry 的存在，使 Suricata/Zeek 等输入可以被平台统一接入，而不会把每一个数据源直接耦合到 Incident 服务。

### 4.7 Asset

Asset 表示平台认识和管理的安全对象，例如域名、URL、IP、主机、服务、应用、账号或终端。Asset 需要有稳定标识、类型、归属、环境、状态、标签和生命周期。

Asset 不是扫描目标字符串的同义词。扫描任务可以临时使用一个 Target，但安全运营需要把目标归一到可追踪资产，才能把历史 Finding、事件、响应和证据关联起来。

### 4.8 Knowledge

Knowledge 是具有来源和版本的安全知识。它可以解释一个漏洞、检测规则、处置建议、技术实体、威胁情报或外部文档。

Knowledge 不应保存为无来源的“AI 记忆”。企业需要知道知识来源、版本、更新时间和适用范围。未来 AI Planner 可以使用 Knowledge，但它输出的计划仍需经过 Capability 和 Governance 约束。

### 4.9 Evidence

Evidence 是支持安全判断和动作责任的证据。它可以包括扫描原始输出、请求响应摘要、规则匹配、进程信息、Provider 返回、审批记录、验证结果和回滚结果。

Evidence 与日志不同。日志记录系统过程；Evidence 用于证明某个 Finding、SecurityEvent、Incident 或 Response 是否有事实依据。Evidence 需要关联来源、时间、资源、动作、执行者和上游对象。

### 4.10 Assessment

Assessment 负责主动评估和安全检查。它通常从 Asset 或 Target 开始，调用 Assessment Plugin，接收原始结果，经过 Normalizer、Fingerprint、Risk 和 Knowledge Mapper 等处理后形成 Finding 或 Evidence。

Assessment 不负责直接关闭事件，也不应直接修改防火墙。它负责产生评估结论，后续的 Incident 和 Response 由相应领域治理。

### 4.11 Detection

Detection 负责从 Telemetry、规则或输入数据中识别安全信号。Detection 可以使用 Suricata、Zeek 或其他 Detection Plugin，但插件输出必须标准化为平台的 Detection Finding 或 SecurityEvent 语义。

Detection 的重点是匹配、归一化、严重性、规则身份、时间、主体和关联信息。它不等同于 Incident，因为一个 SecurityEvent 可能只是需要进一步调查的信号。

### 4.12 Incident

Incident 是经过检测、相关性分析和运营判断后，需要被组织处理的安全事件对象。Incident 包含状态、优先级、严重性、责任人、时间线、关联 SecurityEvent、Finding、Asset、Evidence 和处置动作。

Incident 不是所有告警的容器。若把所有 Detection 输出都直接建成 Incident，系统会失去运营容量和优先级管理。Incident 是治理和协作对象。

### 4.13 Response

Response 负责高影响安全动作的计划、审批、执行、验证、补偿和回滚。常见动作包括隔离主机、封禁地址、修改 WAF 规则、下发防火墙策略、执行 EDR Host Action。

Response 的关键不是动作调用本身，而是动作生命周期：Planned、Waiting Approval、Approved、Executing、Verified、Failed、Rolled Back 或 Compensated。动作必须可追踪、可审计、尽量幂等，并能够在失败时停止扩散。

### 4.14 Notification

Notification 将安全状态转换为组织协作。它可以发送消息、邮件、创建 Ticket 或向外部工单系统同步。

Notification 不负责重新判断事件，也不应隐藏原始 Evidence。它需要记录模板、路由、接收方、Provider、成功/失败和重试结果。Ticket 是运营协作对象，不应取代 Incident 或 Evidence。

### 4.15 Playbook

Playbook 是安全能力的声明式编排层。它可以把 Detection、Assessment、Knowledge、Incident、Approval、Response、Notification 和 Evidence 串成持久化执行流程。

CAP 的 Playbook 不是任意脚本容器。节点只能调用已治理 Capability；高影响节点可以等待审批；失败时支持顺序补偿；执行历史需要持久化；恢复时应从已知状态继续，而不是重新无条件执行所有步骤。

### 4.16 RBAC

RBAC 负责把身份映射为角色和权限，并对每一个受保护 API 做后端授权。CAP 当前使用可信代理注入的用户身份和代理密钥，生产身份验证由 OIDC/企业网关承担，CAP Backend 负责确认信任边界和授权。

RBAC 不是前端菜单控制。隐藏按钮只能改善用户体验，不能防止直接调用 API。对于 Response、Approval、Playbook 和平台管理操作，还需要领域层的显式权限和业务策略。

### 4.17 Observability

Observability 包含 Metrics、Trace 和 Structured Logging。CAP 使用低基数的 HTTP route template label，避免把用户 ID、Incident ID 或 Trace ID 直接写进指标标签。

系统提供健康检查、Ready 检查、Prometheus 指标、W3C traceparent 传播、Trace ID/Span ID 关联和结构化请求日志。Observability 不是附加装饰，它是验证 Worker、队列、审批、插件成功率、响应延迟和故障恢复的基础。

---

## 五、Plugin 架构：为什么是 Plugin → Adapter → Provider

### 5.1 三层的职责

CAP 的典型集成链路是：

```text
Plugin
  ↓
Adapter
  ↓
Provider
  ↓
External Product / Service
```

Plugin 是平台能力的领域实现和治理入口。它知道能力的语义、Manifest、输入输出合同和平台生命周期。

Adapter 是平台接口与外部工具接口之间的翻译层。它负责字段转换、请求构造、响应解析、错误映射、超时处理、重试边界和输出标准化。

Provider 是具体的外部实现。它可以是一个 HTTP API 客户端、命令行工具封装、SDK、云服务连接器或测试用 Mock Provider。Provider 只关注如何访问外部系统，不拥有平台领域状态。

### 5.2 为什么不能把三层合成一个类

如果 Plugin 直接包含所有工具调用代码，那么领域语义、厂商 API、网络细节和测试替身会混在一起。结果是：

- 更换工具需要修改领域服务；
- 测试必须启动真实外部服务；
- Mock 与生产实现难以替换；
- 错误和超时语义无法统一；
- Provider 细节可能泄漏到 API 和数据库；
- 同一能力无法支持多个厂商。

通过 Adapter 和 Provider 分层，CAP 可以在不改变平台能力语义的情况下替换实现。例如 `detection.network` 可以由 Suricata 或 Zeek 提供，`response.host_action` 可以由不同 EDR Provider 提供，`response.block` 可以连接不同 WAF/Firewall 厂商。

### 5.3 为什么平台不直接集成 Nuclei、ZAP、Suricata、Zeek、WAF、Firewall、EDR

这些工具都很重要，但它们不是平台本身。

Nuclei 擅长模板驱动的漏洞和暴露面探测；ZAP 擅长 Web 应用安全测试；Suricata 擅长网络入侵检测和网络事件输出；Zeek 擅长网络协议分析和结构化日志；WAF 和 Firewall 负责网络访问控制；EDR 负责终端可见性和响应动作。

它们的共同特征是：各自拥有不同的输入输出模型、配置方法、升级节奏、资源要求、错误方式和安全风险。如果这些工具被直接写入平台核心，CAP 将被外部产品的私有概念牵着走。

因此 CAP 不把“集成工具”当作“修改平台核心”。平台定义统一 Capability 和契约，工具通过 Plugin、Adapter 和 Provider 接入。这样：

- 工具升级主要影响 Provider；
- 厂商替换主要影响 Adapter；
- 平台的 Finding、SecurityEvent、Incident、Response 和 Evidence 语义保持稳定；
- 测试可以使用 Fake/Mock Provider；
- 网络和密钥权限可以由 Sandbox 与 Secret Provider 统一限制；
- Plugin 不需要直接拥有数据库会话。

### 5.4 插件可替换的真实边界

“新增 Plugin 几乎不用修改平台”并不意味着完全不需要任何审查。新增插件仍然需要：

- 选择已有能力域；
- 遵守 Manifest 版本；
- 提供 Provider 实现；
- 处理超时、取消、错误和异常输出；
- 通过 Sandbox 和 Secret Provider；
- 增加单元、合同、安全、幂等和回滚测试；
- 注册到 Registry；
- 明确生产依赖和限制；
- 通过 RBAC、Audit 和兼容性评审。

真正的价值是：这些审查遵循平台统一规则，平台核心不需要为每个工具增加一套特殊逻辑。

---

## 六、统一数据模型：为什么 Asset、Finding、Event、Incident 不能混在一起

### 6.1 Asset：对象身份

Asset 回答“系统正在保护、观察或评估什么”。它是资源身份和生命周期的载体。没有稳定 Asset，Finding 和 Incident 很容易停留在一次性字符串上。

### 6.2 Knowledge：解释和关联

Knowledge 回答“我们知道什么，以及这个知识从哪里来”。Knowledge 可能说明漏洞、技术、规则、产品、处置建议或外部威胁情报。它有来源和版本。

### 6.3 Evidence：事实依据

Evidence 回答“我们凭什么相信这个判断或动作发生过”。它引用真实输出、匹配信息、请求响应、审批、验证和回滚结果。

### 6.4 Finding：评估发现

Finding 通常由 Assessment 产生，回答“在某个 Asset 或 Target 上发现了什么风险”。它强调目标、规则、严重性、指纹、状态、首次/最近发现时间和证据引用。

### 6.5 SecurityEvent：检测信号

SecurityEvent 通常由 Detection 产生，回答“系统观察到了什么安全信号”。它强调时间、主体、规则、来源、事件类型和上下文。

### 6.6 Incident：运营处理对象

Incident 回答“哪些安全信号已经被组织认定为需要处理的事件”。它有状态、负责人、优先级、时间线和协作过程。

### 6.7 Ticket：协作记录

Ticket 回答“哪个团队或外部系统需要跟进这件事”。它可以与 Incident 关联，但不能替代 Incident 的安全状态，也不能替代 Evidence。

### 6.8 Playbook：执行过程

Playbook 回答“平台按照什么声明式过程组合和执行能力”。它记录的是定义、节点、条件、审批、执行和补偿历史。

### 6.9 关系示意

```text
Asset
 ├── Finding ───────┐
 ├── SecurityEvent ──┼──> Incident ───> Ticket
 └── Evidence <──────┘       │
                              ├──> Approval
                              ├──> Response
                              ├──> Notification
                              └──> Playbook Execution

Knowledge 为 Finding / SecurityEvent / Incident / Playbook 提供解释和关联上下文。
Evidence 记录各阶段的事实依据与来源血缘。
```

### 6.10 混合建模的代价

如果把 Finding、SecurityEvent 和 Incident 混成一个 Alert 表，会出现：

- 扫描结果和网络实时事件使用同一状态机；
- 同一个漏洞重复创建事件；
- 一个事件关联多个检测信号变得困难；
- 处置动作没有稳定的业务对象；
- Ticket 状态覆盖安全事实；
- Evidence 只能作为附件，无法形成血缘；
- Playbook 无法区分触发输入、运营对象和执行结果。

分离模型不是为了增加概念数量，而是为了让每个状态有唯一责任人和正确生命周期。企业安全系统最怕的不是对象多，而是对象含义不清。

---

## 七、执行流程：从安全发现到运营闭环

### 7.1 网站安全评估流程

一个典型的网站安全评估流程可以表示为：

```text
Asset
  ↓
Workflow
  ↓
Assessment
  ↓
Plugin / Adapter / Provider
  ↓
Normalizer / Fingerprint / Risk
  ↓
Finding
  ↓
Knowledge Mapping
  ↓
Incident（按策略决定是否升级）
  ↓
Approval
  ↓
Response
  ↓
Verification / Rollback
  ↓
Notification / Ticket
  ↓
Evidence
  ↓
Audit / Metrics / Trace
```

第一步，平台接收一个已经登记的域名、URL、应用或服务 Asset。它不是让用户直接把字符串交给扫描器，而是先获得资产身份、归属和环境信息。

第二步，Workflow 或 Playbook 根据任务参数、权限和策略生成 Assessment 任务。任务中包含目标、范围、超时、网络和数据处理要求。

第三步，Assessment Service 选择合适的 Assessment Plugin。Nuclei Plugin 可以把模板扫描作为 Provider；ZAP Plugin 可以把被动或受限 Web 测试作为 Provider。平台不需要知道每个工具的内部对象，只接收 Assessment Contract。

第四步，Adapter 将平台请求翻译为工具请求，并把工具返回的原始结果标准化。Normalizer 处理字段、严重性、标签和原始输出；Fingerprint 防止同一发现重复扩散；Risk 模块负责风险表达；Evidence 保存可以复核的事实。

第五步，Finding 关联 Asset 和 Knowledge。Knowledge 可能说明漏洞类型、受影响组件、修复建议和相关参考资料。Finding 仍然是具体资产上的具体发现，不会被知识对象替代。

第六步，策略决定是否创建或更新 Incident。低风险、重复或已接受风险可以保持在 Finding 层；高风险、正在利用或具有业务影响的发现才进入 Incident 运营流程。

第七步，如果涉及高影响 Response，例如临时封禁、WAF 规则或终端隔离，系统需要请求 Approval。Approval 记录请求者、审批人、时间、范围、理由和决定。

第八步，已批准动作由 Worker 在 Sandbox 中执行。Provider 不直接获得平台数据库，也不直接拥有任意密钥。执行结果包括成功、失败、超时、取消、验证和必要的补偿。

第九步，Notification Service 将事件和动作状态发送给 SOC、资产负责人或工单系统。通知不是事实来源，而是将平台状态传递给协作对象。

第十步，Evidence 与 Audit 形成完整链条。审计回答谁做了什么，证据回答为什么这样判断以及动作实际产生了什么结果。Metrics 和 Trace 用于运行层面的观察和故障定位。

### 7.2 Suricata 到 Playbook 的检测流程

另一个典型流程如下：

```text
Suricata
  ↓
Telemetry Adapter
  ↓
Telemetry Stream / Checkpoint / Backpressure
  ↓
Detection Plugin
  ↓
SecurityEvent
  ↓
Correlation
  ↓
Incident
  ↓
Playbook
  ↓
Approval（必要时）
  ↓
Response / Notification
  ↓
Evidence / Audit
```

Suricata 输出网络事件或规则命中结果。它们首先进入 Telemetry，而不是直接写 Incident。Telemetry 负责来源、格式、消费进度、背压和重放。

Detection Plugin 使用平台 Detection Contract 对事件进行规则处理、归一化和分类，产生 SecurityEvent。SecurityEvent 可能包含攻击类型、源地址、目标地址、规则 ID、时间和原始证据引用。

Correlation 负责把多个相关事件、同一资产、同一时间窗口或同一攻击链组合起来。只有达到运营策略要求时，才创建或更新 Incident。

Playbook 可以根据 Incident 的类型选择调查、通知、资产查询、阻断或升级流程。低影响调查可以自动执行；高影响响应需要 Approval。整个执行状态由 Playbook Runtime、Worker、Audit 和 Evidence 保存。

### 7.3 Zeek 遥测与检测流程

Zeek 的价值通常在于结构化网络行为和协议日志，而不只是单条告警。CAP 可以将 Zeek JSONL 或其他日志格式接入 Telemetry Plugin，使用 checkpoint 和流处理机制对日志进行消费。

Zeek Telemetry 与 Zeek Detection 可以保持分离：

- Telemetry 关注日志接收、字段、时间、流进度和背压；
- Detection 关注检测语义、事件类型和安全规则；
- Incident 关注运营处置；
- Playbook 关注跨能力编排。

这种分离使未来可以在不改动 Telemetry 接入层的前提下增加新的检测策略，也可以在不改变 Incident 模型的情况下替换日志来源。

---

## 八、安全设计：企业为什么必须建立多层控制

### 8.1 Worker 不是普通后台线程

普通后台线程只关心函数是否执行完成；企业 Worker 还需要回答：这个任务属于谁，它是否仍然有效，是否被其他 Worker 接管，旧 Worker 是否还能继续写入。

CAP 通过 Lease、Heartbeat、状态版本和 Fencing Token 处理这些问题。Worker 领取任务后获得有限租期；如果心跳失效，任务可以被回收；如果旧 Worker 在网络分区后恢复，它必须因为 Fencing 失效而不能覆盖新执行者的状态。

### 8.2 Sandbox 不是“把命令放到子进程”

子进程隔离本身不等于安全。Sandbox 需要限制网络、时间、输出、密钥和生命周期，并在超时或异常时可终止。对于外部扫描器、网络客户端和终端响应 Provider，Sandbox 是降低第三方代码风险和执行面风险的必要层。

### 8.3 Secret Provider

密钥不能随着 Plugin、Playbook 或 Agent 在系统中自由流动。Secret Provider 通过引用方式为执行上下文提供所需凭据，平台记录“使用了哪个 Secret 引用”，但不应把明文写入日志、Evidence 或前端。

未来接入 Vault 或云密钥管理服务时，Provider Interface 可以保持稳定，变化主要位于 Secret Provider 实现和部署配置。

### 8.4 Approval

审批的本质是将高影响动作从“计算结果”升级为“组织授权”。审批必须在后端强制执行，记录请求者和决策者，并与具体 Incident、Response Plan、Asset、动作参数和 Evidence 关联。

审批不是对 Agent 的不信任，而是对不可逆业务影响的治理。即使 AI Agent 判断正确，也不能自动获得越过制度的权力。

### 8.5 Rollback 与 Compensation

回滚不是所有动作都天然支持的按钮。有些动作可以恢复原配置，有些只能执行补偿动作，有些外部系统根本不提供可靠反向操作。因此 CAP 需要显式记录：

- 原始状态是否可获取；
- 动作是否幂等；
- 是否有补偿能力；
- 回滚是否需要再次审批；
- 回滚完成后如何验证；
- 失败后怎样进入人工处置。

这使系统不会把“调用成功”错误地等同于“风险已经恢复”。

### 8.6 RBAC

CAP 当前将身份验证委托给可信代理或企业网关，Backend 接收经过验证的身份并执行权限判断。生产网关必须删除客户端自带的身份头和代理密钥头，再注入经过验证的值。

这个设计把身份认证和平台授权分开：OIDC、SAML 或企业身份系统负责证明“用户是谁”，CAP 负责判断“这个用户能否调用这个能力”。未来接入 OIDC 不应改变后端的授权核心。

### 8.7 Audit

审计需要覆盖正常和异常路径：成功、拒绝、超时、取消、重试、补偿、回滚和策略阻断都需要保留。只有记录成功动作而忽略失败，企业无法解释事故。

### 8.8 Evidence Lineage

Evidence Lineage 让系统能够从一个最终处置结果向前追溯：

```text
Response
  ← Approval
  ← Incident
  ← SecurityEvent / Finding
  ← Detection / Assessment
  ← Telemetry / Asset
  ← Provider 原始输出
```

这条链路对合规、取证、复盘、误报分析和 AI 决策审计都很重要。它也决定了未来 CAP 能否让 AI 生成“有证据的调查结论”，而不是只生成自然语言摘要。

---

## 九、工程亮点：为什么 CAP 是平台而不是工具集合

### 9.1 工具集合的特征

工具集合通常是多个工具并排存在：一个扫描器、一个检测器、一个脚本、一个工单 API。它们可能通过定时任务或消息连接，但没有统一的能力、权限、状态和证据模型。

工具集合的扩展方式通常是复制粘贴：新增一个工具，就新增一段调用代码；新增一个响应动作，就新增一个异常处理和一套日志；新增一个 Agent，就让它拥有更多环境访问权限。

### 9.2 平台的特征

平台提供的是稳定的内部规则和可复用的边界：

- 能力有统一身份和版本；
- 插件通过标准接口注入；
- 执行通过 Worker 和 Sandbox；
- 状态由数据库和迁移管理；
- 高影响动作通过 Approval；
- 失败和恢复有显式语义；
- 观察和审计横向覆盖所有域；
- 前端只是平台能力的表现层；
- 未来的 Provider 和 Agent 遵守既有合同。

所以 CAP 的平台价值不在于“接入了多少个工具”，而在于新增工具不会重新定义平台的基本安全边界。

### 9.3 新增 Plugin 为什么不需要大范围改平台

假设新增一个新的 EDR Provider。理想情况下，它需要：

1. 遵守现有 Host Action 或 EDR Capability；
2. 提供 Manifest；
3. 实现 Provider Interface；
4. 由 Adapter 完成字段和错误转换；
5. 注册插件；
6. 使用 Sandbox 和 Secret Provider；
7. 接入 Worker、Audit、Evidence 和 Observability；
8. 补充合同和安全测试。

它通常不需要新增数据库表，不需要修改 Incident 核心模型，不需要让前端知道厂商私有字段，也不需要让 Playbook 写一套新的权限系统。

这就是接口和能力抽象对长期维护的意义：变化被限制在适配边界内。

### 9.4 长期维护性来源

CAP 的维护性来自几个方面：

- 领域模型分离，减少状态语义混乱；
- Adapter 隔离外部变化；
- Contract 测试防止实现偏离接口；
- Manifest 显式表达依赖和版本；
- PostgreSQL 作为权威状态，减少缓存真相；
- Worker Lease/Fencing 降低重复执行风险；
- Approval/Rollback 把高影响动作的复杂性显式化；
- Metrics/Trace/Audit 让问题可定位；
- API Freeze 和 SemVer 限制发布期间的随意变化；
- 文档、ADR 和测试共同形成架构记忆。

---

## 十、创新点：不是单点算法，而是治理方式的组合

### 10.1 Capability-first

传统工具通常从产品开始，平台围绕工具建模；CAP 从平台能力开始，再将工具作为实现。它使系统可以回答“平台要提供什么稳定能力”，而不是“某厂商工具今天返回了什么字段”。

创新价值在于，Capability 成为 Agent、Playbook、Plugin、RBAC 和审计共同依赖的稳定语言。这样能力不仅能被调用，还能被授权、版本化、测试和组合。

### 10.2 Plugin-first

Plugin-first 将工具变化隔离在生态边界内。其创新价值不是“插件机制本身新”，而是把插件与治理、Manifest、Sandbox、Provider、证据和兼容性联系起来，避免插件成为没有边界的动态代码。

### 10.3 Worker/Sandbox 的平台级组合

很多系统有异步任务，也有容器隔离，但不一定把 Lease、Fencing、审批、密钥、超时和证据纳入同一执行上下文。CAP 将 Worker 和 Sandbox 作为平台能力，使每个外部动作都可以在一致的约束下执行。

### 10.4 统一知识中心

CAP 的 Knowledge Center 不只是一个文档搜索功能。它试图把外部知识、规则解释、漏洞信息、技术实体和运行结果之间建立可版本化关系。未来 AI Planner 使用知识时，可以追溯引用来源，而不是把模型生成当作事实来源。

### 10.5 统一资产中心

Asset Center 解决的是跨工具身份问题。扫描器中的 URL、Zeek 中的主机、EDR 中的终端、WAF 中的站点和工单中的业务系统，都需要归并到稳定的资产身份上。没有统一资产，安全运营只是多个系统的局部视图。

### 10.6 统一事件模型

将 Telemetry、Detection、SecurityEvent 和 Incident 分离，是 CAP 对安全运营语义的一个重要取舍。它允许不同来源的事件被统一关联，同时避免每条原始日志都直接占用 Incident 运营资源。

### 10.7 Playbook 与补偿链

CAP 的 Playbook 强调持久执行、审批恢复、失败语义和补偿链，而不是只强调节点数量。安全自动化的难点不只是完成主路径，还包括中断后如何恢复、动作失败后如何停止、部分成功后如何补偿。

### 10.8 Evidence Lineage

Evidence Lineage 将检测、判断、审批、响应和验证串成可追溯链路。这种设计适合审计、复盘、合规和 AI 辅助调查，特别适合需要解释“为什么执行这个动作”的企业安全环境。

### 10.9 Provider-neutral

CAP 不绑定具体厂商 Provider。Provider-neutral 不是否定厂商能力，而是让平台的生命周期和安全语义不依赖某一个厂商。企业可以逐步替换 Provider、在不同环境采用不同实现，并保留统一的能力和审计接口。

---

## 十一、适用场景

### 11.1 企业 SOC

CAP 可以作为企业 SOC 的能力治理层，将资产、漏洞、检测、事件、响应和通知放入同一运行框架。SOC 不必因为新增一个 Provider 就重新设计权限和审计。

### 11.2 安全运营中心

对于有 7×24 运营要求的组织，Worker、Queue、Lease、Observability 和 Playbook 可以帮助区分实时检测、调查任务和高影响响应，并把失败任务纳入恢复流程。

### 11.3 教育靶场

CAP 适合用于网络安全教学和靶场编排。教师可以将 Assessment、Detection、Incident、Response 和 Evidence 组合成实验流程；Sandbox 和 Provider 可以隔离实验工具，避免学生直接触碰平台持久层。

### 11.4 攻防演练

在攻防演练中，Asset、Telemetry、Detection、Incident、Playbook 和 Notification 可以形成时间线；Evidence 和 Audit 便于演练复盘。响应动作仍可以要求审批，以模拟真实企业流程。

### 11.5 漏洞运营

CAP 可以将扫描器结果从一次性报告转化为长期 Finding，关联资产、知识、责任团队、Incident、Ticket 和修复证据。它不替代扫描器，而是把扫描结果纳入运营生命周期。

### 11.6 蓝队平台

蓝队可以通过 Suricata、Zeek、EDR、WAF 和 Firewall Provider 形成分层检测与响应，同时利用统一的 SecurityEvent、Incident 和 Playbook 进行协同。

### 11.7 安全研究平台

研究人员可以将新检测器、实验 Provider、仿真工具或 AI Agent 注入平台，通过 Manifest、Sandbox 和测试合同进行验证。这样研究成果有机会逐步成为可治理的正式能力，而不是永远停留在个人脚本中。

### 11.8 不适合直接承担的场景

CAP v1 不应被描述为：

- 已经完成所有生产环境认证的 SaaS；
- 可以替代 SIEM、EDR、WAF 或 Firewall 的单一产品；
- 无需人工审批的全自动攻击平台；
- 不需要企业身份系统的登录产品；
- 可以在任何网络环境中直接执行任意代码的 Agent 平台。

它是一个企业级安全能力控制平面和运营编排平台，需要与真实环境、身份、密钥、基础设施和组织流程共同完成生产化。

---

## 十二、未来规划：从可治理平台走向分布式安全操作系统

### 12.1 v1：稳定边界和生产基础

v1 的重点不是堆叠更多安全功能，而是固化以下基础：

- 稳定 OpenAPI、SDK 和 Plugin Manifest；
- Asset、Knowledge、Finding、SecurityEvent、Incident、Response 和 Evidence 的关系；
- Worker Lease/Fencing；
- Sandbox 和 Secret Provider；
- RBAC、Approval、Audit 和 Observability；
- Playbook 持久执行和补偿语义；
- Compose、Helm、CI/CD、备份恢复和运维文档；
- 真实 PostgreSQL、Redis、镜像、SBOM、性能、Soak 和恢复认证。

v1 的成功标准不是“功能最多”，而是边界稳定、结果可信、责任可追溯。

### 12.2 v1.1：生产集成和治理增强

v1.1 可以在不破坏 v1 合同的前提下增加：

- OIDC 和企业身份集成；
- Vault 或云 Secret Manager；
- 更完整的 Provider 健康、容量和版本管理；
- Durable Event Bus；
- 更完善的 Evidence Lineage 查询和导出；
- 事件去重、相关性和时间线视图；
- 多环境和租户隔离的边界设计；
- 更强的审计保留、归档和检索能力；
- 目标环境的持续性能和 Soak 基线；
- 变更审批、策略模拟和发布前 Playbook 校验。

v1.1 的关键仍是向后兼容。新增能力应作为兼容扩展，不应让现有 Plugin 或 Playbook 被迫重写。

### 12.3 v2：分布式控制和智能规划

v2 可以考虑：

#### OIDC 和统一身份

将可信代理身份与标准 OIDC/OAuth2 集成，仍然保持 Backend RBAC 权威。身份系统负责认证，CAP 负责能力授权和审计。

#### Vault 和密钥联邦

让 Secret Provider 支持 Vault、云 KMS、企业密钥服务和短期动态凭据。目标不是让 CAP 保存更多秘密，而是让 Provider 得到更短生命周期、更小权限的凭据。

#### Durable Event Bus

将 Telemetry、Audit、Evidence 和跨服务事件从简单队列扩展为可持久、可重放、可分区的事件总线。必须同时处理顺序、重复、背压、检查点和权限边界。

#### Kubernetes Operator

把 CAP 的部署、迁移、Worker、Provider 和策略管理进一步声明式化，由 Operator 负责协调目标状态。但 Operator 不能绕开平台的审批和审计规则。

#### Graph Projection

在 Asset、Knowledge、Finding、SecurityEvent、Incident、Evidence、Ticket 和 Playbook 之间建立图投影，用于攻击路径、资产依赖、事件关联和调查导航。图数据库或图投影不应取代 PostgreSQL 的权威写模型，而应作为可重建的读模型。

#### AI Planner

AI Planner 可以根据 Knowledge、Asset、历史 Finding 和检测上下文生成调查计划或 Playbook 草案。但它必须遵守：

- 只调用已注册 Capability；
- 不直接获取任意 Secret；
- 不绕过 RBAC；
- 高影响动作必须 Approval；
- 计划、输入、证据和输出可审计；
- 计划版本可复现；
- AI 失败时系统 fail closed，而不是自动扩大权限。

#### Autonomous Investigation

可以发展半自动或受控自治调查，让 Agent 自动执行资产查询、知识检索、日志关联、证据收集和低风险验证。对于隔离主机、阻断网络、修改策略、删除资源等高影响动作，必须继续保持审批边界。

未来的自治不应被理解为“取消人”。更合理的目标是让机器自动完成证据收集和低风险重复工作，把人的注意力集中在判断、授权、例外和风险接受上。

---

## 十三、CAP 与其他系统的根本区别

### 13.1 与传统漏洞扫描器的区别

漏洞扫描器是专项检测工具，CAP 是承载扫描器、资产、知识、发现、事件、处置和证据的能力平台。扫描器回答“哪里可能有漏洞”，CAP 还需要回答“这个目标是什么资产、风险是否真实、谁负责、是否升级事件、是否批准处置、处置是否生效”。

### 13.2 与 SOAR 的区别

SOAR 通常强调流程编排，CAP 将流程编排放在更大的平台治理体系中。CAP 的 Playbook 不是任意脚本，而是基于 Capability、Worker、Sandbox、Approval、Audit、Evidence 和 Rollback 的持久执行模型。

### 13.3 与 SIEM 的区别

SIEM 主要聚合、检索和分析日志事件，CAP 不以日志存储为核心。CAP 关注从遥测到 Detection、SecurityEvent、Incident、Response 和运营闭环。它可以消费 SIEM 或向 SIEM 输出结果，但不等同于 SIEM。

### 13.4 与脚本自动化的区别

脚本通常以调用成功为终点，CAP 以能力合同、权限、执行状态、证据、审计和恢复为完整生命周期。脚本可以成为 Provider 实现，但不能直接代表平台治理。

### 13.5 与普通 Agent 的区别

普通 Agent 是一个能够执行任务的智能组件，CAP 是治理 Agent 和工具执行的运行平台。Agent 在 CAP 中受到 Runtime、Capability、RBAC、Sandbox、Secret、Approval、Audit 和 Evidence 的约束。

### 13.6 与 AI Agent 的区别

AI Agent 擅长理解、规划和推理，CAP 负责将推理转化为可验证、可授权、可恢复的企业动作。CAP 不把模型输出当作事实，也不把自然语言意图直接当作生产权限。

### 13.7 一句话概括差异

传统安全工具关注“完成某一项安全任务”；CAP 关注“让不同安全能力在统一的身份、数据、执行、治理、证据和恢复框架下长期协作”。

---

## 十四、工程价值与企业价值

### 14.1 工程价值

CAP 的工程价值主要体现在复杂性治理，而不是功能数量：

1. 通过 Capability 和 Interface 降低外部工具变化的传播范围。
2. 通过 Plugin/Adapter/Provider 分离领域语义和厂商细节。
3. 通过 Worker、Lease 和 Fencing 处理异步执行的一致性问题。
4. 通过 Sandbox、Secret Provider 和 Approval 限制副作用。
5. 通过明确的数据模型避免 Finding、Event、Incident 和 Ticket 混乱。
6. 通过 Evidence 和 Audit 提高安全结果的可解释性。
7. 通过 Metrics、Trace 和日志降低生产排障成本。
8. 通过 API Freeze、SDK 和 Manifest 保护兼容性。
9. 通过测试、ADR、发布检查和 Runbook 形成长期架构记忆。

### 14.2 企业价值

企业层面的价值包括：

- 让现有安全投资可以通过 Provider 继续复用；
- 降低新工具接入对核心平台的侵入；
- 让安全响应具备审批、追责和回滚能力；
- 让资产、发现、事件和证据形成统一视图；
- 让自动化从个人脚本升级为组织可维护能力；
- 让 AI 能够在安全边界内辅助调查，而不是成为不可审计的超级账号；
- 让生产发布拥有可验证的入口门禁，而不是只依赖“测试通过”的口头结论。

但是企业价值必须建立在真实环境验证之上。一个具备良好架构设计、却没有完成 PostgreSQL、Docker、Kubernetes、SBOM、压力和 Soak 认证的系统，仍然不能直接被称为生产就绪。CAP 当前的架构价值已经形成，但 GA 认证需要独立证据。

---

## 十五、对 CAP 的完整评价

从 Principal Security Architect 的角度看，CAP 真正要建设的不是一个“集成了许多安全工具的 Web 系统”，也不是一个“可以调用工具的 AI Agent 框架”。它更准确的定义是：

> 一个面向企业安全运营的、以 Capability 为核心抽象、以 Plugin 为扩展边界、以 Worker/Sandbox 为执行边界、以 PostgreSQL 为权威状态、以 RBAC/Approval/Audit/Evidence 为治理基础、以 Playbook 为编排方式的安全能力控制平面。

它的核心贡献不在某个单独的扫描器、检测器或 AI 模型，而在于建立了一种可以长期扩展的组织方式：

- 工具不是平台；
- Agent 不是权限中心；
- Playbook 不是任意脚本；
- Finding 不是 Incident；
- Event 不是 Evidence；
- Ticket 不是安全事实；
- 日志不是审计；
- 调用成功不是响应成功；
- 静态模板通过不是生产认证；
- 测试通过不是 GA 授权。

这组区分正是企业安全平台能否长期维护的基础。

CAP 的方向是合理的，因为它把安全系统中最容易被忽略的部分——边界、状态、责任、证据、恢复和兼容性——放到了架构中心。它也保持了相对克制：没有试图让平台直接替代所有安全产品，没有把每个工具的私有概念提升为公共 API，没有把 AI 的推理能力夸大为无需审批的自治权。

CAP 当前最重要的工作不是继续增加功能，而是完成生产认证闭环：真实 PostgreSQL migration round-trip、真实 Compose 与镜像、Helm、CI/CD artifacts、SBOM、依赖和镜像扫描、外部性能、8 小时以上 Soak、重启恢复、目标环境安全控制以及最终签署。只有这些证据齐全，`1.0.0-rc1` 才能有依据地转换为 `v1.0.0`。

因此，对 CAP 的最终判断可以分为两层：

### 架构判断

CAP 已经具备平台化安全能力控制平面的清晰架构基础。其 Platform First、Capability First、Plugin First、Worker/Sandbox、Source of Truth、Approval/Rollback 和 Evidence Lineage 设计，能够支撑比单一安全工具或脚本更复杂、更可治理的企业安全运营。

### 发布判断

按照企业生产标准，当前不能基于架构设计或历史测试直接宣称 GA。Phase 24 已经明确将未执行的验证标记为 BLOCKED/PARTIAL，并停止开发等待 Architect 最终批准。这是正确的工程结论，而不是项目失败：它说明系统已经把“功能完成”和“生产认证”区分开来。

**CAP 目前是真正进入生产认证阶段的企业安全平台候选版本，而不是已经完成无条件生产认证的正式版本。**
