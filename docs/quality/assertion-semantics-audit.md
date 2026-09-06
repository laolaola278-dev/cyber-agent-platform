# P1 断言语义审计报告（1.0.5 线）

> 对应 `ci-blindspot-governance.md` P1 项：拆除"把缺陷行为写成断言"的测试。
> 历史反面教材：egress CONNECT 隧道缺陷曾在 v1.0.0/v1.0.1 存活两个 GA——
> 既有测试注释 `does not parse headers` 把缺陷固化为"当前行为"，而非承诺行为。

## 1. 审计方法

全仓 grep 以下模式（tests + production code 双侧）：

| 模式族 | 覆盖模式 |
|---|---|
| 治理文档点名 | `does not parse` / `not supported` / `raises NotImplemented` / `temporarily disabled` / `known limitation` |
| 隐蔽缺陷语义 | `current behavior` / `currently (not\|fails\|only)` / `workaround` / `for now` / `at the moment` / `not implemented yet` / `TODO` / `FIXME` / `stub` |
| 历史 HUD | `does not parse headers`（v1.0.0 egress 缺陷的具体断言文本） |

扫描范围：`backend/tests`（全部 `test_phase_*.py`）、`backend/app`（生产代码）、`frontend/src`（全部 ts/tsx）。

## 2. 审计结果：零缺陷语义固化

### 2.1 全部命中点的定性

| 命中点 | 定性 | 理由 |
|---|---|---|
| `backend/app/tools/zeek/adapter.py:119` — `"Zeek TSV input is not supported; configure Zeek to write JSON logs …"` | **真实限制（保留）** | 设计性拒绝：错误消息显式命名 supported format（jsonl）与 remediation（`LogAscii::use_json=T`），有 `test_zeek_tsv_error_names_the_remediation`（PATCH-GATE 12）与 `test_zeek_tsv_error_carries_structured_details` 双断言其可操作性，且 `test_zeek_jsonl_parsing_still_works` 守护边界披露不等于适配器损坏。矩阵已锚定 |
| `backend/tests/test_phase_13_zeek.py:146` — `match="TSV input is not supported"` | **真实限制（保留）** | 断言的是承诺行为（拒绝必须可操作：命名 supported format + remediation），非固化缺陷。v1.0.1 修复时已从 "reserved" 改写为可操作消息（行内注释见 v1.0.1 更新说明） |
| `backend/tests/test_phase_28_8_capability_disclosure.py:252` — `assert "not supported" in message` | **真实限制（保留）** | 断言错误消息包含 remediation（`LogAscii::use_json=T`）——断言承诺行为而非当前行为 |
| `backend/app/workflow/nodes.py:70` — `"Approval provider is not implemented in Phase 3"` | **真实限制（保留）** | 预留接口的能力诚实（capability honesty），`test_phase_28_8_capability_disclosure.py:200 test_reserved_interfaces_are_disclosed` 治理其披露义务 |
| `backend/app/plugins/edr/plugin.py:74` + `backend/app/tools/edr/policy.py:60` — `"EDR action is reserved and not implemented in Phase 19"` | **真实限制（保留）** | reserved 动作被 `test_phase_19_edr_response.py` 的 policy 层（:315）与 plugin 层（:373）双重断言拒绝，且 disclosure 测试治理——预留即安全边界，拒绝即承诺行为 |
| `backend/app/acquisition/*.py` 中多处 `does NOT`/`does not` 注释 | **非断言（文档注释）** | 均为架构不变式文档（claim 不建第二租约系统、worker_main 不用 synthetic runtime、worker_path 不绕过 DB-atomic claim）——这些是"承诺语义"的正向描述 |
| `backend/tests` 中 38 处 `pytest.skip`/`skipif`（PG/MinIO/docker/kind 不可达） | **合法环境守卫（保留）** | 环境可达性守卫，非缺陷掩盖。GA 链在 CI 内由可达环境全量执行（strict 模式 SKIP=FAIL）下这些 skip 不会触发 |

### 2.2 历史反面教材已清除确认

- `does not parse headers`：**全仓零命中**——v1.0.2-rc1 修复 egress CONNECT 缺陷时已拆除，1.0.5 线确认无复发
- `temporarily disabled` / `not implemented yet` / `workaround` / `for now`：**全仓零命中**（tests + app + frontend 三域）
- `TODO`/`FIXME`/`stub`：**backend/app 与 frontend/src 均零命中**

## 3. 结论

P1 断言语义审计 **通过**：仓库当前不存在"把缺陷行为写成断言"的测试。全部 "not supported/not implemented" 类命中均为：

1. **设计性拒绝**（Zeek TSV/JSONL 边界）——错误消息可操作化已由 v1.0.1 完成，且有结构化 details + remediation 双断言 + JSONL 正路径回归测试
2. **预留接口的能力诚实**（Phase 3 approval / Phase 19 EDR reserved action）——预留即安全默认，拒绝即承诺行为，且被 capability disclosure 测试套件治理

无需任何代码/测试改动。本报告作为 1.0.5-rc1 锚点认证的前置审计证据（GATE 13 targeted tests 扩展），发布说明可引用本报告作为"盲区清除/验证路径变更"小节的 P1 项闭环凭证。

## 4. 治理文档验收标准对照

| 治理文档验收标准 | 本审计结论 |
|---|---|
| "仓库中不存在把缺陷行为写成断言的测试（grep 审计通过）" | ✅ 通过（本报告 §2） |
| P1 建议"作为 rc 锚点认证前置检查项（GATE 13 的 targeted tests 扩展）" | ✅ 本报告即前置审计证据，1.0.5-rc1 切出时引用 |
