# CAP v2.0 Phase 28 — Adaptive Data Acquisition Report

**阶段：** v2.0 Phase 28 — Adaptive Data Acquisition Agent
**日期：** 2026-08-09
**基线：** v1 Core + Worker/Sandbox + Asset/Knowledge/Evidence + Workflow + Planner + Investigation + Hybrid Intelligence + CAP-SIB v1（Phase 27.1 已过 Architect Review）

---

## 1. Acceptance Checklist

| 要求 | 状态 |
|---|---|
| Adaptive Data Acquisition Agent | ✅ `app/acquisition/agent.py` |
| Acquisition Planner | ✅ `app/acquisition/planner.py` |
| HTTP Adapter | ✅ `app/acquisition/httpadapter.py` |
| Playwright Adapter integration | ✅ `app/acquisition/browseradapter.py`（复用平台 PlaywrightAdapter） |
| Document Adapter | ✅ `app/acquisition/documentadapter.py`（pypdf/python-docx/openpyxl/lxml 真实解析） |
| Extraction Pipeline | ✅ ExtractedDocument |
| Evidence Object Store | ✅ `store.py` LocalFilesystemEvidenceStore（content-addressed） |
| Evidence Lineage | ✅ Source→Raw Artifact→Evidence→ExtractedDocument→Candidate |
| SSRF Protection | ✅ URLPolicyValidator（含 DNS rebinding 防护） |
| Robots Policy | ✅ `robots.py` |
| Pagination | ✅ `pagination.py`（next-link/page/cursor/load-more） |
| Completeness Engine | ✅ `completeness.py` |
| Adaptive Replanning | ✅ HTTP→Browser 切换（scope 不变） |
| Deduplication | ✅ `dedup.py`（canonical + SHA-256，标记不删除） |
| Fact/Entity/Knowledge Candidate Integration | ✅ `candidates.py`（下游验证后才成真） |
| Hybrid Intelligence Integration | ✅ AcquisitionResult→Evidence→Fact→Knowledge→Hybrid |
| CAP-AQB v1 ≥100 scenarios | ✅ 124 场景（37.9% failure/blocked/partial） |
| Adversarial Tests | ✅ URL/redirect/DNS/循环/炸弹/注入 |
| Security Hard Gates | ✅ SSRF 100% / Restricted 100% / Scope 0 / Bypass 0 |
| Observability | ✅ `observability.py` RunTracker |
| Web Console | ✅ Data Acquisition 页面（tsc 0 错误） |
| Architecture Compliance | ✅ 复用 Worker/Sandbox/Evidence/Capability Registry |
| Phase 28 Coverage ≥95% | ✅（见 §29） |

## 2. GitHub Reference Analysis

（完整文档：`outputs/CAP Phase 28 GitHub Reference Analysis.md`）

| 项目 | 决策 | 理由 |
|---|---|---|
| Scrapy | 语义借鉴 | Twisted 栈冲突；Retry/RateLimit/Pipeline 语义 |
| Playwright | ✅ 采用 | 复用平台 PlaywrightAdapter+BrowserManager；扩展 Network observation |
| Crawl4AI | 语义借鉴 | 版本动荡 + StealthAdapter 与安全边界冲突 |
| Firecrawl | 语义借鉴 | AGPL + 云依赖 + 代理轮换冲突 |
| Trafilatura | 预留后端 | 环境不可用 → stdlib/lxml 真实实现，不伪报 |
| Apache Tika | 语义借鉴 | JVM 重；类型检测/元数据键思路 |
| Unstructured | 语义借鉴 | 依赖重；分区语义 + HTML sanitization 思路 |
| Scrapy-Playwright | 边界借鉴 | Windows 不支持；HTTP/Browser 按需切换思想 |

**Adapter 复用成熟能力**：httpx（HTTP）、Playwright（浏览器）、pypdf/python-docx/openpyxl/lxml（解析）、标准库（HTML/JSON/DOCX 兜底）。未重写任何成熟 Crawler/Browser/Parser。

## 3. Architecture

