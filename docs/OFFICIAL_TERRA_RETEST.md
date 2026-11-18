# 已接收本地整合基线：官方 Terra 复测

日期：2026-11-17。本批从 integration/pr10-official-retest 的 4d4b91a 整合 PR #11 的 c9cbcae；不合并 main、不部署生产。完整父历史保留。仅修真实闭环前置问题，不增加供应商、来源或调度系统。

**2026-11-18 最新交接：两次 Sol 调用成功，Terra 显式合同仍待验收。** 新诊断 `1644360e-62e6-472c-aaee-c0c19106ab52` 在 `fi_pr12_sol_diag`，两次官方 Responses 均返回 HTTP 200 和 `gpt-6.2-sol`，不是本会话授权的 `gpt-6.1-terra`。完整保留为独立历史诊断，不据此改变目标模型或批准重试。实际先五类别、后二元；两次输入计数均因不支持的 `max_output_tokens` 返回 400，现场驱动仍发送了推理请求。运行时 Python/SQL 覆盖与缺失的 DoH 注入文件也未形成规范代码验收。

当前推理未知预留为 **0.70 美元**。继续原 Terra 目标的四次新调用若各按已证明条件预留 0.07 美元，总占用为 **0.98 美元**；这是条件预算，不是本轮派发或重试授权。输入计数接口免费已由官方帮助中心明确，但有效计数仍是发送前的验收条件。本轮只补指定任务领取、计数回执门控和时间边界，详见 [ADR-023](adr/023-controlled-retest-boundaries.md) 与 [本次审计](../reports/SOL_DIAGNOSTIC_AUDIT.md)。

新题的首发载体、历史常数、精确同比计算及更正规则见 [ADR-022](adr/022-cpi-first-release.md)。运行方已给出带时区的两个截止；新版本按真实整点发布处理，不加 1 微秒。此代码审计没有发起真实模型调用，没有新建实验、FORMAL、生产时间证明或自动结算。

## 1. 已核对的材料与证据边界

收到的 ZIP SHA256 为 `43603dac7312eb005d5c04b8740cdcc2ce76fd03b094be1b72314054b2aade9f`，SHA256SUMS 中21项全部匹配。远端4d4b91a的父节点为f9eafb1与12c5d11；f9的祖先保留f5f66a8。交接材料不是数据库连接；当前环境仍没有原宿主和安全注入的运行凭证。

三份脱敏报告支持已有官方Terra两题DIAGNOSTIC成功、模型/请求/响应摘要与成本记录。该实验在独立的 `fi_pr10_live`，不能误认成迁移清单所指的 `fi_live2`。原请求正文、完整冻结题目/证据与原始回包未随脱敏包提交，因此本轮只核验材料完整性、代码/历史与摘要自洽性，不宣称对原调用进行独立网络重放或原始字节重算。原始材料仍应留在运行方私有目录，不自动上传公开仓库。

2026-11-18 新交接包 SHA256 为 `62535bfe64ce11a50310a327593b027e3a124528d43730f93f6b51725f2459ca`；17 项 SHA256SUMS 全部匹配。包内有两次调用的脱敏时间与状态、冻结配置和题目摘要、输入长度、费用预留、报价依据及升级/测试摘要。原始 HTTP 正文、完整数据库和运行驱动未包含。清单匹配证明收到的文件完整，不能代替独立实库核验或外部可信时间。包内旧 status/REPORT 保留 11:49 的失败状态，较新的 MANIFEST 记录密钥更换后的状态；不回写旧记录，也不把模型列表 HTTP 200 当作 Responses 推理通过。原文件保留在私有交接目录，不上传公开仓库。

