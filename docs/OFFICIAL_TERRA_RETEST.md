# 已接收本地整合基线：官方 Terra 复测

日期：2026-11-17。本批从 integration/pr10-official-retest 的 4d4b91a 整合 PR #11 的 c9cbcae；不合并 main、不部署生产。完整父历史保留。仅修真实闭环前置问题，不增加供应商、来源或调度系统。

**2026-11-18 最新交接：脱敏包已核验，真实复测尚未恢复。** DIAGNOSTIC `ff4a4bb1-1449-4e5d-9776-57ad1d85d119` 两次请求均为 HTTP 401 / `token_invalidated`；官方密钥已在原环境更换，运行方报告模型列表检查成功，尚未用新密钥重跑推理。配置已登记，但尚无官方成功执行该配置的证据。未知预留仍为 0.44 美元，不归零。按已核实费率及输入规模条件，可提出每题 0.07 美元的新预算，四次新调用与旧预留合计 0.72 美元；完整请求 token 计数仍须核对，不能将候选方案当作已通过的账单上限。恢复条件见第 4 节。

新题的首发载体、历史常数、精确同比计算及更正规则见 [ADR-022](adr/022-cpi-first-release.md)。本轮没有新 DIAGNOSTIC、FORMAL 或生产时间证明，也没有真实模型调用费用。

## 1. 已核对的材料与证据边界

收到的 ZIP SHA256 为 `43603dac7312eb005d5c04b8740cdcc2ce76fd03b094be1b72314054b2aade9f`，SHA256SUMS 中21项全部匹配。远端4d4b91a的父节点为f9eafb1与12c5d11；f9的祖先保留f5f66a8。交接材料不是数据库连接；当前环境仍没有原宿主和安全注入的运行凭证。

三份脱敏报告支持已有官方Terra两题DIAGNOSTIC成功、模型/请求/响应摘要与成本记录。该实验在独立的 `fi_pr10_live`，不能误认成迁移清单所指的 `fi_live2`。原请求正文、完整冻结题目/证据与原始回包未随脱敏包提交，因此本轮只核验材料完整性、代码/历史与摘要自洽性，不宣称对原调用进行独立网络重放或原始字节重算。原始材料仍应留在运行方私有目录，不自动上传公开仓库。

2026-11-18 新交接包 SHA256 为 `62535bfe64ce11a50310a327593b027e3a124528d43730f93f6b51725f2459ca`；17 项 SHA256SUMS 全部匹配。包内有两次调用的脱敏时间与状态、冻结配置和题目摘要、输入长度、费用预留、报价依据及升级/测试摘要。原始 HTTP 正文、完整数据库和运行驱动未包含。清单匹配证明收到的文件完整，不能代替独立实库核验或外部可信时间。包内旧 status/REPORT 保留 11:49 的失败状态，较新的 MANIFEST 记录密钥更换后的状态；不回写旧记录，也不把模型列表 HTTP 200 当作 Responses 推理通过。原文件保留在私有交接目录，不上传公开仓库。

## 2. 旧0014差异已定位，不修改历史

文件：`future_intelligence/db/migrations/0014_openai_compatible_provider.sql`。

| 来源 | SHA256 |
|---|---|
| f5f66a8原始文件；fi_live记录 | `0507332664003bccb1952ef1474bb0e225117cf8780dad2214e57e3eb95930b1` |
| f9eafb1修改后；4d4b91a当前文件 | `ea4e7c8a3472963a879b85eb4525f4c6777521034c4f851815c93026daa1a684` |
| PR10独立explicit0014 | `583d5d47992adb07cf331bffa69e4f0699e629fa13001033b9cdd001c4f5c059` |
| 0015协调文件 | `60a418fa5cfdcf2b96ad4e836c4813e7c594e3f6f73de8006fc37a0b8b736989` |

f9将已存在的兼容端点档案从4字段扩展为5字段，加protocol和Messages分支，并改了valid_compatible_endpoint、prepare_live_call、provider_request_guard。不是换行符差异，也不是两个0014数字前缀本身造成。fi_live继续被当前迁移器拒绝是正确行为。

fi_live2的16项哈希匹配，但实际顺序是compatible0014先、explicit0014后、最后0015。只读核对工具会报告顺序需复核；0015的名字不自动豁免。此次隔离回归重放两种14基线、13基线及16基线，并将安装后的全部fi函数定义/权限与新库逐项比较；实际结果以本批CI为准。这不是直接读取用户数据库的最终定义。

### 不丢数据的旧库恢复方案（尚未执行）

