# CAP Phase 28 — GitHub Reference Analysis

**阶段：** v2.0 Phase 28 — Adaptive Data Acquisition Agent
**日期：** 2026-08-09
**性质：** 开发前架构基准分析——决定"采用什么 / 不采用什么 / 哪些成熟能力通过 Adapter 复用"。
**总原则：** 禁止为了 CAP 重写成熟 Browser/Crawler/Parser；CAP 的安全边界（public-only、SSRF 防护、无绕过）高于第三方便利能力。

---

## 1. Scrapy — `github.com/scrapy/scrapy`

### 重点架构
| 组件 | 机制 |
|---|---|
| Engine | 组件间数据流中枢（事件驱动） |
| Scheduler | Request 去重 + 优先级队列 |
| Downloader | HTTP(S) 请求、重定向、并发控制 |
| Spider | 用户解析逻辑：Response→Item/新 Request |
| Item Pipeline | 清洗/验证/去重/存储 |
| Retry | RetryMiddleware：状态码(500/502/503/504/408)+连接错误，RETRY_TIMES=2 |
| Request/Response | 封装 URL/method/headers/meta/callback |
| Concurrency | Twisted 异步；CONCURRENT_REQUESTS/PER_DOMAIN/PER_IP |

### 决策：**不采用为运行时**；**借鉴设计语义**
- **不采用原因**：① Twisted 事件循环与平台 asyncio/httpx 栈冲突（scrapy-playwright 需特殊 reactor 桥接）；② 框架重、学习曲线高，CAP 不需要全量 Spider 生态；③ 原生无 JS 渲染（动态页需额外集成）；④ 其代理轮换/UA 轮换等生态组件与 Phase 28 安全边界（禁绕过）冲突。
- **借鉴语义**（在 CAP Tool Adapter 层实现等价物，非重写框架）：
  - **Retry 语义**：可重试状态码集合 + 连接错误 + 最大次数（CAP 的 `AcquisitionPolicy.retry`）
  - **Rate Limit 思想**（AutoThrottle）：请求速率上限（CAP 的 `request_rate`）
  - **Pipeline 阶段分离**：下载→解析→去重→存储的职责链（CAP 的 Adapter→Extraction→Store→Evidence）

## 2. Playwright — `github.com/microsoft/playwright`

### 重点架构
| 组件 | 机制 |
|---|---|
| Browser | 浏览器进程（chromium/firefox/webkit） |
| BrowserContext | 独立会话/配置文件隔离 |
| Page | 标签页；goto/渲染/交互/DOM |
| Network events | page.route/on("request"/"response") 网络观察与拦截 |
| Navigation | 自动等待、domcontentloaded |
| Rendering | 完整 JS 渲染、截图/PDF |

### 决策：**采用（已有平台集成 `app/tools/playwright`）**
- CAP 已实现 `PlaywrightAdapter`（public-web 限定：仅 GET、禁 cookies/headers/credentials/proxy 注入）+ `BrowserManager`（context 隔离）。
- **Phase 28 复用并扩展**：
  - `Browser/Context/Page` 三层 → 直接复用现有 BrowserManager
  - **Network events** → 观察页面正常前端发出的 XHR/Fetch → `PublicEndpointCandidate`（OBSERVED）
  - DOM snapshot + 受限等待条件 → Dynamic Page Acquisition
- **不采用**：Playwright 的 stealth/反检测生态（与本阶段边界冲突，不实现）。

## 3. Crawl4AI — `github.com/unclecode/crawl4ai`

### 重点架构
- **Structured extraction**：分层策略 JsonCss/JsonXPath/Regex/Cosine/LLM
- **Crawler strategy**：BFS/DFS/BestFirst + URL 过滤器 + 自适应爬取（判断何时已获取足够）
- **Markdown extraction**：HTML→Markdown（Fit Markdown + BM25/Pruning 过滤器）
- **Browser integration**：基于 Playwright；网络请求捕获（调试/API 发现）；含 StealthAdapter（反检测）

### 决策：**不采用为依赖**；**借鉴**
- **不采用原因**：① 版本演进破坏性变更频繁（0.5→0.7→0.9 API 重构）；② 依赖重（Playwright+多提取引擎）；③ **StealthAdapter 反检测能力与 Phase 28 安全边界直接冲突**。
- **借鉴**：
  - **分层提取策略思想** → CAP `Content Extraction Adapter` 提供结构化提取（标题/正文/表格/链接）
  - **网络请求捕获做 API 观察** → CAP `PublicEndpointCandidate`（只观察页面正常公开请求，不猜测 path）

## 4. Firecrawl — `github.com/firecrawl/firecrawl`

### 重点架构
- **Crawl job**：POST /v2/crawl → job ID → 轮询 status（completed/total/creditsUsed）
- **Map**：即时发现网站 URL 链接
- **Scrape**：URL→Markdown/HTML/结构化 JSON
- **Document pipeline**：代理轮换/请求编排/JS 渲染/PDF-DOCX 解析（云服务）
- **Result model**：success + data（markdown + metadata）

### 决策：**不采用**；**借鉴**
- **不采用原因**：① AGPL-3.0 许可（商用合规风险）；② 核心能力依赖托管云服务（需 API key）；③ 自动代理轮换等反检测能力与安全边界冲突。
- **借鉴**：
  - **Job 状态模型**（提交→轮询→completed） → CAP `AcquisitionRun` 生命周期（PENDING/RUNNING/COMPLETE/PARTIAL/BLOCKED）
  - **Result model**（markdown + metadata + sourceURL） → CAP `ExtractedDocument`（text + metadata + source_url）

