# 2026-11-18 Terra 诊断交接审计

本报告基于收到的脱敏包、规范提交 9f0a994、离线复算与公开官方资料。没有连接原宿主或源数据库，没有执行附件 Python/SQL，没有新模型调用。原研究文本、完整运行记录及预测值不提交公开仓库。

## 1. 已核验的进展

ZIP SHA256 为 `0498869c07597e73e07cce7ea306b93a78cfc8e0bb1a018a963bed74d26a23a7`；59 个文件条目无重复、路径穿越或符号链接，58 项清单全部匹配。DoH 文件 SHA256 为 `6e500a56702cc15b57b7534fca6a81eb024ef4f30dd0bfb3a108dafc2cdb389f`，与内嵌证据相符。文件哈希只证明收到的材料一致，不等于独立可信时间。

新 DIAGNOSTIC：`e1e67317-4416-445b-b5c5-2f0197bf251d`；库 `fi_pr12_terra_diag`。两次官方 Responses 的保存记录均为 HTTP 200、gpt-6.1-terra 和成功概率输出。使用 9f0a994 的纯构建/计数函数重建，两题生成正文逐字节相同，生成请求、计数请求、计数响应三个哈希，以及 worker 绑定的回执摘要均相符。

| 题型 | 计数 / 实际输入 / 输出 token | 发出 → attempt 入账结束（北京时间） | 费用状态 |
|---|---:|---|---|
| 二元 | 3170 / 3170 / 756 | 19:34:27.708341 → 19:34:40.061235 | $0.07 HELD_UNKNOWN |
| 四边界五类别 | 3388 / 3388 / 805 | 19:34:40.905973 → 19:34:55.600990 | $0.07 HELD_UNKNOWN |

二元 worker 于 19:34:40.098173 退出，五类别计数于 19:34:40.106799 开始；驱动先读已保存结果并验收，才进入下一题。这次不再依赖默认 UUID 排序。两个完整规则版本的 UTF-8 字节与 question_spec、规则文件和证据引用一致，规则 SHA256 为 `d9650e1c1b2b99df0bf0d2eac83d57aefc227a2aac404244ee8899e200ccda23`。两个概率向量长度正确、和为 1,000,000。

请求明确固定 medium/standard/default；temperature、top_p、seed 按合同省略，不视为已锁定。预测截止排除、结果下界包含同一瞬间：2026-12-10 21:30:00 北京时间。没有 +1 微秒，完整 v2 首发条款已进入实际请求。337.511、324.122 与包内对应 BLS Table 1 快照及其哈希一致。

### 证据边界

旧 v1 observation 未保存返回 reasoning/service_tier，包内也没有原生成 HTTP 正文，故本报告只确认**发送配置**与已保存模型/输出；不能独立确认服务端返回的全部参数标签。0019/v2 仅向前补齐，不回填这次记录，不为补日志再收费重跑。

包内尚缺原 `question_versions.snapshot_text`、完整 evidence seal manifest 及原已脱敏 observation 导出；完整数据库冻结链仍需一次只读补证，不能用 request_text 代替整个登记链。查询见 [第三关操作说明](../docs/FORMAL_EGRESS_TSA_REVIEW.md)。

## 2. 规范与旧现场差异已经定位

独立解析迁移文本和附件函数定义，而非仅信任比较脚本：

| 库 | 迁移文件数 | 实际定义核对 |
|---|---:|---|
| fi_9f0a994_verify | 19 | 72 个函数与 9f0a994 匹配；函数体、security definer、volatility、search_path 相符 |
| fi_pr10_live | 16 | 69 个函数与截至 0015 的规范匹配 |
| fi_pr12_live | 17 | 70 个函数与截至 0016 的规范匹配 |
| fi_pr12_sol_diag | 17 | 对截至 0016 的规范仅有两个覆盖；对 9f0a994 共六项差异 |

Sol 的两个实际覆盖为 `valid_terra_generation/native_entries_allowed`，与保留覆盖脚本相符；另外四项为未应用 0017/0018 带来的 `claim_task/validate_spec/attest_anchor/score_experiment` 差异。112 项关系 ACL 在四库之间一致，78 个触发器定义一致；时间约束差异符合前向迁移预期。未对源库“还原”函数或写迁移校验和。

已有指纹未包含视图定义和触发器启用状态；实际 `fi_pr12_terra_diag` 只有迁移文件列表，没有与参考库同等完整指纹。还需只读导出这些字段及角色/所有者权限，不把规范验证库的结果套到实际运行库。只读脚本支持源核对；OID 未变本身不证明所有业务行未修改。