1. fi_live保持只读；使用既有备份流程制作并验证恢复到独立副本，保留原迁移表和所有业务记录。
2. 从f5f66a8恢复**原始部署产物**，验证其0014精确匹配旧库记录；这是隔离的历史恢复目录，不覆盖当前分支的任何0014，也不改数据库校验和。
3. 读取副本的实际函数、触发器、权限与记录指纹，对照原始产物，排除曾经手工改库的其他差异。
4. 为该旧部署谱系单独审查一个前向桥接：包括缺失protocol帮助函数、prepare与v2守卫的完整目标定义，而不是直接套用只替换守卫的0015。桥接记录新增迁移身份，旧记录保持原值。当前fi_live2使用另一份已应用0014，不能用同一文件覆盖两个谱系。
5. 在副本验收数据/旧元数据完全不变、目标定义和权限一致、真实记录仍可读。通过后才提出原库升级变更。若原始数据库定义未取得，停在待核验；不删除记录、不修写checksum、不清库。

本批保留原16份SQL字节，唯一新增0016用于显式Terra配置，不声称它能解除旧fi_live的checksum阻断。

## 3. 显式请求合同（需要新的DIAGNOSTIC）

仅允许官方 `api.openai.com/v1/responses` + `gpt-6.1-terra` + prompt v2。调用方在**新的实验**内固定：

```python
from future_intelligence.providers.live import CURRENT_PROMPT_SHA256, terra_generation_profile
# cfg 是新建的官方 Terra 参赛配置，不修改任何已登记实验。
cfg['prompt_sha256'] = CURRENT_PROMPT_SHA256
cfg['parameters']['generation'] = terra_generation_profile()
```

profile固定reasoning.effort=medium、reasoning.mode=standard、service_tier=default。temperature和top_p在此推理档位不支持，明确登记为OMIT_UNSUPPORTED_WITH_REASONING；seed未获Responses文档支持，登记OMIT_NOT_DOCUMENTED_IN_RESPONSES，不发送、不谎称锁定随机种子。只把reasoning和service_tier加入HTTP请求，不能把OMIT字符串透传给供应商。

Python和数据库同时检查完整配置和精确请求值；增加/删除/篡改参数或换模型均拒绝。无generation的历史诊断仍构建原请求，不回写、不改分数。官方Terra的新FORMAL要求显式profile，旧DIAGNOSTIC不能升格。正式批次、证据、权限、时间、完整分母等原检查保留。

官方文档核查于2026-11-17：
- https://developers.openai.com/api/docs/models/gpt-6.1-terra （medium支持与型号）
- https://developers.openai.com/api/docs/guides/latest-model （非none推理不支持temperature/top_p）
- https://developers.openai.com/api/docs/guides/reasoning （standard/pro独立于effort）
- https://developers.openai.com/api/reference/resources/responses/methods/create （default服务层）

配置冻结不等于模型权重永久冻结，也不保证逐token复现。本次未找到可以凭空替换的带日期Terra快照；继续使用用户指定ID，记录实际返回。实际响应的model、reasoning及service_tier仍须在新的真实诊断中核对，官方HTTP接受不等于全部语义与预算验收。

## 4. 原环境执行次序与预算

### 已发生的记录，不覆盖或重试

原成功诊断 `902fdb78-b014-406a-9df8-5189128cfd33` 位于 `fi_pr10_live`，报价估算 0.008101 美元不是账单，0.14 美元 UNKNOWN 预留继续保留。2026-11-18 运行方报告：该源库的 16 项迁移校验和及函数定义匹配，只缺本 PR 的 0016；在独立副本 `fi_pr12_upgrade_copy` 升级通过，源库没有升级或清理。新诊断另建在 `fi_pr12_live`，保留实验 `ff4a4bb1-1449-4e5d-9776-57ad1d85d119` 及两次 401、未知费用记录。交接包中的升级和 304/185 项测试摘要已收到并核对完整性；没有在此会话重跑原库验收。

401 是认证拒绝，不是二元/五类别答案或生成参数的通过证据。显式填写 reasoning/service_tier 不证明上游接受、执行或返回它们；temperature/top_p/seed 的省略也不等于锁定。原始错误正文可能包含凭据片段，不向日志、仓库或交接包复制；保留已有响应哈希、HTTP 状态、获准请求标识及时间记录。

