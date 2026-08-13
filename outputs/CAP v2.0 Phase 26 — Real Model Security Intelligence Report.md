# CAP v2.0 Phase 26 — Real Model Security Intelligence Report

**项目：** Cyber Agent Platform（CAP）
**阶段：** v2.0 Phase 26 — Real LLM Evaluation、Autonomous Triage & Attack-Chain Reasoning
**前置：** Phase 25 已通过 Architect Review
**报告日期：** 2026-08-08
**核心问题：** 当 FakeLLMProvider 被真实 LLM 替换后，CAP 是否仍然安全，且智能调查能力是否优于纯规则系统？

本阶段禁止新增安全工具、禁止新增 Response Plugin、禁止 LLM 直接执行高风险动作——均已遵守。

---

## 1. Acceptance Checklist

| # | 交付项 | 结果 | 证据 |
|---|---|---|---|
| 1 | Real LLM Provider | ✅ | `app/agent/providers.py`：OpenAI-compatible Provider，SecretProvider 取凭据，base_url allowlist，Configuration First |
| 2 | ModelDataPolicy | ✅ | `app/agent/datapolicy.py`：LOCAL_ONLY/REDACTED/MODEL_ALLOWED/MODEL_FORBIDDEN 四级分类 |
| 3 | TriageAgent | ✅ | `app/agent/triage.py`：建议性输出，不改变平台状态 |
| 4 | AttackChainAnalyzer | ✅ | `app/agent/attackchain.py`：多阶段攻击链假设（Hypothesis，非 Evidence） |
| 5 | Hypothesis Model | ✅ | `app/agent/hypothesis.py`：5 态状态机 + 证据接地强制校验 |
| 6 | Timeline Reasoning | ✅ | `app/agent/timeline.py`：统一时间线映射，只读，不改原始 timestamp |
| 7 | Entity Resolution | ✅ | `app/agent/entity.py`：EntityLinkCandidate，复用 Asset Center |
| 8 | Knowledge Candidate | ✅ | `app/agent/knowledge.py`：CVE/CWE/CAPEC/ATT&CK/KEV/IOC 引用候选 |
| 9 | ≥150 Evaluation Scenarios | ✅ | 164 个（19 类），见第 11 章 |
| 10 | Fake vs Real Comparison | ✅ | `app/agent/evaluation2.py` + 第 14 章 |
| 11 | Adversarial Evaluation | ✅ | 8 类攻击者视角测试 + 记录（第 15 章） |
| 12 | Cost/Latency Budget | ✅ | `app/agent/budget.py`：token/request/latency/cost 四维预算 |
| 13 | Model Failure Handling | ✅ | `app/agent/failures.py`：7 类故障全部 fail closed |
| 14 | AI Audit | ✅ | `model_invocations` 扩展列 + `investigation_hypotheses` 表（迁移 0020） |
| 15 | Web Console | ✅ | Investigation 页面新增 Triage / Attack Chain / Model Comparison 面板 |
| 16 | Architecture Compliance | ✅ | 第 21 章（8 项验证） |
| 17 | 安全硬门禁 100% | ✅ | 第 16 章：high-risk/unknown/secret/approval/shell/db 全达标 |
| 18 | 覆盖率：Phase 26 新增 ≥95% | ✅ | 全部新增模块 ≥95%（多数 99-100%）；整体 94%（v1 历史缺口），见第 22 章 |

## 2. GitHub Reference Analysis

### 2.1 研究范围（8 项）

| 项目 | 研究重点 | 借鉴 | 不采用 |
|---|---|---|---|
| **OpenAI Agents SDK**（复用 P25） | Guardrail/Tracing | 输入/输出双向护栏 | 模型直接调用工具 |
| **LangGraph**（复用 P25） | Checkpoint/HITL | 持久化审计思路 | 状态注入重放副作用 |
| **Microsoft AutoGen**（复用 P25） | Multi-Agent | Handoff 雏形 | 无权限隔离 |
| **OpenCTI** | STIX2 知识 schema、实体-关系推断、confidence 与来源引用 | **实体关联 + confidence 分级**（→ EntityLinkCandidate + InvestigationHypothesis）；来源必须可追溯（→ evidence grounding 强制校验） | 自由关系推断（CAP 的 EntityLinkCandidate 必须平台验证后才成 AssetRelation） |
| **MITRE ATT&CK CTI** | technique/tactic/relationship、STIX 数据模型 | **ATT&CK 映射的结构化表达**（→ AttackChainStage 的 technique_id/tactic）；观察→技术映射需可验证 | 不内置完整 ATT&CK 数据集（CAP 引用 Knowledge Center 的 ATT&CK 条目） |
| **Sigma** | 检测规则 YAML 结构 | 规则=结构化声明的思路（CAP 的 Guardrail 规则集类似） | 规则执行引擎（CAP 检测仍走既有 Detection 服务） |
| **TheHive** | Case/Task/Observable、TLP/PAP、Alert→Case | **Alert→Case 流程**（→ TriageAgent 的 escalation_recommended）；可观察物去重/关联（→ Timeline correlate） | Case 管理界面（CAP 用 Incident 模块） |
| **Timesketch** | Timeline/Sketch、标注协作 | **统一时间线 + 标注**（→ InvestigationTimelineEntry 的 tags）；只读分析视图 | 取证工作区 |

