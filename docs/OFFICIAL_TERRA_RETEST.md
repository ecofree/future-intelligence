# 已接收本地整合基线：官方 Terra 复测

日期：2026-11-17。本批从 integration/pr10-official-retest 的 4d4b91a 整合 PR #11 的 c9cbcae；不合并 main、不部署生产。完整父历史保留。仅修真实闭环前置问题，不增加供应商、来源或调度系统。

## 1. 已核对的材料与证据边界

收到的 ZIP SHA256 为 `43603dac7312eb005d5c04b8740cdcc2ce76fd03b094be1b72314054b2aade9f`，SHA256SUMS 中21项全部匹配。远端4d4b91a的父节点为f9eafb1与12c5d11；f9的祖先保留f5f66a8。交接材料不是数据库连接；当前环境仍没有原宿主和安全注入的运行凭证。

三份脱敏报告支持已有官方Terra两题DIAGNOSTIC成功、模型/请求/响应摘要与成本记录。该实验在独立的 `fi_pr10_live`，不能误认成迁移清单所指的 `fi_live2`。原请求正文、完整冻结题目/证据与原始回包未随脱敏包提交，因此本轮只核验材料完整性、代码/历史与摘要自洽性，不宣称对原调用进行独立网络重放或原始字节重算。原始材料仍应留在运行方私有目录，不自动上传公开仓库。

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

保留已有实验、请求、响应、费用预留。已有两次调用摘要：报价估算0.008101美元，实际账单未知，0.14美元预留继续保留。不要因为估算很低就释放UNKNOWN。

本批环境没有模型密钥和worker/operator DSN，因此新增真实调用0次、费用0美元；不是密钥失效的结论。不要再次在聊天发送密钥。运行方使用已有安全注入入口。

建议对同一次1美元授权作最保守核算：旧未知预留0.14美元 + 新DIAGNOSTIC两个任务各最多0.15美元 + 之后FORMAL两个任务各最多0.15美元 = 0.74美元。每个实验max_attempts=1，禁止并行新建绕过总额；还有其他未结请求时先加进总账，超过上限或无法证明余量就停止。价格、缓存写入、输入/输出/推理tokens均须核对，不能把报价当账单。0.26美元余量不是授权自动重试。

新DIAGNOSTIC固定问题版本、原始证据/输入哈希、prompt哈希、schema/hash和显式generation；一次二元题、一次四边界五类别题。改变任何请求条件需另建新诊断并计入同一总额。首次失败/不明费用立即留账，不自动修输出、换型号或盲重发。

## 5. 出网与生产TSA仍是实测前提

在实际worker容器/宿主核查api.bls.gov、freetsa.org的系统解析；修正所用DNS/TUN的fake-IP设置或使用正常解析的隔离执行环境。不要加198.18/15白名单、固定绕过IP、关闭证书校验、或用curl绕过项目策略。现有PublicHTTPS与原SSRF策略不改。当前会话自己的DNS失败不能冒充已修复原宿主网络。

生产TSA需要已审核的来源独立性、根指纹、完整证书链和专用timestamp用途、policy OID、nonce/messageImprint、精度依据、有效期与撤销检查策略。仅从服务站点下载根、只有VERIFIED字符串、人工填missing_accuracy均不足以自动通过。当前没有独立审批或真实新证明；不得复制诊断测试CA作为生产信任。两个数据库账号不自动等于两名独立审查者。

所有条件通过后才另建FORMAL并取得REGISTRATION证明，随后发送模型请求，截止前封存全量FORECAST并取得其证明。任何时间证明晚于截止均不追认。当前无FORMAL，不创建结算定时任务。

## 6. 新正式题的正确日历和首发值

BLS两份官方日历已复核：2026年11月CPI计划于2026-12-10 08:30 America/New_York =13:30UTC =21:30Asia/Shanghai =22:30Asia/Tokyo公布。
- https://www.bls.gov/schedule/news_release/cpi.htm
- https://www.bls.gov/schedule/2026/12_sched_list.htm

必须新建问题版本，旧诊断的Dec9定义不改。正式入场前保存最新获准日历原始快照并独立审核。

CUUR0000SA0是指标序列标识；不得把指数水平直接当成百分比同比结果。新题应明确总体/核心、季调与否、目标月份、同比/环比/指数、阈值和分箱单位、首发新闻表格/列、舍入以及缺失/推迟/官方更正处理。未取得原始冻结spec前，不用猜测补齐阈值或实际结算规则。预计cpi_12102026归档尚未发布，不把未来URL当作已有证据。

只有FORMAL成立且记录实际实验/题目/证明ID后，才安排公布后的采集、盲化判定与评分；当前不提供带虚构ID的score命令，也不声称自动等待。
