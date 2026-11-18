# 当前验收索引

更新：2026-11-18。验收绑定具体提交；历史报告不改写。整合分支不是生产部署。

## 当前整合

**2026-11-18 19:35 最新交接：[Terra 诊断审计](TERRA_DIAGNOSTIC_AUDIT.md)。** ZIP 和 58 项清单匹配；新实验 `e1e67317-4416-445b-b5c5-2f0197bf251d` 两题已成功保存，先二元验收再五类别。规范 9f0a994 请求字节、计数回执和完整 v2 规则复算通过；旧现场与规范库的差异已定位，DoH 源码补齐。实际 Terra 库完整指纹、冻结链只读导出和前置规划失败实验的零领取/零预留仍待补证。不能从旧 v1 observation 恢复返回的 reasoning/service_tier。

本批新增前向 0019 和 [ADR-024](../docs/adr/024-provider-observation-metadata.md)，仅向前保存实际响应参数标签和两个 ID，旧库 prepare 前 capability 门控；原 19 份 SQL 与请求正文不变。离线 **372 项通过**，本批数据库/升级/打包/CI 结果见新审计第 5 节，绑定本次 PR HEAD，不借用旧提交通过状态。

当前保留未知预留 **$0.84**，尚未承诺 **$0.16**；未来两次 Terra FORMAL 各 $0.07 的条件计划合计 **$0.98**。本批真实新增费用 **$0**，不为补日志重跑。当前进入 [正常出网与 TSA 审核](../docs/FORMAL_EGRESS_TSA_REVIEW.md)，宿主 fake-IP、生产独立信任、精度与撤销策略尚未通过；无 FORMAL、生产时间证明和自动结算。

### 之前各批次状态（保留时点）

[Terra整合与显式请求合同](TERRA_INTEGRATION_VALIDATION.md)：已取得integration/pr10-official-retest的4d4b91a与脱敏交接包，以双父提交bce7458整合PR11的c9cbcae；保留双方历史和原16份SQL。新0016固定官方Terra生成参数。当前提交的完整CI结果须按本批PR实际记录，不沿用父分支状态。操作与历史差异分析见[官方复测说明](../docs/OFFICIAL_TERRA_RETEST.md)。

2026-11-18 最新续报：[Sol 交接审计](SOL_DIAGNOSTIC_AUDIT.md) 的 ZIP 与 33 项清单 SHA256 已核验。独立诊断 `1644360e-62e6-472c-aaee-c0c19106ab52` 两次 HTTP 200，返回 gpt-6.2-sol、先五类别后二元；不是当前官方 Terra 合同的通过证据。两次输入计数 400、运行时 Python/SQL 覆盖、未交接的 DoH 注入及首发 +1µs 问题均记录保留。未知推理预留合计 **0.70 美元**，FORMAL 和生产时间证明未创建。

本批新增 0017 指定任务领取、0018 严格预测截止，保留原 17 份迁移。输入计数纯函数与受控 worker 门控不绕过失败；新评分语义标 fi.score.v2，旧记录不改。实现与边界见 [ADR-023](../docs/adr/023-controlled-retest-boundaries.md)；本提交测试、打包及 CI 结果见新审计第 5 节，未取得结果不沿用父提交通过状态。

全部已发生记录和费用持有不变。官方已明确输入计数免费；这不使 400 计数有效。若新 Terra 完整输入计数和适用报价均满足条件，下一轮两题诊断和两题 FORMAL 各预留 0.07 后总计划为 **0.98 美元**，不是立即执行或重试授权。正常网络、完整规则冻结、TSA 信任与独立入场仍待完成。

## 已有报告（保持各自时点与范围）

- b04085d：401 持久化后停止/非零退出与 CPI 离线算术；321 项离线、185 项数据库、56 个包文件/17 迁移，以及原四升级路径和 205 行列表浏览器检查在该提交通过。此前 `.44 + .28 = .72` 预算已被最新 Sol 记录更新；详见 [整合报告第 5 节](TERRA_INTEGRATION_VALIDATION.md)。
- [历史迁移顺序漏检修复](HISTORY_ORDER_AUDIT_FIX.md)：PR #11，代码c9cbcae；268项离线、同一套167项PG16/17与部署/打包检查见该PR实测留言。哈希一致不代表执行顺序或最终定义已核验。
- [累积列表分页与只读迁移核对](CUMULATIVE_PAGINATION_VALIDATION.md)：097ce460，256项离线/164项数据库；205记录分页和精确查询。
- [Grok兼容通道历史诊断](OPENAI_COMPATIBLE_VALIDATION.md)：保留f5f66a8/f9eafb1阶段记录。不是当前仅官方Terra试点的参赛资格。
- [显式答案合同v2](CONTRACT_FIX_VALIDATION.md)：a31e1e9，231项离线/158项数据库；旧提示词和失败记录保留。
- [工程预览基线](PREVIEW_BASELINE_VALIDATION.md)、[运行准备](OPERATIONAL_READINESS_VALIDATION.md)、[P0-04～07](P0_04_07_VALIDATION.md)。
- [P0-03](P0_03_VALIDATION.md)、[P0-02](P0_02_VALIDATION.md)、[P0-01](P0_01_VALIDATION.md)、[初始化](BOOTSTRAP_VALIDATION.md)、[初始化发布](GITHUB_BOOTSTRAP.md)。

本轮只允许官方OpenAI Responses、gpt-6.1-terra，总新增调用预算不超过1美元。不调用中转，不换模型；兼容代码保留只为保护原历史。未读取运行方原始请求/证据/账单，不将脱敏摘要等同完整独立验收。没有新FORMAL、生产时间证明或结算任务；模型真实费用仍须与供应商核对。主分支和现有PR不自动合并。