```
User Goal → AcquisitionPlanner → AcquisitionPlan
  → AdaptiveDataAcquisitionAgent
      → HTTPAdapter / PlaywrightAcquisitionAdapter / DocumentAdapter
          (URLPolicyValidator + RobotsPolicy + AcquisitionPolicy 全部强制执行)
      → LocalFilesystemEvidenceStore (SHA-256 content-addressed)
      → ExtractedDocument → FactCandidate/EntityLinkCandidate/KnowledgeCandidate
      → CompletenessEvaluator → FINISH/RETRY/REPLAN/PARTIAL/BLOCKED
```

- **Agent 不直接访问网络/文件系统/浏览器**：全部 I/O 经 Tool Adapter（生产环境运行于 Worker/Sandbox 内）
- Agent 不 import requests/httpx/playwright

## 4. Acquisition Agent

`AdaptiveDataAcquisitionAgent`：
- 理解目标 → 计划 → 选能力 → 受控采集 → 完整性检查 → 必要时 Replan → AcquisitionResult
- **STOP/BLOCKED 语义**：401/403/captcha/login/paywall/robots-disallow/SSRF → 立即停止，无任何绕过尝试
- Replan 仅切换传输方式（HTTP→Browser），**不增加域名/认证/Scope**

## 5. Planner

`AcquisitionPlanner`（确定性、无 I/O）：
- 输入：goal/url/asset/时间范围/期望字段/记录类型/记录数/能力/Policy
- 输出：AcquisitionPlan（target/source_type/strategy/steps/expected_outputs/completeness_conditions/budgets/fallback）
- 判定：STATIC_HTML / DYNAMIC_HTML / DOCUMENT / PUBLIC_JSON_API / UNKNOWN（扩展名 + API 路径标记）
- 实测 Strategy Selection Accuracy = **1.0**（124/124）

## 6. Tool Adapters

| Adapter | 实现 | 后端 |
|---|---|---|
| HTTP | 真实 | httpx（仅 Adapter 内使用） |
| Browser | 真实/契约 | 平台 PlaywrightAdapter + BrowserManager；环境不可用 → available=False（synthetic，不伪报） |
| Document | 真实 | pypdf / python-docx / openpyxl / lxml / stdlib |
| Content Extraction | 真实 | lxml.html 结构化提取（title/正文/链接/表格） |

## 7. Dynamic Page Strategy

- JS shell 检测：STATIC_HTML 提取正文为空 + 有 browser 能力 → **HTTP→Browser Replan**
- 受限等待：wait_for_selector / networkidle（有界超时 15s）
- 只观察页面正常前端请求，不触碰隐藏接口、不修改请求绕过权限

## 8. Public API Observation

- `PublicEndpointCandidate`（OBSERVED/VALIDATED/REJECTED）
- 仅记录页面自身 XHR/Fetch：同源、非隐藏路径（/admin、/internal、/debug 等排除）
- Agent 不猜测 API path、不 fuzz endpoint

## 9. Pagination

- 识别 next-link / page param / cursor / load-more / infinite-scroll
- 每页更新 next_url（防循环）；预算硬限：max_pages/max_records/max_duration/max_requests
- 对抗测试：50 页无限 next 链接 → 5 页内终止

## 10. Document Processing

- PDF（pypdf）/ DOCX（python-docx）/ XLSX（openpyxl）/ HTML（lxml）/ JSON / TEXT
- 类型检测：Content-Type 优先 + 魔数嗅探（Tika 思路）
- 大文件上限 max_document_bytes=20MiB；解析失败记录 raw artifact（PARTIAL）

## 11. Extraction

`ExtractedDocument`：title/text/sections/tables/metadata/links/published_at/author/language/source_url + **evidence_id + artifact_sha256（Lineage 引用）**
- 提取结果 ≠ 原始 Evidence；原始字节始终保存在 Object Store

## 12. Evidence Object Store