脱敏账本显示：北京时间 11:48:42.646705 保存首个 401 observation，.656837 完成首个 attempt；第二个调用标记在 .707812，晚于首次完成 50.975 毫秒。不能再按“两个请求都已在途”解释这份账本顺序；但调用标记不是抓包时间，附件也未包含现场驱动，不能据此断言具体调用了哪个入口。当前 runtime 和旧 delivery worker 的有界循环均在保存 401 后停止；旧 worker 的 BLOCKED/NO_REDISPATCH/STOPPED_UNCERTAIN 也非零退出，记录失败时不谎称已保存。自行直接调用 `run_once` 的外部驱动也必须检查结果，不得忽略停止条件。

### 1 美元授权的当前核算

| 项目 | 未知预留或待规划额度（美元） |
|---|---:|
| 原诊断未知预留 | 0.14 |
| 两次 401 的未知预留 | 0.30 |
| 已占用合计 | 0.44 |
| 尚可承诺额度 | 0.56 |
| 若新 DIAGNOSTIC 两题仍各预留 0.15 | 0.30 |
| 若其后 FORMAL 两题仍各预留 0.15 | 0.30 |
| 沿用原方案的最终占用 | **1.04，超额 0.04** |

2026-11-17 的 0.74 美元计划已被这次真实失败消耗更新，不能作为再次运行的预算依据。401 不自动等于零费用；不得删除失败记录或自行释放 UNKNOWN。先取得可追溯的官方费用对账，按既有账本追加结算；或依据核实过的官方价格、冻结输入规模、输出与推理 token 上限，证明剩余完整四次调用的保守预留不超过 0.56 美元。不能只为凑数调低预留，不能擅自增加总预算。有其他未结请求须先计入同一总额；无法证明余量即停止。

### 2026-11-18 新预算候选：不减少输出额度

包内固定 `input_token_ceiling=16384`、`max_output_tokens=8192`、输入报价 1,250,000 微美元/百万 token、输出报价 5,000,000 微美元/百万 token。在这些条件成立时，单次规划上界为 `20480 + 40960 = 61440` 微美元，即 **0.061440 美元**。

| 项目 | 候选预留（美元） |
|---|---:|
| 每题新预留 | 0.07 |
| 新 DIAGNOSTIC，两题各一次 | 0.14 |
| 其后另建 FORMAL，两题各一次 | 0.14 |
| 旧未知预留，保持不变 | 0.44 |
| 合计 | **0.72** |
| 距 1 美元授权余量 | **0.28** |

