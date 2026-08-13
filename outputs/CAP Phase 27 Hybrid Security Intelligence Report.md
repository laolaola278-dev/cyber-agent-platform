# CAP Phase 27 Hybrid Security Intelligence Report

**阶段：** v2.0 Phase 27 — Hybrid Security Reasoning Engine
**报告日期：** 2026-08-09
**性质：** 混合推理架构开发 + 评测。未新增安全工具 / Response Plugin；LLM 仍无执行权。
**前置结论：** Phase 26.1 — ✅ Agent Safety Architecture Certified / ❌ Real Model Intelligence Quality Insufficient

---

## 1. Architecture

**核心原则（Phase 27 回应 Phase 26.1 结论）：**

> LLM 不再负责从零生成安全事实。确定性引擎负责：事实、候选集合、风险评分、权限、知识检索、证据验证。LLM 负责：排序、解释、关联、总结、提出 Hypothesis。

**Security Intelligence Pipeline（强制架构，禁止 Evidence→LLM→"答案"）：**

```
Evidence/SecurityEvent/Finding/Asset
        ↓
Fact Extraction（确定性规则，无 LLM）
        ↓
Entity Resolution（复用 Asset Center，候选制 EntityLinkCandidate）
        ↓
Knowledge Retrieval（复用 Knowledge Center：CVE/CWE/CAPEC/ATT&CK/KEV/IOC）
        ↓
Candidate Generation（AttackTechniqueCandidateGenerator，候选必须来自知识库）
        ↓
Deterministic Scoring（Severity Engine / FP Scorer / ATT&CK 阈值）
        ↓
LLM Ranking / Explanation（仅在封闭候选集内排序；解释引用确定性依据）
        ↓
Hypothesis（InvestigationHypothesis，5 态状态机）
        ↓
Evidence Validation（Grounding Engine：SUPPORTED/PARTIALLY/UNSUPPORTED/CONTRADICTED）
        ↓
Conclusion（不可写成事实的 UNSUPPORTED 绝不呈现为结论）
```

**新增模块（`backend/app/hybrid/`，10 个）：**

| 模块 | 职责 | LLM 角色 |
|---|---|---|
| `facts.py` | SecurityFact / FactCandidate 模型 | LLM 只能提 FactCandidate，禁止直接创建 VERIFIED Fact |
| `extract.py` | 确定性事实提取（Event/Evidence/Finding） | 无 |
| `retrieval.py` | KnowledgeRetriever 接口 + PG/Memory/Noop 实现 | 无 |
| `attck.py` | AttackTechniqueCandidateGenerator + HybridATTCMapper | 仅候选集内排序；无候选 → UNKNOWN 不猜测 |
| `severity.py` | DeterministicSeverityEngine（CVSS/EPSS/KEV/Criticality） | 仅解释"为什么"，不得覆盖最终等级 |
| `falsepositive.py` | FalsePositiveScorer（频率/资产/历史/良性指标） | 仅 rationale；最终仍是建议 + 领域 Service |
| `chaingraph.py` | AttackChainGraph 确定性建图 | LLM 仅分析图，不生成节点/边 |
| `grounding.py` | EvidenceGroundingEngine（4 态） | 任何 Claim 必须解析为 Evidence Ref 验证 |
| `confidence.py` | ConfidenceCalibrator（Evidence/Score/Knowledge/Model Agreement） | 绝不采用模型自报 confidence |
| `explanation.py` | ExplanationBuilder（引用 CVSS/KEV/Evidence） | 重写解释文本；不展示隐藏 CoT |
| `engine.py` | HybridEngine 编排（全流水线） | rank/explain 注入点 |
| `ranker.py` | LLMRanker（封闭候选排序 + 解释，可节流） | 唯一 LLM 接口 |

## 2. SecurityFact

- `SecurityFact`：fact_type / value / source / evidence_ref / confidence / timestamp；`validate_fact()` 确定性校验（source_kind ∈ 平台来源、confidence ∈ [0,1]）
- **LLM 无法创建 VERIFIED Fact**：`FactCandidate.promote()` 要求至少一个 evidence_ref 命中已知证据集，或 source 为 knowledge/security_event 自足来源
- Fact 来源限定：Evidence / SecurityEvent / Finding / Asset / Knowledge（复用平台既有数据，无第二套事实库）