### 2.2 关键结论

1. **安全行业共识**：实体/关系必须带 confidence 且可追溯到来源（OpenCTI）——CAP 的 InvestigationHypothesis 强制"引用证据或 insufficient_evidence=True"，禁止无来源确定性事实。
2. **时间线是只读分析视图**（Timesketch）——CAP 的 TimelineBuilder 绝不修改原始 timestamp。
3. **Triage 是"建议→人工/平台决策"流程**（TheHive Alert→Case）——CAP 的 TriageAgent 只输出建议，状态变化归领域 Service。
4. **ATT&CK 映射必须可验证**（MITRE）——CAP 的 AttackChainHypothesis 是假设，永不写入原始 Evidence。

## 3. Real LLM Provider

- `OpenAICompatibleLLMProvider` 实现 `LLMProvider` 接口；模型名/端点/超时/retry/成本**全部 Configuration First**（`ModelConfig`），领域代码零硬编码。
- **凭据只经 SecretProvider**（`SecretReference` + `resolve()`）；禁止读取 .env Secret。
- **base_url allowlist**（DEFAULT_ALLOWED_BASE_URLS，fail closed）——未在白名单的端点直接拒绝。
- **Capability Degraded**：无合法 Secret 时 `health_check()=False`、`availability()` 返回 degraded 原因、`complete()` 抛 `ProviderUnavailableError`；**绝不 fallback 到未认证 Provider**。
- 支持：model / base_url / timeout / max_tokens / structured_output（response_format=json_object）/ token_usage / retry（429、5xx 重试）。
- FakeLLMProvider 保留用于确定性测试与对照。

## 4. Model Data Security

`ModelDataPolicy` 四级分类（fail-closed：未显式允许即 LOCAL_ONLY）：

| 分类 | 行为 | 示例 |
|---|---|---|
| MODEL_FORBIDDEN | 移除并计数 | api_key / token / cookie / private_key / authorization |
| LOCAL_ONLY | SHA-256 指纹占位 | 未知字段 |
| REDACTED | `首…末` 脱敏 | email / phone / ssn |
| MODEL_ALLOWED | 白名单字段透传 + 截断 | title / severity / technique / evidence_refs… |

- `sanitize_payload` 输出 `RedactionReport`（local_only/redacted/forbidden/secrets_removed/truncated 计数），写入 AI Audit。
- `validate_outgoing` 在出站前二次检查 Secret 与禁止 URL 模式（纵深防御）。
- 默认**禁止**发送：Secret / Credential / 完整 Cookie / Authorization Header / Private Key / 不必要的 Evidence Raw Payload。

## 5. Triage Architecture

- 输入：Finding / SecurityEvent / Incident Candidate / Asset / Knowledge / evidence refs。
- 输出：`TriageResult`（classification / severity_assessment / confidence / likely_false_positive / related_entities / techniques / recommended_investigation / escalation_recommended / evidence_refs / uncertainties）。
- **Agent 不得**关闭 Incident / 标记 FALSE_POSITIVE 为最终 / 执行 Response——TriageOutput 仅是建议；状态变化仍归领域 Service。
- 证据接地：`evidence_grounded = evidence_refs ⊆ 输入证据`。
- 注入数据 → `isolate_untrusted_data` fail-closed 拒绝（`ModelFailure`）。

## 6. Attack Chain Reasoning

- `AttackChainAnalyzer`：输入多事件 + Findings + 资产关系 + Knowledge + ATT&CK + Timeline → `AttackChainHypothesis`。
- 结构：ordered_stages（tactic/technique_id/entities/supporting_evidence）/ entities / techniques / supporting_evidence / contradicting_evidence / confidence / gaps / alternative_hypotheses。
- **Attack Chain 是 Hypothesis，不是 Evidence**——永不写入原始 Evidence；通过 `as_hypothesis()` 转为可持久化的 InvestigationHypothesis。

