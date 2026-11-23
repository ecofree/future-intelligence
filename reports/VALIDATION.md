# 当前验收索引

更新于 2026-11-23。各项验收绑定其提交和运行环境。

## 当前整合

2026-11-23 在隔离 Windows 工作副本以 Python 3.11.15、Git OpenSSL 3.5.4 运行 `python -m unittest discover -s tests -v`，发现 420 项，417 项通过。两个文件符号链接用例受当前账户权限限制，另一个用例需要 POSIX FIFO。实际 NTFS ACL、含换行和控制字节的时戳印记、CRL 与证书链均有通过的回归。模型请求正文固定摘要回归通过。Windows 的 `runtime check-egress` 将四个 `198.18.*` 响应全部拒绝，退出码为 2。本次未领取历史任务，新增模型调用与费用为零，尚无正式实验 ID。独立清单、时间精度和候选 worker 复测入口见[出网与 TSA 核验](../docs/FORMAL_EGRESS_TSA_REVIEW.md)。

本机独立 PostgreSQL 17.12 在 Windows 兼容性修复后的基线运行通过 231 项集成测试，含 8 条升级路径。在正式 Terra worker 加入计数与响应门控后，`test_terra_generation_db` 的 7 项再测通过。测试使用临时数据库、模拟模型响应与测试 CA，缺少或错绑计数时实际 attempt 和预算预留均为零。数据库保存有效概率和未知费用后，模型不匹配会得到阻断结果。

`2000cb6` 的完整本机 PostgreSQL 17.12 回归通过 232 项。GitHub 的 PostgreSQL 16 和 17 各通过 232 项，三个 Python 版本各通过 420 项，独立安装逐文件核对 62 个包文件及 21 份 SQL。Chromium 的 205 行分页、备份恢复与重启检查通过。候选 worker 的同一次普通解析与 TLS 1.3 握手覆盖 OpenAI、两个 BLS 域名、FreeTSA 和 APED 五个域名，记录了公网 peer、SNI 和证书指纹。该运行使用临时数据库或空供应商密钥，未取得原实验库的唯一写入权。[候选 worker 原始作业](https://github.com/ecofree/future-intelligence/actions/runs/45165644834/job/137661771700) · [数据库回归](https://github.com/ecofree/future-intelligence/actions/runs/45165644670) · [离线回归](https://github.com/ecofree/future-intelligence/actions/runs/45165644696) · [安装包](https://github.com/ecofree/future-intelligence/actions/runs/45165644591)

随后为 APED 的一次性样本增加手动入口。Windows 离线回归发现 422 项，419 项通过，3 项因平台或账户权限跳过。新增两项覆盖公网检查失败时零请求、一次请求后保留原字节以及禁止覆盖已有取样目录。任务附带回执用本地接收证书加密，公开日志只记录摘要和传输状态。

**2026-11-19 现场交接续报：只读审计与独立副本升级已执行，普通出网与 TSA 独立审核仍阻断。** 新包 `29a834bb…fa86c` 的 ZIP 与 13 项清单逐项匹配；21 份迁移 SHA/字节数、旧 19 条迁移记录和可比较业务/预算摘要与 41daeea、9f0a994 交叉一致。原环境日志记录离线 413、集成 231 项通过；本会话没有重跑原库测试。完整目录/角色成员闭包、未附原文和缺少共同字段的源库全量前后指纹不扩大为已独立复算，详见 [正式入场交接复核](FORMAL_ADMISSION_HANDOFF_AUDIT.md)。

发现原文字报告将实验 manifest `c0a4a6bc…` 与证据封存 manifest `75ec6d15…` 混为同值；JSON 和旧包复算可区分，本批追加更正，不改原包或实验。7f 前置实验截至审计零领取/派发/预留，但两个任务仍 QUEUED；旧未知占用 **$0.84** 保留。恢复执行继续指定新实验/任务。当前外部工作仅为授权管理层恢复正常出网及完成有依据的 TSA 信任/量化精度/CRL 策略审核；不新增控制器、迁移或收费调用。FORMAL、正式时间证明和自动结算均未创建。

### 前一批代码验收（保留实现时点）

**2026-11-19：补齐撤销检查与生产时间资格。** 以 4575ddb 为起点，旧 NOT_CHECKED 报告可贡献生产资格的缺陷由前向 0020 修复；原 **20 份迁移逐字未改**，现共 21 份。严格离线 CRL profile/report v2、当前派发窗口、历史 score v3 与独立导出复验见 [ADR-025](../docs/adr/025-offline-crl-admission.md) 和 [Terra 审计第 6 节](TERRA_DIAGNOSTIC_AUDIT.md)。不改模型请求、旧诊断、已应用校验和或 UNKNOWN 费用。

本地 Python 3.12 的 `python -m unittest discover -s tests -v` **413 项通过**，其中新增 28 项 CRL、10 项导出复验及 3 项 CLI 回归；`compileall`、差分空白检查通过。真实本地签名的三层测试链覆盖叶和中间证书撤销、缺失上级/下级 CRL、签名损坏、未来/过期/超龄材料、证书混入和严格时间边界。没有用测试 CA 冒充生产证明。

wheel 与仓库外隔离安装的 **61 个包文件 / 21 份迁移**逐项匹配，摘要见本批审计。新增 10 项隔离数据库回归及第八条升级路径，比较当前函数、视图、权限、约束、旧行和迁移历史；0018/0019 原历史回归分别保留明确终点。本会话没有原宿主或 PostgreSQL 执行入口，数据库结果须读取本次提交自己的 CI，不沿用旧 220 项绿灯。实际 CI 和失败记录继续记入本批审计与 PR #12。

已保存公开 CA/叶证书/CRL 和过期旧叶证书的原字节与 OpenSSL 离线输出，审计附件 SHA256 `cebbe4070117a11ad5db9b3c4cad30fa4fb5a8fae01f2a8a6162433764a8533f`。叶证书链/用途与 CRL 检查成功；德文摘要对应已过期旧证书。没有实际生产 token、正常直连出网或独立信任批准，不能据此创建 FORMAL。当前调用 0、费用 0，已有未知占用 **$0.84 / $1.00** 保留，主分支不改、不部署。

首轮 dae40793 的离线、打包、备份还原与 205 行浏览器通过；PG16/17 各 231 项中八条升级路径通过，两个新增测试夹具分别触发了正确的身份隔离和时间上界门控，整体数据库未通过。后续只修测试身份与等待条件，保留原断言、生产权限和 21 份 SQL；[失败与修复详情](TERRA_DIAGNOSTIC_AUDIT.md)及本次新 HEAD 的实际 CI 结果由 PR #12 保留，不抹去失败记录。

### 前一批整合状态（保留原时点）

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