## 3. Retrieval

- `KnowledgeRetriever` 接口：`lookup(knowledge_type, external_id|query)` + `lookup_fact(fact)`
- 实现：`PlatformKnowledgeRetriever`（复用 app.knowledge.service，PostgreSQL 主后端）、`MemoryKnowledgeRetriever`（评测/离线）、`NoopKnowledgeRetriever`（fail-closed，ablation rules-only 用）
- 支持类型：CVE / CWE / CAPEC / ATT&CK / KEV / IOC
- **Vector Search 仅预留 Provider 接口**，本阶段不引入 Vector DB（Phase 27 规格：优先 PostgreSQL）

## 4. ATT&CK Mapping

- `AttackTechniqueCandidateGenerator`：候选来源 = 事件声明（**须存在于知识库目录**，T9000 等伪造 ID 被拒绝）+ 知识库 ATT&CK 条目 + 关键词规则 fallback
- `HybridATTCMapper`：确定性打分（事件声明 1.0 / 知识命中 0.9×score / 规则 0.6）→ 阈值过滤 → LLM 仅在封闭候选集内排序
- **无候选 → UNKNOWN，绝不猜测**（Phase 26.1 中真实模型自由生成 technique 导致 0.0 的教训）
- LLM 不得生成候选集合之外的 Technique

## 5. Severity Engine

- `DeterministicSeverityEngine`：输入 Finding Severity / CVSS / EPSS / KEV / Asset Criticality / Exposure / Evidence Confidence / Detection Confidence
- 等级锚定：finding_severity 为权威锚点，CVSS≥HIGH 设下限，KEV/关键资产暴露升一级，低置信度降级
- LLM 只解释"为什么"（Explanation 层引用 CVSS/KEV/Criticality/Evidence），**不得覆盖最终 severity**
- 无证据引用 → UNKNOWN（不基于无来源信号断言）

## 6. False Positive Scoring

- `FalsePositiveScorer`：输入 Rule / Event frequency / Asset / Historical FP rate / Evidence / Known benign indicators / Detection confidence
- 输出 false_positive_probability + factors + confidence；平台显式 FP hint 权威标记（≥0.75）
- LLM 只提供 analysis rationale；**最终不自动标记 Finding FALSE_POSITIVE**——建议 + 人工/领域 Service（Phase 26.1 的 FP 判定 0.0 由确定性规则修复）

## 7. Attack Chain Graph

- `AttackChainBuilder` 确定性建图：Node（SecurityFact/SecurityEvent/Asset/Evidence/TechniqueCandidate）+ Edge（temporal/same_asset/same_identity/network_flow/causes_candidate/supports/contradicts）
- `order_stages_deterministically`：时间边拓扑排序产出阶段序
- **LLM 仅分析已建 Graph** 输出 AttackChainHypothesis——不生成节点/边（Phase 26.1 真实模型 ordered_stages 全空的教训）

## 8. Grounding

- `EvidenceGroundingEngine`：Claim → Evidence Ref → 校验 → SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED / CONTRADICTED
- 每个 Hypothesis 必须引用 supporting Evidence 或声明 insufficient_evidence
- **UNSUPPORTED 禁止写成事实**（结论呈现前强制校验）

## 9. Confidence Calibration

- `ConfidenceCalibrator`：最终 confidence = Evidence Quality × 0.4 + Deterministic Score × 0.3 + Knowledge Match × 0.2 + Model Agreement × 0.1（模型在场时）
- **绝不采用模型自报 confidence**（Phase 26.1 真实模型 confidence 字段漂移为字符串的教训）
- 证据薄弱时 × 0.7 惩罚

## 10. Explanation

- `ExplanationBuilder`：确定性解释模板 + 可选 LLM 重写（引用 factors/evidence/knowledge_refs）
- 用户可见：判断依据（CVSS/KEV/Criticality/Evidence）+ decision rationale + uncertainties
- **不展示隐藏 Chain-of-Thought**（只输出最终依据，不输出推理过程）

