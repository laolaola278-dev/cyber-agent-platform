# 发布节奏与 CI 盲区治理方案（缺陷驱动发布 → 预防驱动发布）

> 对应 Readiness 评估第 6 点：v1.0.0/1.0.1/1.0.2 四天三 GA 全部由缺陷驱动，
> 说明 v1.0.0 发布时的功能验证存在系统性盲区（典型案例：egress CONNECT 隧道缺陷在
> v1.0.0/v1.0.1 生产中必备的外网采集路径上存活了两个 GA，直到 v1.0.2-rc1 才被修复）。
> 本方案的目标：把"缺陷驱动的发布节奏"改造成"从盲区矩阵出发、预防驱动的发布节奏"，
> 全部复用既有 GATE / 分类器 / 认证生成器体系，不引入新框架。

## 0. 问题根因（先承认，再治理）

| 盲区类型 | 根因 | 本次真实案例 |
|---|---|---|
| 路径盲区 | CI 只覆盖单元/集成路径，未覆盖"产品承诺的真实端到端路径" | egress proxy CONNECT headers 残留 → 真实外网采集 BLOCKED(bytes=0)，本地单测"does not parse headers"反而把缺陷写成断言 |
| 断言盲区 | 测试断言的是"当前行为"，而非"产品承诺的行为" | 既有测试注释 `does not parse headers` 固化了缺陷语义 |
| 环境盲区 | 本地/CI 环境与生产环境隔离，真实外部依赖（外网、设备、第三方 API）不参与门禁 | CONNECT 隧道未做真实网络回归，CI 无法识别 |
| 节奏盲区 | 发布由"修了什么"驱动，而非"验证了什么"驱动 | 两次 GA 均为缺陷修复后的纯版本提升，认证继承机制掩盖了函数面缩水 |

## 1. 三层防线（对既有体系的增量，非替换）

```
┌─ 第 1 层：真实路径测试层（新增 CiBlindSpot 套件）
│   承诺路径 = 从产品文档/README/任务书提取的"用户可达端到端路径"
│   每个承诺路径必须有一个真实网络/真实依赖的探针测试（非 mock）
│   门禁：ci.yml 的 frontend/backend quality-gates 显式运行，缺测即红
│
├─ 第 2 层：盲区矩阵（新增 docs/quality/coverage-matrix.md，定期证伪）
│   矩阵行 = 承诺能力，列 = 验证层（单测/集成/真实网络/soak/认证）
│   任何"在某列空缺"的能力在发布前必须显式声明为 known limitation
│
└─ 第 3 层：缺陷驱动回溯（新增 release 前置步骤）
    任何修复类提交，release 前必须回答：
    a) 该缺陷是否来自 CI 盲区？→ 若是，盲区矩阵对应格立即标红待补
    b) 是否有测试在修复前断言了缺陷行为？→ 若有，全仓 grep 拆除旧断言
    c) 修复的端到端路径是否已有真实探针？→ 若没有，下个发布必须补齐
```

## 2. 具体落地项（按优先级）

### P0：真实路径测试层（堵"承诺路径无测试"）
- 在 `backend/tests` 常规套件中新增 **network-marker 真实网络探针**（已由 `cfd5289`
  示范：CONNECT 隧道真实取数）。凡 README/任务书声明的外部可达能力，
  必须有一条不依赖 mock 的端到端测试。
- **门禁化**：`quality-gates`（复用 ci.yml）增加一条**盲区矩阵一致性断言**——
  解析 `docs/quality/coverage-matrix.md`，若存在"承诺能力 × 无真实探针"且未标
  known limitation 的组合，quality-gates 失败。
- **新增 marker 必须注册**（`filterwarnings=error` 会把未知 marker 变错误），
  并接进 `pytest backend/tests` 常规套件（ci.yml 的收集范围）。

### P1：断言语义审计（拆"把缺陷写成断言"的测试）
- 全仓 grep 模式：`does not parse`、`not supported`、`raises NotImplemented`、
  `temporarily disabled`、`known limitation` 等描述当前行为而非承诺行为的注释，
  逐条确认是"真实限制"（保留）还是"缺陷语义固化"（改写断言+补真实探针）。
- 建议作为 rc 锚点认证前置检查项（GATE 13 的 targeted tests 扩展）。

### P2：盲区矩阵文档（让"什么没验证"可审计）
- 新建 `docs/quality/coverage-matrix.md`，格式示例：

```markdown
| 承诺能力 | 单测 | 集成 | 真实网络 | soak | 认证 | 状态 |
|---|---|---|---|---|---|---|
| 外网采集 CONNECT 隧道 | ✅ | ✅ | ✅(cfd5289) | — | GATE 24/25 | 已堵盲区 |
| EDR 阻断响应 | ✅ | ✅ | ❌ mock-only | — | GATE 13 | known limitation |
| Zeek TSV 摄入 | ✅ | ✅ | ❌ 拒绝 | — | — | known limitation |
```

- 发布报告必须引用该矩阵，任何空格子必须给理由。

### P3：发布节奏规则（让 GA 是"验证完成"而非"修完 bug"）
- GA release notes 模板增加固定小节：**"本版本验证路径变更 / 盲区清除"**，
  列出本轮新增的真实探针与清除的矩阵空格。
- 分类器 `classify_diff.py` 已能把 runtime-affecting 与 release-metadata 分开；
  在此基础上，**修复类提交的 GA 提升必须附带"盲区清除证据"**（矩阵中该格从 ❌→✅），
  否则不进入 release-metadata-only 继承路径。
- 节奏目标（示例）：从"缺陷驱动"改为"每 2~4 周一个可认证 rc，GA 仅在验证全绿时发生"。

## 3. 与既有机制的关系（复用而非另起炉灶）

- **40 门认证（GATE 1–40）**：不变，仍由 `CAP_GA_STRICT=1` 生成器在锚点派生。
- **fail-closed 分类器**：保持，仅新增"修复类提交须带盲区清除证据"的输入。
- **PATCH-GATE（20 门）**：把"盲区矩阵一致性"作为 patch 认证的输入之一。
- **soak（2h）**：盲区矩阵的 soak 列如实填写（24h 长稳仍未跑 → 空格 + known limitation）。

## 4. 验收标准

1. `docs/quality/coverage-matrix.md` 存在且被 quality-gates 解析（缺文件即 CI 红）。
2. 仓库中不存在"把缺陷行为写成断言"的测试（grep 审计通过）。
3. 任何生产可达的真实路径（外网采集/响应执行/工单外发）至少有一条真实网络探针。
4. v1.0.3 的 GA release notes 包含"验证路径变更 / 盲区清除"小节，且矩阵相应格为 ✅。
5. 下次缺陷驱动的发布（若有）触发强制回溯：先补盲区，再提升版本。