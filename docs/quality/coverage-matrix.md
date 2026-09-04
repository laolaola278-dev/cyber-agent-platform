# Coverage Matrix — 承诺能力 × 验证层盲区矩阵

> 治理来源：`docs/quality/ci-blindspot-governance.md` P2。
> 目的：让"什么没验证"可审计。每个空格必须给理由；`known limitation`
> 表示当前行为是**有意登记的限制**（release notes 必须引用），否则即为待清除的盲区。
>
> 维护纪律：修复类提交若声称"清除盲区"，必须把对应格从 ❌ 推进到 ✅ 并在本表
> 留下证据锚点（commit / gate 编号）。发布报告（`docs/releases/vX.Y.Z.md`）
> 必须引用本矩阵。

图例：✅ 已验证 · ❌ 未验证（盲区） · 🚫 设计性拒绝（有测试锚定拒绝行为） · — 不适用

| 承诺能力 | 单测 | 集成 | 真实网络 | soak | 认证 | 状态 |
|---|---|---|---|---|---|---|
| 外网采集 CONNECT 隧道（egress） | ✅ | ✅ | ✅ (`cfd5289`, `test_phase_27_probe.py`) | — | GA-GATE 24/25 | 已堵盲区 |
| Linux 运行时网络探针 | ✅ | ✅ | ✅ (`test_phase_28_5_linux_network.py`) | — | Phase 28.5-L | 已堵盲区 |
| Zeek JSONL 摄入（Adapter→Telemetry→Detection 三件套） | ✅ (`test_phase_13_*`) | ✅ | ❌ 需真实 Zeek 传感器 | — | — | 盲区：无传感器环境，缓解=fixture 回放 |
| Zeek TSV 摄入 | ✅ 锚定拒绝 (`tools/zeek/adapter.py:107`) | — | — | — | — | 🚫 known limitation（设计性拒绝，带 remediation） |
| Suricata EVE JSON 摄入 | ✅ | ✅ | ❌ 需真实 sensor | — | — | 盲区：同 Zeek，缓解=fixture 回放 |
| Nuclei 扫描结果归一化 | ✅ | ✅ | ❌ 需真实 nuclei 二进制 | — | — | 盲区：CI 不装外部扫描器，缓解=fixture 回放 |
| ZAP 扫描结果归一化 | ✅ | ✅ | ❌ 需真实 ZAP | — | — | 盲区：同上 |
| EDR 阻断响应 | ✅ (`test_phase_19_edr_response.py`) | ✅ | ❌ mock-only (`fake_plugin.py`) | — | GA-GATE 13 | 🚫 known limitation（v1.0.2 起登记） |
| WAF 拦截响应 | ✅ (`test_phase_16_waf_response.py`) | ✅ | ❌ mock-only | — | GA-GATE 13 | 🚫 known limitation（同上） |
| Firewall 阻断响应 | ✅ (`test_phase_17_firewall_response.py`) | ✅ | ❌ mock-only | — | GA-GATE 13 | 🚫 known limitation（同上） |
| Notification（邮件/webhook 出站） | ✅ | ✅ | ❌ 无真实 SMTP/webhook 探针 | — | — | 盲区：待真实出站探针（P0 候选） |
| Ticket（ITSM 工单出站） | ✅ | ✅ | ❌ 无真实 ITSM | — | — | 盲区：同上 |
| cancel/complete 线性化契约 | ✅ DB-atomic 证明 | ✅ SQLite+PG 双权威 | — | ✅ | heartbeat_invariant 三项 PASS（v1.0.3 起） | 已堵盲区（`2e4d0b1`/`4bc5169`） |
| 执行租约误回收（heartbeat） | ✅ | ✅ 静态+SQLite+PG | — | ✅ | 同上 | 已堵盲区（同上） |
| DR 恢复（RPO/RTO） | ✅ | ✅ | — | ✅ | GA-GATE 2..4（RPO 12.19s / RTO 210.48s） | 已验证 |
| 容量包络（1/2/4 副本 × 100/500/1000） | ✅ | ✅ | — | — | GA-GATE 27 | 已验证 |
| 升级/回滚（Helm） | — | ✅ | — | ✅ upgrade 6.5s / rollback 0.6s | K8s Certification | 已验证 |
| 供应链（SBOM/Trivy/锁文件） | — | ✅ | — | — | GA supply-chain job + GA-GATE 33 | 已验证 |
| SLO 强制执行 | — | — | — | — | — | 盲区（设计如此）：`slo-candidates.json` 为候选，转正待满月生产数据 |
| SLI 生产导出 | — | — | — | — | — | 盲区：GA 报告 `sli` 块无生产者（转正 SLO 时同步解决） |
| 24 小时长稳 | — | — | — | ❌（认证 soak 为 7200s） | — | 🚫 known limitation（v1.0.2 起登记） |

## 空格理由汇总

- **真实传感器类（Zeek/Suricata/Nuclei/ZAP）**：CI runner 不安装外部安全设备/二进制；
  当前以 fixture 回放覆盖解析与归一化路径。清除条件 = 引入带真实工具的 self-hosted runner 或容器化 sensor job。
- **Response 三家 provider（EDR/WAF/FW）**：产品内无真实设备集成，`fake_plugin.py`
  为 v1 冻结契约下的既定实现；清除 = Phase 29+ 真实 provider 立项（MAJOR/MINOR 评估见 roadmap）。
- **出站通知/工单**：缺真实 SMTP/ITSM 探针，属 P0"真实路径测试层"下一步候选。
- **SLO/SLI**：按 GA 认证设计，sli 块无生产者不是缺陷；转正流程见 `slo-candidates.json` note。