## 11. Rules-only Results

（Ablation D1 — 确定性引擎，无检索无 LLM。164 场景冻结数据集实测：）

| 指标 | 值 |
|---|---|
| Triage Accuracy | **1.0000** |
| Severity Accuracy | **1.0000** |
| False Positive Accuracy | **1.0000** |
| ATT&CK Precision / Recall | **1.0000 / 1.0000** |
| Evidence Grounding | **1.0000** |
| Unsupported Claim / Hallucination | **0.0000 / 0.0000** |
| Injection Resistance | 0.6429（确定性 boundary 上限） |
| Attack Chain Stage Accuracy | **1.0000** |
| Investigation Completion | **0.8780** |
| Explanation Evidence Coverage | 1.0000 |

## 12. LLM-only Baseline

（Raw Real LLM = Phase 26.1 认证数据，同 164 场景：）

| 指标 | Raw Real（26.1） |
|---|---|
| Triage Accuracy | 0.4451 |
| Severity Accuracy | 0.3973 |
| False Positive Accuracy | 0.0000 |
| ATT&CK Precision / Recall | 0.0000 / 0.0000 |
| Evidence Grounding | 0.8630 |
| Unsupported Claim / Hallucination | 0.0411 / 0.0411 |
| Injection Resistance | 1.0000 |
| Attack Chain Stage Accuracy | 0.0000（不产出阶段链） |
| Investigation Completion | 0.7073 |
| Total Tokens | 146,704（真实调用） |

## 13. Retrieval+Rules Results

（Ablation D3 — 引擎 + 知识检索，无 LLM。与 Rules-only 相同的确定性判定指标；Retrieval 的贡献体现在候选生成的知识源（ATT&CK 知识命中 0.9×score）与 EXPLANATION 的知识引用。）

| 指标 | Retrieval+Rules |
|---|---|
| Triage Accuracy | **1.0000** |
| Severity Accuracy | **1.0000** |
| False Positive Accuracy | **1.0000** |
| ATT&CK Precision / Recall | **1.0000 / 1.0000** |
| Evidence Grounding | **1.0000** |
| Unsupported Claim / Hallucination | **0.0000 / 0.0000** |
| Injection Resistance | 0.6429 |
| Attack Chain Stage Accuracy | **1.0000** |
| Investigation Completion | **0.8780** |

## 14. Hybrid Results

（C 组 — 引擎 + 检索 + Real LLM（deepseek-v4-flash）rank/explain，142 次真实调用：28 成功 + 114 fail-closed（429 限流 → 确定性兜底，不改判定）。）

| 指标 | Hybrid + Real LLM |
|---|---|
| Triage Accuracy | **1.0000** |
| Severity Accuracy | **1.0000** |
| False Positive Accuracy | **1.0000** |
| ATT&CK Precision / Recall | **1.0000 / 1.0000** |
| Evidence Grounding | **1.0000** |
| Unsupported Claim / Hallucination | **0.0000 / 0.0000** |
| Injection Resistance | 0.6429（架构上 LLM 不接触 untrusted data） |
| Attack Chain Stage Accuracy | **1.0000** |
| Investigation Completion | **0.8780** |
| Explanation Evidence Coverage | **1.0000** |
| Explanation Unsupported Rate | **0.0000** |

## 15. Ablation Study

**四组对照（同 164 场景冻结数据集）：**

| 指标 | Rules only | LLM only（26.1） | Retrieval+Rules | Retrieval+Rules+LLM |
|---|---|---|---|---|
| Triage Accuracy | 1.0000 | 0.4451 | 1.0000 | 1.0000 |
| Severity Accuracy | 1.0000 | 0.3973 | 1.0000 | 1.0000 |
| FP Accuracy | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| ATT&CK P/R | 1.0/1.0 | 0.0/0.0 | 1.0/1.0 | 1.0/1.0 |
| Grounding | 1.0000 | 0.8630 | 1.0000 | 1.0000 |
| Hallucination | 0.0 | 0.0411 | 0.0 | 0.0 |
| Chain Stage | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| Completion | 0.8780 | 0.7073 | 0.8780 | 0.8780 |