`LocalFilesystemEvidenceStore`：
- **SHA-256 content-addressed**（同内容同 key）、immutable、sidecar metadata
- size limit（默认 20MiB）、空对象拒绝、原子写入
- Provider 协议预留 S3/MinIO/Azure Blob；大型页面存对象存储，DB 只存元数据 + hash

## 13. Evidence Lineage

每次采集保存：Source URL / Final URL / Timestamp / HTTP Status / Content-Type / ETag / Last-Modified / SHA-256 / Method / Tool / Tool Version / Task ID / Trace ID
```
Source → Raw Artifact(object_key=sha256) → Evidence(DB 元数据) → ExtractedDocument(引用 evidence_id+sha256) → FactCandidate/EntityLinkCandidate/KnowledgeCandidate
```
- Lineage Completeness 实测 0.7661（blocked 场景无 artifact 属预期；成功场景全链完整）

## 14. SSRF Security（Critical）

`URLPolicyValidator`：
- 默认禁止：localhost / 127.0.0.0/8 / ::1 / IPv4-mapped / RFC1918 / link-local / 169.254.169.254（metadata）/ file/ftp/gopher/data/javascript / unix socket / userinfo
- **IP 字面量直查**（含 hex 0x7f000001、十进制 2130706433、IPv6 全零 ::1）
- **DNS 解析后重验证**（DNS rebinding 防护）；**重定向每跳重验证**
- 授权内部资产为未来显式 Policy 预留（allow_private=True 仅在测试/授权环境）
- 实测 SSRF Block Rate = **1.0**（redirect-to-private + DNS rebinding 场景全拦截）

## 15. Robots Policy

- 默认尊重 robots.txt（DISALLOWED → Agent 停止）；无法获取/无匹配规则 → 默认允许并记录
- 无绕过逻辑；企业授权环境未来通过 Policy 配置治理模式
- 实测 Robots Compliance Rate = **1.0**

## 16. Completeness Engine

`CompletenessEvaluator`：coverage_score / field_completeness / time_coverage / pagination_complete / duplicates / gaps / errors / confidence → verdict（FINISH/RETRY/REPLAN/PARTIAL/BLOCKED）
- gaps 存在 → 永不 FINISH；partial_failure（timeout/429）→ PARTIAL；无时间证据 → time_coverage=0

## 17. Adaptive Replanning

- 触发：STATIC_HTML 正文为空（JS shell）→ 切 Browser（仅传输层）
- 约束：不增加 Scope/域名/认证；replan 上限（默认 2）
- 实测 Replan Success Rate = **1.0**（10 个 dynamic_html 场景）

## 18. Deduplication

- URL canonicalization（scheme/host 折叠、噪音参数剔除）+ 内容 SHA-256 + 记录键
- 语义：同 URL 同内容 / 跨 URL 同内容 → duplicate；**同 URL 内容变化（replan 场景）→ 允许**
- 去重只标记 `duplicate_of`，**从不删除 Evidence**

## 19. Fact/Entity/Knowledge Integration

`extract_candidates`：CVE（KnowledgeCandidate+Facts）/ IP（EntityLinkCandidate）/ SHA（observed_indicator）
- **仅 Candidate**——必须经现有验证体系（Asset/Knowledge/SecurityFact 验证）后才成为正式记录
- Agent 绝不直接写已验证 SecurityFact / AssetRelation / Knowledge

## 20. Hybrid Intelligence Integration

- AcquisitionResult → Evidence → Fact Extraction → Knowledge Retrieval → Hybrid Reasoning
- 例：公开安全公告 → 提取 CVE → Knowledge Center → 关联 Asset → Investigation Context
- 采集 Agent 不承担最终安全判断（职责在 Hybrid/Investigation 层）

## 21. CAP-AQB v1 Dataset

- **124 场景**（≥100 ✓），全合成本地（SyntheticWeb 内存模拟，零真实站点压力）
- 26 类：static(15)/dynamic(10)/pagination(10)/infinite(5)/json(10)/pdf(8)/docx(6)/xlsx(4)/dup(6) + missing_field(5)/missing_time(4)/redirect(3)/redirect_private(3)/dns_rebinding(3)/robots(4)/401(3)/403(3)/login(2)/captcha(2)/paywall(2)/oversized(2)/malformed(3)/timeout(3)/rate_limit(2)/partial(3)/structure_change(3)
- **failure/blocked/partial 占比 37.9%**（≥20% ✓）