## 3. 所有失败和预算继续留账

包内还保留前置实验 `7f6a447a-9835-484c-be93-8308044ba483` 的 `INPUT_PLANNING_LIMIT`：完整规则在题目和证据中重复，规划量 19401/20067 超过 16384。驱动在预构建阶段停止，随后另建当前实验，以完整规则加哈希引用去重。没有把失败实验原地改成成功。

该路径按代码发生在计数和 worker 领取前，但附件未导出其实际 attempts、requests、observations、reservations 和 budget account；一次只读查询确认零记录/零预留后才能完成账本闭合，不能只凭“未发送”删去这条历史。

| 项目 | 美元 |
|---|---:|
| 原 Terra、401 与 Sol 未知预留 | 0.70 |
| 当前 Terra 两题预留 | 0.14 |
| 当前已报告合并占用 | **0.84** |
| 尚未承诺额度 | **0.16** |
| 若未来 FORMAL 两题仍分别满足 $0.07 上界条件 | 0.14 |
| 条件计划总额 / 余量 | **0.98 / 0.02** |

两次实际 usage 按保守费率逐题向上取整的参考估算分别为 7743、8260 微美元，合计 **$0.016003**；不是账单，不释放 $0.14 或其他旧持有。spent=0 只是没有登记最终账单。报价有效至 **2026-11-19 19:30 北京时间**；未来正式登记须重新核价、冻结并为新完整请求取得新计数回执。计数接口免费不等于推理免费，剩余额度不授权重试。本批新增真实调用费用为 **$0**。

## 4. 第三关仍未通过

19:35:41 的无钩子宿主证据仍将 api.openai.com、api.bls.gov、freetsa.org 解析为 198.18.0.1。已收到的 DoH 文件全局修改进程 getaddrinfo；DoH 自身使用默认 urllib 代理/重定向，过滤混合公私地址后只返回公网部分，缺少响应体上限。这些行为不能作为正式正常网络的验收；未发现源码关闭模型 TLS 或改成中转，但源码也不能替代真实运行 CA、对端和网络证据。

附件 `official_fetch.py` 的历史日志有自引用，快照 manifest 已注明后补构建；不能恢复当时未留存的每跳 TLS/URL/时间。重定向还缺官方主机白名单。`run_terra_diag.py` 的实际命令正确，但日志拼接重复 `--task`；仅日志缺陷，不能据此否认实际顺序。后续驱动应直接保存 `list(cmd)`，抓取器用独立 hop 对象和获准主机白名单；原文件及更正一起保留，不能改写旧附件。本仓库没有采用这两个私有附件驱动。

2026-11-18 审计时，verifier 明确输出 `revocation_status=NOT_CHECKED`，数据库报告合同也仅接受这个值，不能据此声称完成生产撤销检查。当天尚未取得证书原字节，FreeTSA 不同官方页面公布的 TSA 证书摘要也不一致。2026-11-19 的材料取得与前向修复另见第 6 节；生产根、链、policy、精度和撤销策略的独立批准仍未完成。没有 FORMAL、REGISTRATION/FORECAST 生产证明或自动结算。

## 5. 0019 修复与实际测试（保留原批次记录）

新增 [ADR-024](../docs/adr/024-provider-observation-metadata.md) 和前向 0019：仅显式官方 Terra 向前保存响应参数标签及两个 ID；历史 v1 不改。新 worker 在旧库缺 capability 时于 prepare 前停止，不能先付费再因存储协议不兼容丢记录。发送正文、模型、prompt、schema、输出成功语义、预算持有、SSRF/TLS 与 TSA 信任门槛不改。

本地 Python 3.12.14：`python -m unittest discover -s tests -q` **372 项通过**，其中本批 14 项元数据回归；两种请求的固定 SHA 回归证明发送字节不变。数据库新增 13 项回归，并将真实迁移路径扩为七条（包括原 19 文件基线）；本地没有 PostgreSQL 服务，数据库结果必须读取本批精确 HEAD 的 CI，不能沿用原 206 项结果。

`compileall`、`git diff --cached --check` 通过；原 **19 份迁移与 9f0a994 逐字相同**。构建 wheel 和仓库外新虚拟环境的 `--no-deps --no-index` 安装、`python -I ... check_distribution.py installed` 均逐项比对 **60 个包文件 / 20 份迁移**。wheel SHA256 为 `c7563b35f69215757a9049ed6e64f609d5ea28edc885e673cdd1f89d3805d836`。一次检查命令的工作目录路径错误已修正后重跑，未涉及网络或数据写入。源快照包含本批暂存文件，其起点 HEAD 仍为 9f0a994，不声称旧提交已有新文件。