16:32 之后的 Sol 交接包 SHA256 为 `e891e6dc163286a52dc730fc053fb7b02fc7355c5584ff88a20da5aa02e8e90c`，33 项清单全部匹配。收到的驱动与 SQL 只读取、没有执行。数据库保存的任务身份与更正说明一致；驱动摘要曾用计划中的题型和 task_id 覆盖实际返回身份，故以实际请求哈希和保存记录核对顺序，同时保留原摘要和更正。包内没有完整原始 HTTP 字节、完整冻结实验导出和注入的 `sitecustomize.py`；不把摘要和清单当作原环境、原数据库或 TLS/解析钩子的独立验收。

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

整合时保留原 16 份 SQL，0016 新增显式 Terra 合同。此次又完整保留上述 17 份 SQL 字节，仅新增 0017 精确任务领取、0018 时间边界前向迁移；不解除旧 `fi_live` 的 checksum 阻断。新升级测试覆盖两条 0014 谱系、13/16/17/18 文件基线到 19 文件终态，并比较函数、权限和表约束，不能冒充原库升级。

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
| 本次 Sol 两题，各 0.13 | 0.26 |
| 已占用合计 | **0.70** |
| 账面尚未承诺额度 | **0.30** |
| 运行方另规划 Sol FORMAL 两题，各 0.13，尚未登记 | 0.26 |
| 上述假设计划的总占用 / 余量 | 0.96 / 0.04 |

0.04 是包含两次未发生 Sol FORMAL 调用的计划余量，不是当前账本余额，也不是改用 Sol 或额外重试的授权。两次成功推理也不自动释放 UNKNOWN；按全部输入使用缓存写入上限估算为 0.016965 美元，仍不是账单。保留 0.70 全额未知预留，只有官方费用证据可支持追加对账。此前 0.74、0.72 或原每题 0.15 的 1.04 计划均不得继续当作当前余额。有其他未结请求必须先并入同一总预算。

### 2026-11-18 新预算候选：不减少输出额度

包内固定 `input_token_ceiling=16384`、`max_output_tokens=8192`、输入报价 1,250,000 微美元/百万 token、输出报价 5,000,000 微美元/百万 token。在这些条件成立时，单次规划上界为 `20480 + 40960 = 61440` 微美元，即 **0.061440 美元**。

| 项目 | 候选预留（美元） |
|---|---:|
| 每题新预留 | 0.07 |
| 新 DIAGNOSTIC，两题各一次 | 0.14 |
| 其后另建 FORMAL，两题各一次 | 0.14 |
| 现有未知预留，保持不变 | 0.70 |
| 合计 | **0.98** |
| 距 1 美元授权余量 | **0.02** |