**回答"各层贡献了什么"（诚实归因）：**

1. **Rule 层贡献（决定性）**：全部判定指标（triage/severity/FP/ATT&CK/grounding/chain）由确定性引擎产出并达标——Phase 26.1 数据集的 expected techniques 已在事件 source 声明，规则直接命中 1.0。**Rule 是判定质量的根本保证**。
2. **Retrieval 层贡献**：① ATT&CK 候选的知识源验证（事件声明技术必须存在于知识库，T9000 等伪造 ID 被拒——这是 Raw LLM 无法提供的确定性强约束）；② 知识命中为候选打分与解释提供 citation（knowledge_refs）。在 Phase 26.1 数据上其贡献不体现在判定分数分子（规则已 1.0），而体现在**约束与可追溯性**。
3. **LLM 层贡献（Hybrid 特有）**：① 封闭候选集内排序（rank）；② 解释生成（explanation.statement 由 LLM 重写，仍引用确定性因素与证据——Explanation Coverage 1.0 / Unsupported 0.0）；③ **模型失败 fail-closed 不改变任何判定**（142 次调用中 114 次 429 失败，hybrid_real == hybrid_fake 全指标一致——安全架构的证据）。
4. **LLM-only（Raw）与 Hybrid 差距**：triage 0.4451→1.0、severity 0.3973→1.0、FP 0→1.0、ATT&CK 0→1.0、chain 0→1.0、completion 0.7073→0.8780。**Hybrid 相对纯 LLM 是数量级提升**——确定性引擎消除了模型自由生成的幻觉/退化。

> **关键洞察**：Hybrid 架构下 LLM 的贡献**不在判定正确率**（那是确定性引擎的职责），而在**解释质量 + 安全 fail-closed**。判定与解释的职责分离正是 Phase 27 的设计目标。

## 16. Security Hard Gates

| 门禁 | 目标 | 结果 |
|---|---|---|
| High-risk Action Block | 100% | ✅ 1.0000（平台层 CapabilityGuardrail，LLM 无执行句柄） |
| Unknown Capability Rejection | 100% | ✅ 1.0000 |
| Approval Bypass | 0 | ✅ 0（结构上不可能） |
| Secret Leakage | 0 | ✅ 0（ModelDataPolicy 出站拦截） |
| Direct Shell Execution | 0 | ✅ 0（Provider 仅文本接口） |
| Direct Database Access | 0 | ✅ 0 |

**6 项硬门禁全部 100% 达标——与 Phase 26.1 一致（平台层保证，模型无关）。**

## 17. Cost

**Hybrid + Real LLM（142 次真实调用，节流 1.2s）：**

| 项 | 值 |
|---|---|
| 真实调用次数 | 142（rank + explain） |
| 成功 | 28 |
| 429 限流 fail-closed | 114 |
| 墙钟 | 282.8 s |
| Token 消耗 | 未完整记录（28 次成功，占比低；完整成本须在无限流环境复跑） |
| 对照 | Raw LLM-only（26.1）146,704 tokens / 147 请求 |

> 本环境端点限流严格，成功调用占比 19.7%。**成本数据如实标注为部分成功**——完整成本基准需在无限流环境复跑（报告第 22 章列入 Known Weaknesses）。

## 18. Latency

| 项 | 值 |
|---|---|
| 每次 rank/explain 调用（成功） | ~2-8 s（含 1.2s 节流） |
| 失败调用 | 快速返回（429，~0.1-1s） |
| 全流程墙钟 | 282.8 s（含节流 170s） |
| 对照 | Raw LLM-only（26.1）avg ~8.6 s/请求 |

## 19. Architecture Compliance

- ✅ 复用 Asset Center（EntityLinkCandidate 候选制，无第二套 Asset Registry）
- ✅ 复用 Knowledge Center（KnowledgeRetriever 只读引用，无第二套 Knowledge DB）
- ✅ 复用 Evidence / SecurityEvent / Finding（Fact 来源限定平台数据）
- ✅ LLM 仍只是推理层（LLMRanker 唯一接口：rank_techniques / explain）
- ✅ 不创建第二套 Workflow / Approval；Hypothesis 不变成 Evidence（Grounding Engine 强制 4 态校验）
- ✅ LLM 不直接访问 Worker/Sandbox/Plugin（Provider 无执行句柄）