当前官方 [Terra 模型页](https://developers.openai.com/api/docs/models/gpt-6.1-terra) 的 Standard 输入价为 $1/百万、默认 30 分钟缓存写入为 $1.25/百万、输出为 $5/百万；按全部输入 $1.25 取保守最大值。[缓存文档](https://developers.openai.com/api/docs/guides/prompt-caching) 明确省略 TTL 保留 30m，写入费是该部分 token 的总费率，不与普通输入重复相加。[Responses 文档](https://developers.openai.com/api/reference/python/resources/responses/methods/create) 的 `max_output_tokens` 包含可见输出与推理 token，8192 不再重复加一份推理额度。上述核对仅适用于官方全球端点、default 服务层、无额外付费工具及当前费率，不改变实际请求。

该候选尚有明确前置条件：本地 `input_token_ceiling` 是规划值，不发送给供应商，也不是精确 tokenization。4091/4697 字节的旧请求长度不能单独证明完整新请求的输入 token 数。发送前须对实际 instructions、input、schema 等完整输入取得与请求哈希绑定的计数，确认不超过 16384；新 FORMAL 输入改变时重新核对。[官方输入计数指南](https://developers.openai.com/api/docs/guides/token-counting) 可作为方法依据，但未取得其免费的明确保证，不能把该请求偷偷排除在预算之外。

包内报价有效至 **2026-11-19 11:47:49.011360 北京时间**，到期重新核实适用价格。只有这些条件成立才在新实验登记 70000 微美元/题；现有实验的 150000 预留不改。0.28 美元余量不是额外重试授权，也不把估算差额提前释放。

### 恢复真实请求之前

1. 原运行方已报告安全更换**官方 OpenAI**凭据，并以模型列表检查确认访问；新密钥继续留在原环境。使用已更新的安全注入入口，核对项目及权限，不再要求贴出密钥，也不重新试旧密钥。列表检查不是两题 Responses 推理验收。官方错误与凭据指引：https://developers.openai.com/api/docs/guides/error-codes 、https://developers.openai.com/api/reference/overview/ 。
2. 完成上述预算对账/保守上界核算后，才能发起下一次真实请求。正常网络、生产 TSA 与独立入场仍是 FORMAL 的必要条件；更换密钥不解除这些阻碍。当前 FORMAL 不成立，不安排结算任务。
3. 另建全新的 DIAGNOSTIC，冻结问题版本、证据/输入哈希、prompt、schema、generation 及预算；保留前两次诊断。只用一个 worker，顺序执行二元题和四边界五类别题，每题最多一次；第一题验收通过再发第二题。任何影响请求的变更都在新诊断中验证，不修改历史实验。
4. 首次认证失败立即停止后续派发并使用既有 operator pause；不在外部脚本中忽略非零退出码或自动重启。工作器继续先保存 HTTP_ERROR observation 与未知费用，再把 HTTP 状态传给有界循环；`runtime.service.loop` 与旧 `delivery worker` 均遇 401 停止并退出 2，不领取下一题。这只保证该循环停止，不能撤回已派发的并行请求或约束另一个独立进程。未派发的任务保留原登记状态，不伪造失败调用。
5. 每题同时核对官方返回的 model、reasoning/service_tier、结构化输出、概率、usage 和费用状态；只有两题真实通过且第 5 节条件满足，才另建 FORMAL。

## 5. 出网与生产TSA仍是实测前提

交接预检中 `api.openai.com`、`api.bls.gov`、`freetsa.org` 均解析为 `198.18.0.1`。在实际 worker 容器/宿主核查这三个域名，修正所用 DNS/TUN 的 fake-IP 设置或使用正常解析的隔离执行环境。不要加198.18/15白名单、固定绕过IP、关闭证书校验、或用curl绕过项目策略。现有PublicHTTPS与原SSRF策略不改。官方 HTTPS 获得 401 或模型列表 200 不能证明该网络预检已通过；仅凭 fake-IP 也不能断言存在非官方中转。当前会话自己的网络结果不能冒充已修复原宿主网络。

生产TSA需要已审核的来源独立性、根指纹、完整证书链和专用timestamp用途、policy OID、nonce/messageImprint、精度依据、有效期与撤销检查策略。仅从服务站点下载根、只有VERIFIED字符串、人工填missing_accuracy均不足以自动通过。当前没有独立审批或真实新证明；不得复制诊断测试CA作为生产信任。两个数据库账号不自动等于两名独立审查者。

所有条件通过后才另建FORMAL并取得REGISTRATION证明，随后发送模型请求，截止前封存全量FORECAST并取得其证明。任何时间证明晚于截止均不追认。当前无FORMAL，不创建结算定时任务。

## 6. 新正式题的正确日历和首发值

BLS两份官方日历已复核：2026年11月CPI计划于2026-12-10 08:30 America/New_York =13:30UTC =21:30Asia/Shanghai =22:30Asia/Tokyo公布。
- https://www.bls.gov/schedule/news_release/cpi.htm
- https://www.bls.gov/schedule/2026/12_sched_list.htm

必须新建问题版本，旧诊断的Dec9定义不改。正式入场前保存最新获准日历原始快照并独立审核。

交接题目摘要已明确：二元阈值为 337.511；五类别按 `100 × (V / 324.122 - 1)` 自算同比，边界 3.0/3.5/4.0/4.5。2026-11-18 已分别对照 [2026 年 10 月归档 Table 1](https://www.bls.gov/news.release/archives/cpi_11102026.htm) 和 [2025 年 11 月归档 Table 1](https://www.bls.gov/news.release/archives/cpi_12182025.htm) 核实两项常数。

新题采用 November 2026 新闻稿 Table 1 → All items → Unadjusted indexes 的三位小数指数，系列 CUUR0000SA0、1982-84=100。新 [首发合同](adr/022-cpi-first-release.md) 选择锁定首次刊出数值，后来官方更正另留记录和影响说明；不能拿一位小数同比替代精确计算，不能用最新 API 数值证明首发。交接摘要没有完整预测截止和结算截止时刻；新版本冻结前必须补齐带时区的完整时间戳，不能把“2027-01-31”自行当作已经登记的某个时刻。

离线候选转换入口为 `python -m future_intelligence.delivery.cpi --index <经独立审核的三位小数指数>`。它保留精确分数与类别，仅为现有数据库数值字段生成同类别的有限小数；不联网、不写库、不评分，输出的来源、首发和发布时间验证标记始终为 false。正式登记前将本合同写进新的问题版本，并复测新的 DIAGNOSTIC，不覆盖旧版本。预计 cpi_12102026 归档尚未发布，不把未来 URL 当作已有证据。

只有FORMAL成立且记录实际实验/题目/证明ID后，才安排公布后的采集、盲化判定与评分；当前不提供带虚构ID的score命令，也不声称自动等待。