当前官方 [Terra 模型页](https://developers.openai.com/api/docs/models/gpt-6.1-terra) 的 Standard 输入价为 $1/百万、默认 30 分钟缓存写入为 $1.25/百万、输出为 $5/百万；按全部输入 $1.25 取保守最大值。[缓存文档](https://developers.openai.com/api/docs/guides/prompt-caching) 明确省略 TTL 保留 30m，写入费是该部分 token 的总费率，不与普通输入重复相加。[Responses 文档](https://developers.openai.com/api/reference/python/resources/responses/methods/create) 的 `max_output_tokens` 包含可见输出与推理 token，8192 不再重复加一份推理额度。上述核对仅适用于官方全球端点、default 服务层、无额外付费工具及当前费率，不改变实际请求。

该候选尚有明确前置条件：本地 `input_token_ceiling` 是规划值，不发送给供应商，也不是精确 tokenization。字节长度加 4096 不能代替完整新请求的输入计数。发送前须对实际 instructions、input、reasoning、tools 和完整 text/schema 取得与请求哈希绑定的有效计数，确认不超过 16384；新 FORMAL 输入改变时重新核对。[官方帮助中心](https://help.openai.com/en/articles/4936856-understanding-and-counting-tokens) 已明确 `POST /v1/responses/input_tokens` 免费、不占正常推理额度；单独的推理调用仍计费。参考 [计数指南](https://developers.openai.com/api/docs/guides/token-counting) 和 [端点字段](https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count)。

此次两个 400 的原因是向计数端点传了生成端点的 `max_output_tokens`。新离线 helper 仅映射端点支持的六个输入字段，移除 `max_output_tokens/service_tier/store/stream`，但记录仍绑定未删减的生成请求哈希。400、缺失计数、普通 completion 的 usage、哈希不一致或超上限均不得继续发送；不得用历史 usage 倒填计数回执。采用指定任务加计数回执的单次 worker 入口，见 ADR-023。

原 Terra 报价有效至 **2026-11-19 11:47:49.011360 北京时间**；本次 Sol 的不同报价有效至 **2026-11-20 16:30 北京时间**，不能挪用为 Terra 报价。新实验冻结时重新核实相应型号、官方全球端点、default 服务层、缓存费率和报价期限。只有条件成立才登记新的 70000 微美元/题；现有 150000/130000 预留不改。0.02 美元计划余量不是额外重试授权。

### 恢复真实请求之前

1. 原运行方已报告安全更换**官方 OpenAI**凭据，并以模型列表检查确认访问；新密钥继续留在原环境。使用已更新的安全注入入口，核对项目及权限，不再要求贴出密钥，也不重新试旧密钥。列表检查不是两题 Responses 推理验收。官方错误与凭据指引：https://developers.openai.com/api/docs/guides/error-codes 、https://developers.openai.com/api/reference/overview/ 。
2. 完成上述预算对账/保守上界核算后，才能发起下一次真实请求。正常网络、生产 TSA 与独立入场仍是 FORMAL 的必要条件；更换密钥不解除这些阻碍。当前 FORMAL 不成立，不安排结算任务。
3. 另建全新的 DIAGNOSTIC，冻结问题版本、完整首发结算规则、证据/输入哈希、prompt、schema、generation 及预算；保留所有旧诊断。只用一个 worker，使用实际二元 task ID 单次领取，读取实际保存的结果并验收通过后，再单独指定四边界五类别 task ID。不得依赖 UUID 的默认排序，不用计划中的标签覆盖返回的真实 task/attempt。每题最多一次；计数、401、模型/配置核对或持久化失败即停止。任何影响请求的变更都在新诊断中验证，不修改历史实验。
4. 首次认证失败立即停止后续派发并使用既有 operator pause；不在外部脚本中忽略非零退出码或自动重启。工作器继续先保存 HTTP_ERROR observation 与未知费用，再把 HTTP 状态传给有界循环；`runtime.service.loop` 与旧 `delivery worker` 均遇 401 停止并退出 2，不领取下一题。这只保证该循环停止，不能撤回已派发的并行请求或约束另一个独立进程。未派发的任务保留原登记状态，不伪造失败调用。
5. 每题同时核对官方返回的 model、reasoning/service_tier、结构化输出、概率、usage 和费用状态；只有两题真实通过且第 5 节条件满足，才另建 FORMAL。

## 5. 出网与生产TSA仍是实测前提

交接预检中 `api.openai.com`、`api.bls.gov`、`freetsa.org` 均解析为 `198.18.0.1`。在实际 worker 容器/宿主核查这三个域名，修正所用 DNS/TUN 的 fake-IP 设置或使用正常解析的隔离执行环境。不要加198.18/15白名单、固定绕过IP、关闭证书校验、或用curl绕过项目策略。现有PublicHTTPS与原SSRF策略不改。官方 HTTPS 获得 401 或模型列表 200 不能证明该网络预检已通过；仅凭 fake-IP 也不能断言存在非官方中转。当前会话自己的网络结果不能冒充已修复原宿主网络。

Sol 的 DoH 只在 worker 进程注入；宿主仍为 fake-IP。附件没有注入的 `sitecustomize.py`、其 SHA256、完整脱敏启动参数和 CA 配置。必须补齐并审核实际解析、逐个目标 IP 的公网检查、SNI/域名校验、重定向策略与响应路径，不能只凭 `hook_loaded=true` 或一次 TLS 摘要验收。`freetsa.org` 的 HTTP 302 只是连通结果，不是 RFC3161 令牌或信任审核。另有未登记 Python monkeypatch 与 SQL 对 `fi.valid_terra_generation/fi.native_entries_allowed` 的覆盖；该数据库不能仅凭 17 项迁移表就声称函数与规范代码一致，需保留实际定义/权限指纹和覆盖脚本哈希，不执行原库“还原”或改写历史。

生产TSA需要已审核的来源独立性、根指纹、完整证书链和专用timestamp用途、policy OID、nonce/messageImprint、精度依据、有效期与撤销检查策略。仅从服务站点下载根、只有VERIFIED字符串、人工填missing_accuracy均不足以自动通过。当前没有独立审批或真实新证明；不得复制诊断测试CA作为生产信任。两个数据库账号不自动等于两名独立审查者。

所有条件通过后才另建FORMAL并取得REGISTRATION证明，随后发送模型请求，截止前封存全量FORECAST并取得其证明。任何时间证明晚于截止均不追认。当前无FORMAL，不创建结算定时任务。

## 6. 新正式题的正确日历和首发值

BLS两份官方日历已复核：2026年11月CPI计划于2026-12-10 08:30 America/New_York =13:30UTC =21:30Asia/Shanghai =22:30Asia/Tokyo公布。
- https://www.bls.gov/schedule/news_release/cpi.htm
- https://www.bls.gov/schedule/2026/12_sched_list.htm

必须新建问题版本，旧诊断的Dec9定义不改。正式入场前保存最新获准日历原始快照并独立审核。

交接题目摘要已明确：二元阈值为 337.511；五类别按 `100 × (V / 324.122 - 1)` 自算同比，边界 3.0/3.5/4.0/4.5。2026-11-18 已分别对照 [2026 年 10 月归档 Table 1](https://www.bls.gov/news.release/archives/cpi_11102026.htm) 和 [2025 年 11 月归档 Table 1](https://www.bls.gov/news.release/archives/cpi_12182025.htm) 核实两项常数。

新题采用 November 2026 新闻稿 Table 1 → All items → Unadjusted indexes 的三位小数指数，系列 CUUR0000SA0、1982-84=100。新 [首发合同](adr/022-cpi-first-release.md) 选择锁定首次刊出数值，后来官方更正另留记录和影响说明；不能拿一位小数同比替代精确计算，不能用最新 API 数值证明首发。

最新明确时间：预测截止 **2026-12-10 21:30:00 北京时间，排除该瞬间**；结果最早发布时间 **同一瞬间，包含**；结算截止 **2027-01-31 23:59:59 北京时间，包含**。Sol 冻结版本把 outcome_not_before 加 1 微秒，会拒绝真实整点首发，保留其缺陷与原值。0018 仅让未来新版本允许截止与发布下界相等，并要求证明不确定性上界严格小于预测截止；旧问题、旧 attestation、旧评分不改，新的严格资格评分标为 `fi.score.v2`。

下一轮将首发、更正、缺失/延后、HTML/PDF 冲突、VOID 及内部抄录错误处理写入实际冻结的完整 `resolution_rule`，或通过被冻结对象引用的完整规则字节哈希绑定。只引用可变 ADR 的名称/合同标识不足以冻结这些条款；完整实验/题目导出、登记批次和证据原文保存在私有审核档案。

离线候选转换入口为 `python -m future_intelligence.delivery.cpi --index <经独立审核的三位小数指数>`。它保留精确分数与类别，仅为现有数据库数值字段生成同类别的有限小数；不联网、不写库、不评分，输出的来源、首发和发布时间验证标记始终为 false。正式登记前将本合同写进新的问题版本，并复测新的 DIAGNOSTIC，不覆盖旧版本。预计 cpi_12102026 归档尚未发布，不把未来 URL 当作已有证据。

只有FORMAL成立且记录实际实验/题目/证明ID后，才安排公布后的采集、盲化判定与评分；当前不提供带虚构ID的score命令，也不声称自动等待。