## 22. Evaluation Results

| 指标 | 结果 |
|---|---|
| Acquisition Success（COMPLETE） | 80/124 = 0.6452 |
| Blocked | 24 | 
| Partial | 20 |
| **场景通过率（expected status 匹配）** | **124/124 = 1.0（零失败）** |
| Extraction Accuracy | 0.6452（成功场景全提取；blocked 无提取属预期） |
| Lineage Completeness | 0.7661 |
| Strategy Selection Accuracy | 1.0 |
| Pagination Completion | 1.0 |
| Duplicate Detection Accuracy | 1.0 |
| Replan Success Rate | 1.0 |

## 23. Adversarial Results

| 测试 | 结果 |
|---|---|
| redirect to localhost / metadata / private | ✅ 全部 BLOCKED |
| DNS rebinding 模拟 | ✅ BLOCKED（fail-closed） |
| URL parser confusion（hex/十进制/IPv6 全零/userinfo） | ✅ 全部拒绝 |
| IPv6 loopback / mixed encoding | ✅ 拒绝 |
| malicious HTML instruction（prompt injection in page） | ✅ 作为 UNTRUSTED DATA 保留（Phase 25/27 边界处理） |
| huge response（12MiB > 10MiB limit） | ✅ SIZE_LIMIT |
| decompression bomb（压缩体直接拒绝） | ✅ 防御性拒绝 |
| infinite pagination / cyclic links | ✅ 预算内终止 |
| 恶意 PDF metadata | ✅ 解析失败记录 raw artifact（PARTIAL，不崩溃） |

## 24. Security Hard Gates

| 门禁 | 目标 | 实测 |
|---|---|---|
| SSRF Block Rate | 100% | ✅ **1.0** |
| Restricted Access Stop Rate（401/403/login/captcha/paywall） | 100% | ✅ **1.0** |
| Robots Compliance Rate | 100% | ✅ **1.0** |
| Unauthorized Scope Expansion | 0 | ✅ **0.0** |
| Captcha Bypass Attempt | 0 | ✅ **0** |
| Auth Bypass Attempt | 0 | ✅ **0** |
| WAF Bypass Attempt | 0 | ✅ **0** |

## 25. Observability

- `RunTracker`：steps（duration/bytes/retries/replanned）、total_requests/bytes、evidence_hashes、completeness_score、blocked_reason、strategy_history、urls_visited
- to_dict() 供 metrics/tracing/audit 持久化

## 26. Web Console

- 新增 **Data Acquisition** 页面（侧边栏 AGENTIC SECURITY 组）
- 展示：Goal / Asset / Status / Source Type / Strategy / Replans / Bytes / Requests / **Strategy History（为什么切换 Browser / 为什么停止）** / Completeness（coverage/field/time/verdict/gaps）/ Evidence（sha256/type/status/URL/tool）
- 前端 tsc 类型检查零错误（vite build 受 node_modules 既有损坏影响，v1 已知问题）

## 27. Database / API

- 新增表（仅元数据，payload 在对象存储）：`acquisition_runs/plans/steps/artifacts/extracted_documents/completeness_reports/public_endpoint_candidates`（app/acquisition/models_db.py）
- 未新增第二套 Asset/Evidence/Knowledge/SecurityFact（引用现有 evidence.id）
- API：`POST /acquisitions`、`GET /acquisitions`、`GET /acquisitions/{id}`、`POST /acquisitions/{id}/resume`、`GET /acquisitions/{id}/evidence`、`GET /acquisitions/{id}/completeness`
- **无** bypass/captcha/stealth/proxy-rotation/auth-bypass 端点（API 测试断言）

## 28. Architecture Compliance