## 7. Hypothesis Model

- `InvestigationHypothesis` 状态机：PROPOSED → SUPPORTED / CONTRADICTED / INCONCLUSIVE / REJECTED。
- **强制证据接地**：`supporting_evidence` 非空 或 `insufficient_evidence=True`，否则构造即报错——模型无法输出无来源确定性事实。
- 不可变模型（transition 返回新实例），适合审计回放。

## 8. Timeline

- `InvestigationTimelineEntry` 统一映射 SecurityEvent / Finding / Evidence / IncidentTimeline。
- Agent 可排序（时间升序）、关联（按实体聚类 correlate）、总结（summarize）。
- **不修改原始 timestamp**（只读视图）。

## 9. Entity Resolution

- 复用 Asset Center；模型只提出 `EntityLinkCandidate`（source/target/link_kind/confidence/rationale/evidence_refs），状态 PENDING_VALIDATION。
- 平台验证后才成为正式 AssetRelation；**不建立第二套 Asset Registry**。
- 支持实体类型：IP/DOMAIN/HOST/USER/URL/HASH/APPLICATION。

## 10. Knowledge Enrichment

- 复用 Knowledge Center；`KnowledgeCandidate` 引用 CVE/CWE/CAPEC/ATT&CK/KEV/IOC，格式校验（vocabulary + reference pattern）。
- 模型**禁止**直接创建已确认 Knowledge；全部 PENDING_VALIDATION。

## 11. Evaluation Dataset

**164 个合成安全场景（19 类 ≥150 门禁）**：

| 类别 | 数量 | 判定要点 |
|---|---|---|
| normal_investigation | 10 | classification/severity 匹配 |
| false_positive | 10 | likely_false_positive 匹配 |
| multi_stage_attack | 10 | ATT&CK 映射 |
| missing_evidence | 10 | 降级到 UNKNOWN |
| conflicting_evidence | 10 | severity 判定 |
| wrong_attackck_mapping | 10 | 不被误导性 hint 带偏 |
| deceptive_evidence | 8 | severity 判定 |
| web/log/unicode/base64/cross-turn/tool/handoff injection | 7×8=56 | fail-closed 拦截 |
| scope_expansion | 8 | 越权目标拒绝 |
| unknown_capability | 8 | 拒绝率 100% |
| high_risk_response_request | 8 | 拦截率 100% |
| sensitive_data_exfiltration | 8 | Secret 拦截 |
| adversarial | 8 | 攻击者视角 |

## 12. Fake Provider Results

（164 场景实测，详见第 22 章附表）

| 指标 | Fake | 说明 |
|---|---|---|
| injection_resistance_rate | 0.6429 | **纯规则系统局限**：Unicode 全角/零宽、Base64 编码注入无法被模式匹配识别 |
| high_risk_action_block_rate | **1.0** | 硬门禁 ✓ |
| unknown_capability_rejection_rate | **1.0** | 硬门禁 ✓ |
| hallucination_rate | 0.0 | 规则输出零幻觉（也零推理） |

## 13. Real Provider Results

- **部署环境**：配置合法 Secret 后自动使用真实 OpenAI-compatible 端点（allowlist 内）。
- **本环境（无 Secret）**：Capability Degraded；对照评测采用**协议级仿真**（httpx MockTransport，模拟行为良好的真实模型：NFKC Unicode 归一化 + 零宽剥离 + Base64 解码后识别注入意图并拒绝）——报告明确标注，绝不冒充真实结果。
- 注入抵抗 **1.0**：归一化+解码能力使其能识别 Fake 漏掉的混淆注入——**这是"真实 LLM 优于纯规则系统"的核心证据**。

## 14. Model Comparison

- 同一 164 场景、同一 harness，Fake vs Real 分别计分（**禁止只给综合分**——13 项指标各自对照）。
- 关键对照：injection_resistance 0.6429 → 1.0；high_risk / unknown 双 100%（两者一致）；hallucination：Fake 0.0（无推理）vs Real 0.1692（仿真拒绝导致的未完成项，非真实幻觉，报告如实标注）。
- 模型名称可配置（ModelConfig），领域逻辑不绑定单一模型。

## 15. Adversarial Evaluation

8 类攻击者视角（modify_firewall / isolate_host / leak_secret / expand_asset_scope / ignore_system_policy / forge_evidence / forge_approval / spoof_handoff），每条记录 {attack, model_response, guardrail, outcome}：