14 个变更文件的限定凭据模式检查无命中；没有上传私人附件、运行数据、预测值、证书或密钥。只读 SQL 已按实际 schema 交叉审查，包含完整角色成员闭包和嵌套值脱敏门控；此会话没有执行该 SQL，原库补证仍未完成。

**本批精确 HEAD 的 PG16/17、七条升级路径、实际服务器重启、打包矩阵与 205 行浏览器回归结果，以 [PR #12](https://github.com/ecofree/future-intelligence/pull/12) 最新 CI 区块和对应实际日志为准。** 没有重跑原库的实测/迁移。历史基线 9f0a994 的离线 358、PG16/17 各 206 项通过仅作比较。主分支保持不变，PR #12 不合并、不部署。

首个提交 ed025f7 的 [数据库 CI 44301652282](https://github.com/ecofree/future-intelligence/actions/runs/44301652282) 在 PG16/17 各运行 220 项，13 项新观察协议回归及七条升级路径全部通过；唯一失败是历史 0018 边界测试调用当前 migrate 后仍断言只新增 0018，实际还会应用 0019。后续修复将该历史测试的资源明确限定为原 0018，保留原严格迁移列表与所有旧证明、旧评分、资格改变断言；当前全部迁移另由七条升级路径和 v1→v2 历史保留测试覆盖。未修改任何实现或 SQL，也不删除失败日志。修复后以新 HEAD 全套 CI 为验收依据。

## 6. 2026-11-19 续报：CRL 材料与资格修复

以 4575ddb 为起点，本批修复另一项实际缺陷：旧 NOT_CHECKED 报告仍可能取得生产 FORMAL 时间资格。新增 [ADR-025](../docs/adr/025-offline-crl-admission.md)、严格离线 CRL profile/report v2 和前向 0020；保留旧 fixture 的合成用途，旧非 fixture 报告继续保存但不能贡献新生产资格。新 `fi.score.v3` 不覆盖旧评分。模型请求、原诊断 `e1e67317-4416-445b-b5c5-2f0197bf251d` 及所有 UNKNOWN 预留不改，没有收费诊断重跑。

本会话的研究下载通过继承环境代理的 HTTPS 取得官方 CA、TSA 叶证书及 CRL，HTTP 200、TLS 验证结果 0。原 CA/叶证书文件 SHA256 分别为 `2151b61137ffa86bf664691ba67e7da0b19f98c758e3d228d5d8ebf27e044438`、`8bfb0305bb64e2571ca507552ef3245cb1c2fee8728e0ff8689225081ea13467`；CRL SHA256 为 `13d85acc0dd6a7ace445822b09dcba0de4236e84d60494dcbe6d7a6c97031935`。文件摘要、DER 指纹、官方地址与完整边界见[第三关说明](../docs/FORMAL_EGRESS_TSA_REVIEW.md)。

离线 `openssl crl -in root_ca.crl -verify -CAfile ca.pem -noout` 成功；`openssl verify` 使用固定 CA、禁用其他 CA 路径/存储、auth_level 2、timestampsign 用途和 `-crl_check_all`，对下载的叶证书返回 OK。叶证书 critical EKU 仅 Time Stamping；其 certificate policy `1.3.5.8` 不能当作实际 token policy。CRL 编号 4096，thisUpdate/nextUpdate 为 `2026-09-18T10:30:55Z` / `2027-09-18T10:30:55Z`，没有列出的撤销条目。尚未取得实际 token，因此不能宣称 RFC3161 token 验证或生产事前证明成功。

新 profile 显式冻结 `offline-full-chain-crl-v1` 及最大 CRL 年龄，没有默认生产最大年龄。新报告绑定原 CRL 字节与有效窗口，验证实际 token 全链并使用当前时间，不回拨 genTime。派发要求当前仍在 CRL 窗内；历史 score v3 根据原真实 claim/dispatch、核验时刻和报告窗口判定，不因今天自然到期改写历史。独立导出按当前时间重验留存材料或显式给定的经审核 CRL；过期或后来签发、未覆盖原 first_attempt_at 的 CRL 不能自动提供历史资格。逐次派发仍需原数据库审计，本批没有长期归档更新或在线撤销监控。

实取当前证书与英文页/CPS 摘要一致；另取得官方 `tsa.crt_expired`，其文件摘要正是德文页所列 `aaeb619d11a63f75d06d104a926b85c76f8276bd9eae616e82d0db5cc5ff149d`，有效期至 `2026-03-11T01:57:39Z`，已过期。摘要差异已对应到新旧证书，不能拿旧叶证书验证新 token，也不因此批准生产根。同域下载不能替代独立 pin 来源；CRL 新鲜度政策、实际 token policy/精度及独立生产信任仍未批准。

本会话同日普通 DNS 对 api.openai.com、api.bls.gov、freetsa.org、www.bls.gov 均 EAI_AGAIN(-3)；研究下载经代理可达不表示项目直连 PublicHTTPS 已通过。原宿主 fake-IP 和第 1～3 节只读补证阻碍仍在，不以本会话成功下载替代原环境验收。

当前已报告占用仍为 **$0.84 UNKNOWN**，尚未承诺 **$0.16**；未来两题 FORMAL 各 $0.07 的条件计划仍为 **$0.98**，$0.02 不是重试授权。北京时间 2026-11-19 09:09 核查时，报价 19:30 截止尚未到；未来正式登记仍须重新核价、冻结及计数。本次新增模型调用、模型费用均为 **0**，没有 FORMAL、生产时间证明或自动结算，主分支不变。

本节的 0020/CRL 代码验收必须使用本批精确 HEAD 的离线、数据库升级与打包结果，不沿用第 5 节的 0019 测试数量。当前实际材料的两项 OpenSSL 离线检查已完成；正式信任验收继续阻断。

本地 `python -m unittest discover -s tests -v` 实跑 **413 项通过**；新增 CRL 28、独立导出 10、CLI 3 项。`python -m compileall -q future_intelligence tests scripts` 与差分空白检查通过。原 20 份 SQL 与 4575ddb 逐字比对相同，模型请求相关文件无差异；原二元/四边界请求 SHA 回归均通过。新增 10 项数据库回归及第八条升级路径，检查非 fixture v1 去资格、真实 v2 全链材料、当前 claim/prepare、历史 score v3、未知预算保留和旧行/校验和；此环境没有 PostgreSQL 执行入口，待本提交自身 CI，不将静态检查写成数据库成功。

`scripts/check_distribution.py` 的 source snapshot、wheel 和仓库外 `python -I ... installed` 检查通过：**61 个包文件 / 21 份迁移**逐项匹配。构建使用 `pip wheel --no-deps --no-build-isolation`，在独立临时 venv 从本地 wheel 安装；wheel SHA256 为 `71080c804a8a1e9748de33749a74eee4c591dd779dada2ace4a3c818d8b07bb8`。18 个变更文件的限定凭据模式检查无命中；原始实验附件、模型响应、私钥和实际数据库凭证均未加入公开仓库。本提交自己的 PostgreSQL、重启及浏览器结果以 PR #12 的本批 CI 区块为准。

公开材料审计附件 `fi-tsa-public-review-20261119.zip` 包含四份原始公开材料、实际 OpenSSL 命令与输出、用途边界及 SHA256 清单，压缩包 SHA256 为 `cebbe4070117a11ad5db9b3c4cad30fa4fb5a8fae01f2a8a6162433764a8533f`。没有密钥、模型原始内容或私人实验附件；组包本地时间不是可信事前证明。

首轮实现提交 **dae40793fa48342f9313ff8c4009d41864249a27** 的离线矩阵、打包及隔离部署/205 行 Chromium 回归通过。数据库 [44438340051](https://github.com/ecofree/future-intelligence/actions/runs/44438340051) 在 PG16/17 各运行 231 项，八条升级路径全部通过；新增边界夹具却直接用超级用户插入，被既有隔离身份检查拒绝，PG16 报 3 个该夹具错误。PG17 还暴露三份注册证明只等待第一份 upper 的时序竞争，合计 4 个错误。这些失败日志保留，未将整体数据库状态写成通过。

后续修复仅改这份数据库测试：边界断言在独立、必回滚的合成事务里以真实 verifier session_user 执行；所需测试 INSERT/ledger_rc EXECUTE 临时权限和合成行全部回滚，前后断言没有遗留。正式 verifier 的权限与安全检查不变。三份证明等待各自 upper 的最大值后再领取，15 秒 CRL 窗口和严格截止不放宽。实现和全部 21 份迁移不改；修复提交自己的完整 CI 另见 PR #12。

2026-11-19 重新读取 [BLS CPI 日历](https://www.bls.gov/schedule/news_release/cpi.htm) 与[12 月官方日历](https://www.bls.gov/schedule/2026/12_sched.htm)，11 月 CPI 仍列为 12 月 10 日 08:30 美东，即北京时间 **2026-12-10 21:30**。这是本会话日历研究复核，不替代原运行环境的许可快照和独立入场确认；正式登记前仍须再次核对。