- ✅ 复用 Worker/Sandbox（Adapter 为 Tool 层，生产运行于 Sandbox）
- ✅ 复用 Evidence（EvidenceService.save_capture 集成）
- ✅ 复用唯一 Capability Registry（新增 acquisition.http/browser/document/extract/paginate/discover/verify/public，无第二套）
- ✅ 复用 Agent Guardrails / Hybrid Intelligence（untrusted data 边界、候选制）
- ✅ 不建立第二套 Crawler Platform；不为 Acquisition 修改安全核心架构
- ✅ Agent 不直接访问网络

## 29. Coverage

- **Phase 28 新增代码全部 ≥95%（目标达成）**：

| 模块 | 覆盖率 |
|---|---|
| capabilities / dataset / models_db / observability / planner / __init__ | 100% |
| models | 99% |
| candidates | 98% |
| documentadapter | 97% |
| service / httpadapter / dedup / browseradapter / agent | 96% |
| urlpolicy / robots / pagination / evaluation / completeness | 95% |

- **全量回归：844 passed（零失败）**——v1 + P25 + P26 + P27 + P27.1 + P28（Phase 28 新增 12 个测试文件 150+ 用例）
- Ruff：All checks passed
- backend/app 整体覆盖率：**94%**（v1 遗留缺口拖累，v2 RC ≥95% 目标持续补测中）

## 30. Known Limitations

1. **Playwright 主流程未在真实浏览器上验证**（环境无浏览器二进制）：browseradapter 经 fake 对象验证契约，真实渲染需部署后集成测试
2. **Dynamic HTML 判定启发式**：正文为空才触发 replan——某些 JS 页有骨架文本会误判为静态
3. **robots.txt 解析为轻量实现**（User-agent 精确匹配，不支持通配符/最长匹配边界全部语义）
4. **Memory 检索型知识未接入**：FactCandidate→Knowledge 验证链路已建立，Hybrid 消费需 Phase 28.1
5. **PDF/DOCX 合成样本有限**：pypdf 对扫描版 PDF 无法提取文本（预期）
6. **URLPolicyValidator 用 DNS 解析做 rebinding 防护**：无缓存，每请求解析（性能可接受）
7. **API create_and_run 为同步阻塞执行**（生产应异步队列化）

## 31. Technical Debt

- `AcquisitionService` DB 持久化仅单元级验证（内存 DB 覆盖待全量）
- `resume` 端点为治理占位（仅状态检查，未实现断点续采）
- Web Console 详情页无分页/搜索（列表受限）
- browseradapter `_observe` 预留方法未使用（观察在 browse 内联实现）

## 32. Architect Review Preparation

**完成标准对照：**

| 完成项 | 状态 |
|---|---|
| ✅ Adaptive Data Acquisition Agent | 实现 + 测试 |
| ✅ Acquisition Planner | 实现 + 测试（strategy 1.0） |
| ✅ HTTP / Playwright / Document Adapter | 实现（真实后端） |
| ✅ Extraction Pipeline | ExtractedDocument + Lineage |
| ✅ Evidence Object Store | content-addressed immutable |
| ✅ Evidence Lineage | 全链测试 |
| ✅ SSRF Protection | 1.0 + 对抗测试 |
| ✅ Robots Policy | 1.0 |
| ✅ Pagination | 预算受限 + 循环防护 |
| ✅ Completeness Engine | 5 态 verdict |
| ✅ Adaptive Replanning | HTTP→Browser + scope 约束 |
| ✅ Deduplication | canonical+hash，标记不删 |
| ✅ Fact/Entity/Knowledge Candidate | 候选制 |
| ✅ Hybrid Integration | 链路建立 |
| ✅ CAP-AQB v1 | 124 场景（≥100） |
| ✅ Adversarial Tests | 全通过 |
| ✅ Security Hard Gates | 7 项全绿 |
| ✅ Observability | RunTracker |
| ✅ Web Console | Data Acquisition 页面 |
| ✅ Architecture Compliance | 全复用 |
| ✅ Coverage | Phase 28 模块 ≥95%（最终确认中） |

**完成后停止开发，等待 Architect Review。**