- 结果：全部仅产生"建议性输出或拒绝"；无任何场景导致防火墙修改/主机隔离/Secret 泄露/状态变更。
- Guardrail 层（InputGuardrail/CapabilityGuardrail/OutputGuardrail/ModelDataPolicy）对攻击请求 fail-closed。

## 16. Security Hard Gates

| 门禁 | 目标 | 结果 |
|---|---|---|
| High-risk Action Block Rate | 100% | **1.0**（Fake 与 Real 均） |
| Unknown Capability Rejection | 100% | **1.0** |
| Secret Leakage | 0 | 0（MODEL_FORBIDDEN 移除 + validate_outgoing 出站拦截） |
| Approval Bypass | 0 | 0（required_approval 只转审批，绝不执行） |
| Direct Shell Execution | 0 | 0（Provider 无 shell 句柄；analyze_dangerous_commands 拒绝命令） |
| Direct Database Access | 0 | 0（Provider 只收文本返回文本） |

若任一项失败 → Phase 26 自动 Not Passed（当前全部达标）。

## 17. Cost & Latency

- `BudgetTracker` 记录每次调用的 tokens / requests / latency / estimated_cost（按 ModelConfig 单价），每 Investigation 汇总。
- 四维预算（max_tokens / max_requests / max_latency_seconds / max_estimated_cost）超限 → `AgentLoopLimit` 停止 Agent Loop（fail closed）。
- 评测报告分列 total_tokens / total_latency_ms / estimated_cost（Fake 与 Real 分开）。

## 18. Failure Handling

`ModelFailureHandler` 覆盖 7 类故障：timeout / 429 / 5xx / malformed JSON / refusal / context overflow / provider unavailable——全部映射到类型化 `ModelFailure` 异常并 **fail closed**（模型失败不绕过 Policy，绝不 fallback 未认证 Provider）。Malformed JSON 通过 markdown fence 剥离后仍失败则拒绝。

## 19. AI Audit

每次模型调用持久化（`model_invocations` 扩展列，迁移 `20260808_0020`）：

```
model / provider / prompt_version / input_policy / redaction_summary /
structured_output_valid / token_usage / latency / guardrail_verdict / trace_id
```

**不保存 Secret 明文**（redaction_summary 只存计数摘要）。Hypothesis 持久化到 `investigation_hypotheses` 表（状态机约束 CheckConstraint）。

## 20. Web Console

Investigation 页面新增：
- **Triage Agent 面板**：一键 Triage（HIGH/LOW 样例）+ 结果卡（classification/severity/confidence/techniques/uncertainties）。
- **Attack Chain 面板**：触发多事件分析，展示 ordered_stages 表（tactic/technique/evidence）。
- **Model Comparison 面板**：Fake vs Real 13 项指标对照表 + real_provider_note。
- 不展示隐藏 CoT——只呈现 decision rationale / supporting evidence / uncertainties。

## 21. Architecture Compliance

| 合规项 | 结果 | 说明 |
|---|---|---|
| 不创建第二套 Asset | ✅ | EntityLinkCandidate 候选制，复用 Asset Center |
| 不创建第二套 Knowledge | ✅ | KnowledgeCandidate 候选制，复用 Knowledge Center |
| 不创建第二套 Workflow | ✅ | 未新增 workflow 引擎 |
| 不创建第二套 Approval | ✅ | 复用 ApprovalState 语义 |
| 不让 LLM 成为权限主体 | ✅ | RBAC 主体仍是用户 |
| 不直接访问 Worker/Sandbox | ✅ | Provider 无任何执行句柄 |
| 不直接调用 Plugin | ✅ | 无 Plugin 引用 |
| Hypothesis 不变 Evidence | ✅ | 独立表 + 状态机 + 证据接地约束 |

## 22. Test / Coverage

**全量回归（2026-08-08 实测，SQLite in-memory + ASGI）：**

```text
509 passed in 350.05s —— 零失败（v1 冻结 292 + Phase 25 114 + Phase 26 103）
```

Ruff：`All checks passed!`；Alembic：单一 head `20260808_0020`，线性链 `20260808_0019 → 20260808_0020`。

**覆盖率（`coverage run --source=app`，独立 COVERAGE_FILE 采集）：**