## 5. Trafilatura — `github.com/adbar/trafilatura`

### 重点架构
- **Main content extraction**：正文提取（jusText/readability 算法），噪声剔除（导航/页脚）
- **Metadata**：title/author/date/site/categories/tags
- **Deduplication**：URL 管理、过滤、去重
- 输出 TXT/MD/CSV/JSON/HTML/XML；Apache-2.0 许可

### 决策：**优先采用方向（Adapter 后端）**
- Trafilatura 是正文提取领域开源基准第一梯队（ACL 2021 论文、ScrapingHub benchmark 最优）。
- **本阶段环境该库不可用** → 实现 `Content Extraction Adapter` **契约** + 标准库 HTML 解析真实实现（html.parser 提取 title/正文/链接/表格），并**在 Manifest 中声明 extraction_backend="stdlib-html"**（非 Trafilatura 集成，不伪报）。
- **未来**：安装 trafilatura 后仅切换 adapter 后端（backend="trafilatura"），接口不变。
- **借鉴**：正文噪声剔除思想（跳过 nav/script/style/footer）、元数据字段规范化。

## 6. Apache Tika — `github.com/apache/tika`

### 重点架构
- **Document type detection**：tika-detectors（MIME/魔数/编码检测）
- **Document extraction**：tika-parsers（1000+ 格式，SAX 事件流→XHTML）
- **Metadata**：tika-xmp + metadata key registry
- 部署：嵌入式库 / tika-server REST / gRPC / CLI

### 决策：**不采用本阶段**；**借鉴检测语义**
- **不采用原因**：① JVM 依赖 + tika-server 进程管理重（与轻量 Worker 部署不匹配）；② Python 生态下 pypdf/python-docx 已覆盖本阶段 4 种格式。
- **借鉴**：
  - **类型检测思路**（MIME/魔数） → CAP `Document Adapter` 的 `detect_document_type`（扩展名 + 魔数嗅探 + Content-Type）
  - **元数据规范化** → `ExtractedDocument.metadata` 统一键

## 7. Unstructured — `github.com/Unstructured-IO/unstructured`

### 重点架构
- **Partition**：PDF/Office/HTML→语义元素（Title/Text/Table/TableChunk）
- **Chunk**：按语义分块（RAG 友好）
- **Metadata**：ElementMetadata（坐标/URL/表格 id）
- **安全**：HTML 输出 sanitization（XSS 防护：标签白名单、URL 协议过滤）

### 决策：**不采用本阶段**；**借鉴**
- **不采用原因**：① 依赖极重（OCR/transformers 可选链）；② 最新版要求 Python 3.12+（平台 3.13 满足但依赖爆炸）；③ 重型能力超出本阶段 4 格式范围。
- **借鉴**：
  - **分区语义**（Title/Text/Table 元素） → `ExtractedDocument.sections/tables` 结构
  - **HTML sanitization 思路** → 页面内容进入 Extraction 前做**注入安全边界**处理（Phase 25/27 的 untrusted-data 边界，`isolate_untrusted_data`）

## 8. Scrapy-Playwright — `github.com/kinoute/scrapy-playwright`

### 重点架构
- 静态爬取与浏览器渲染的**按需边界**：`meta={"playwright": True}` 逐 Request 选择渲染路径
- Playwright Page 对象经 `playwright_include_page` 回传回调交互
- Twisted asyncio reactor 桥接；PageMethod 预定义等待/滚动操作

### 决策：**不采用**；**借鉴边界决策**
- **不采用原因**：① **Windows 不支持原生**（ProactorEventLoop/Twisted 冲突，本项目在 Windows 开发）；② 代理支持/上下文缓存等能力超出边界。
- **借鉴（关键）**：
  - **静态/动态按需切换** → CAP `Adaptive Replanning` 的 **HTTP→Browser 策略切换**（同一目标 URL，页面结构表明 JS 渲染时才切 browser capability）
  - **预定义页面操作（PageMethod）** → CAP Dynamic Acquisition 的受限等待条件（wait_for_selector/等待网络空闲）

---

## 采用/不采用汇总表

| 项目 | 采用 | 复用方式 | 不采用原因 |
|---|---|---|---|
| Scrapy | 语义借鉴 | Retry/RateLimit/Pipeline 分离 | Twisted 栈冲突、生态含绕过组件 |
| Playwright | ✅ 采用 | 已有 PlaywrightAdapter+BrowserManager，扩展 Network observation | —（禁 stealth） |
| Crawl4AI | 语义借鉴 | 分层提取、网络观察 | 版本动荡、StealthAdapter 冲突 |
| Firecrawl | 语义借鉴 | Job 状态模型、Result model | AGPL、云服务依赖、代理轮换 |
| Trafilatura | 预留后端 | Extraction Adapter 契约；本阶段 stdlib 真实实现 | 环境不可用（不伪报） |
| Apache Tika | 语义借鉴 | 类型检测/MIME、元数据键 | JVM/服务器重 |
| Unstructured | 语义借鉴 | 分区语义、HTML sanitization | 依赖重、超出范围 |
| Scrapy-Playwright | 边界借鉴 | HTTP/Browser 按需切换 | Windows 不支持、Twisted 耦合 |

**成熟能力 Adapter 复用清单**：Playwright（浏览器）、httpx（HTTP 客户端）、pypdf/python-docx/openpyxl（文档解析，若安装成功）、标准库 html.parser/json/zipfile（HTML/JSON/DOCX 兜底）。