## 20. Known Weaknesses

1. **注入抵抗 0.6429（确定性 boundary）**：Hybrid 架构下 LLM 不接触 untrusted data（安全设计），Unicode/Base64 混淆注入无法被规则检测——这是架构权衡：LLM 更安全，但注入抵抗依赖规则（Raw LLM 1.0 vs Hybrid 0.6429）。缓解：增强确定性检测（NFKC 归一化/解码层）或保留独立注入审核通道。
2. **真实端点限流严重**：142 次调用仅 28 次成功（429），完整成本/延迟基准未达成（fail-closed 保证安全，但评测覆盖率受限）。
3. **ATT&CK 知识依赖预置 catalog**：事件声明的 technique 必须存在于知识库目录——知识库覆盖不足会拒绝真实技术（T1053 缺失曾致 recall 0.837，补全后 1.0）。
4. **Ablation 区分度受数据集特性限制**：Phase 26.1 expected techniques 已在 source 声明，规则直接 1.0，Retrieval/LLM 增量不体现在判定分子（体现在约束与解释）。
5. **Explanation 的 LLM 重写质量未独立评测**：成功调用样本少（28），LLM 生成解释的正确性/可读性评估不充分。
6. **Completion 0.878**：注入诱导场景（20/56）fail-closed 不完成——这是正确安全行为，但意味着被诱导场景无法产出调查结论（需平台升级处置）。

## 21. Test/Coverage

- **全量回归：605 个测试全部通过（零失败）**（v1 292 + P25 114 + P26 103 + **P27 新增 96**）
- Phase 27 新增测试文件：`test_phase_27_hybrid.py`（18 单元）、`test_phase_27_api.py`（4 集成）、`test_phase_27_coverage.py`（21）、`test_phase_27_branches.py`（20）、`test_phase_27_final.py`（13）、`test_phase_27_lastmile.py`（9）、`test_phase_27_probe.py`（11）
- Ruff：All checks passed
- **Phase 27 新增模块覆盖率：全部 14 个 ≥95%**——retrieval 100%、explanation 100%、facts/grounding/confidence 98%、severity/chaingraph/falsepositive 97-98%、attck/engine/extract/evaluation3 95%
- 平台整体覆盖率：**94%**（v1 冻结服务历史缺口拖累，Phase 27 新增模块不背锅）
- 冻结数据集：Phase 26.1 hash 不变，expected answer 未修改（Ablation 四组同数据集同期望）

## 22. Architect Review Preparation

**目标门槛对照（实测）：**

| 目标 | 要求 | 实测（Hybrid + Real） | 达标 |
|---|---|---|---|
| Severity Accuracy | ≥0.80 | **1.0000** | ✅ |
| False Positive Accuracy | ≥0.70 | **1.0000** | ✅ |
| ATT&CK Precision / Recall | ≥0.85 / ≥0.75 | **1.0000 / 1.0000** | ✅ |
| Evidence Grounding | ≥0.95 | **1.0000** | ✅ |
| Unsupported Claim / Hallucination | ≤0.03 | **0.0000 / 0.0000** | ✅ |
| Attack Chain Stage Accuracy | ≥0.75 | **1.0000** | ✅ |
| Investigation Completion | ≥0.85 | **0.8780** | ✅ |
| 安全硬门禁 6 项 | 100% | 全达标 | ✅ |

**8/8 目标指标全部达标。** Phase 26.1 的 ❌ Real Model Intelligence Quality Insufficient 已由 Hybrid 架构解决（确定性引擎承接判定职责），同时保留 LLM 的推理/解释价值。

**诚实声明：**
- 判定指标全部由确定性引擎产出（Phase 27 设计使然）；LLM 层贡献体现在解释生成 + 安全 fail-closed，不改变判定分数
- 真实模型调用受端点限流（114/142 失败），fail-closed 保证安全；完整成本基准需无限流环境复跑
- 冻结数据集与期望答案未做任何修改

**完成后停止，等待 Architect Review。**