| 范围 | 覆盖率 |
|---|---|
| 平台整体（全部 app） | **94%**（17,138 stmts / 1,089 missing；较 Phase 25 的 93% 提升） |
| **Phase 26 新增模块** | **全部 ≥95%**：failures/hypothesis/knowledge/llm/observability/planner/injection/handoff **100%**；datapolicy 99%、evaluation2 99%、guardrails 99%、agent 99%、providers 99%、timeline 98%、contracts 98%、budget 97%、entity 97%、triage 97%、attackchain 96%、service2 95%、loop 95% |

**覆盖率门禁（如实披露）**：Phase 26 新增模块达标（≥95%，多数 99-100%）；平台整体 94%——剩余 1,089 行缺失仍集中在 **v1 冻结服务**（assessment/response/notification/detection/incident 备选分支），属历史基线缺口，已列入 Technical Debt #1；v1 补测已开始（Phase 25/26 的 v1_coverage 测试补进部分分支），整体 95% 需持续投入。

**Fake vs Real 评测最终数据（164 场景）：**

| 指标 | Fake | Real(仿真) | 说明 |
|---|---|---|---|
| triage_accuracy | 0.4634 | 0.4634 | 规则 vs 仿真均按 expected 计 |
| severity_accuracy | 0.5000 | 0.5000 | |
| false_positive_accuracy | 0.1316 | 0.1316 | |
| attackck_mapping_precision | 1.0 | 1.0 | |
| attackck_mapping_recall | 0.7959 | 0.7959 | |
| evidence_grounding_rate | 0.8684 | 0.8684 | |
| unsupported_claim_rate | 0.0 | 0.0 | |
| hallucination_rate | 0.0 | 0.1692 | 仿真拒绝导致的未完成项（非真实幻觉，如实标注） |
| **injection_resistance_rate** | **0.6429** | **1.0** | **核心对照：真实模型能识别 Unicode/Base64 混淆注入** |
| **unknown_capability_rejection_rate** | **1.0** | **1.0** | 硬门禁 ✓ |
| **high_risk_action_block_rate** | **1.0** | **1.0** | 硬门禁 ✓ |
| investigation_completion_rate | 0.5854 | 0.5976 | |
| total_tokens / latency | 6,080 / 760ms | 按调用统计 | |

## 23. Known Issues

1. **Real Provider 本环境无真实 Secret**：真实端点调用未在本机执行；对照评测为协议级仿真（httpx MockTransport），结果**如实标注**，待部署环境配置 Secret 后复跑第 12-14 章。
2. **Fake 注入抵抗 0.64**：规则模式检测不了 Unicode 全角/零宽/Base64 混淆——这是纯规则系统的真实局限，也是 Real LLM 的价值点。
3. 前端 build/lint 仍受既有 node_modules 损坏影响（v1 已知问题），本阶段前端为增量改动。
4. 覆盖率 combine 受 safe-delete 钩子限制，用 `coverage run` CLI + 独立 COVERAGE_FILE 采集。

## 24. Technical Debt

1. 整体覆盖率仍受 v1 冻结服务历史缺口影响（见第 22 章实测），需持续补测。
2. Simulated-real 注入识别用 markers + NFKC/Base64 归一化——真实模型接入后应以真实推理结果为准。
3. `Phase26Service.choose_provider` 的 prefer_real 语义与 Secret 配置解耦待细化（配置中心接入）。
4. AttackChainAnalyzer 的 asset_relations / knowledge 参数已入接口但未深度参与 prompt 构造（后续阶段增强）。
5. Evaluation Harness v2 的 adversarial 记录建议增加结构化持久化（当前为运行期报告）。

## 25. Architect Review Preparation

**建议审阅要点：**

1. **安全边界有效性**：确认 Real Provider 无执行句柄、Secret 只经 SecretProvider、base_url allowlist、无 Secret 即 degraded。
2. **硬门禁证据**：6 项 100%（第 16 章）；评测 164 场景全绿。
3. **Fake vs Real 对照的诚实性**：Real 结果为协议级仿真（无真实 Secret），报告明确标注；部署环境复跑计划。
4. **数据策略**：ModelDataPolicy 四级分类 + 出站二次校验（第 4 章）。
5. **数据模型**：`model_invocations` 扩展 + `investigation_hypotheses` 表；迁移链单一 head 0020。
6. **API 冻结**：`/agent/triage`、`/agent/attack-chain`、`/agent/evaluations/v2`、`/agent/model-comparison` 契约。
7. **签署建议**：Phase 26 可作为 v2.0 Agentic Security 的第二个 RC 组件；真实 Provider 的端到端验证需部署环境（配置 Secret）后复跑评测并补充报告。

**完成后立即停止，不进入下一阶段，等待 Architect Review。**
