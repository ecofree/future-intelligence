# ADR-023：指定任务、输入计数与严格预测截止

日期：2026-11-18。状态：实现待本提交 CI 验证；不代表新的真实诊断或 FORMAL 通过。

## 背景与范围

Sol 交接包暴露三项可复现问题：默认队列按 task UUID 领取导致五类别先发；将完整生成 payload 复制给输入计数端点导致 400，但驱动继续发送；旧时间约束迫使 outcome_not_before 加 1 微秒，排除了真实整点首发。另有未登记的运行时配置覆盖，不能作为规范 Terra 合同的验收。

本批沿用现有 worker、账本和评分入口，只增加两份前向迁移与一个纯离线计数适配器。没有新供应商、服务、调度器或生产网络绕过。旧 17 份 SQL 字节、已登记题目、实验、失败、费用及证明原记录保持不变。

## 1. 显式选择一个任务

0017 增加 `fi.claim_task(experiment, kind, task)`。它先核验真实实验类型、登录身份及绑定，再在现有预算锁、admission、证据、来源许可、截止、租约和 fencing 规则下，只领取指定任务。不存在、跨实验/身份、已完成、无法领取或预算不足时不会换取另一题。内部共享函数不授予 worker 或 PUBLIC；原两参数入口仍按既有默认顺序工作。

现有三个 worker CLI 增加 `--task UUID`；有循环的入口要求 `--cycles 1`。返回身份始终来自真实 lease；与所请求 task 不一致时，在 prepare 或 HTTP 发送前失败。指定任务未领取返回 `BLOCKED / TARGET_TASK_NOT_CLAIMABLE`，只标 requested_task_id，不伪造实际 attempt。原不指定任务的 NO_CLAIM 语义保留。

下一轮必须单 worker，先指定二元 task，并核对保存结果、实际模型/生成参数、结构输出、usage、费用状态和停止条件，验收后才单独指定五类别 task。只确定发送顺序不等于完成第一题验收；接口也不能阻止另一个独立进程被错误启动。

## 2. 对实际完整请求核验输入计数

官方 `POST https://api.openai.com/v1/responses/input_tokens` 的输入参数集不同于生成端点。当前冻结 Terra 请求映射如下：

| 处理 | 字段 |
|---|---|
| 原样保留 | input、instructions、model、reasoning、text、tools |
| 计数端点省略 | max_output_tokens、service_tier、store、stream |

完整 `reasoning.mode` 和 `text.format.schema` 都保留。未知字段失败关闭，不能静默丢弃新上下文；不借此接受 Sol 配置。适配器本身不联网，只从确切生成请求字节生成计数请求，并验证已有回执。

回执协议为 `fi.openai-input-count.v1`，绑定未删减生成正文、实际计数正文和原始计数响应的三个 SHA256；同时要求官方端点、HTTP 200、object 为 `response.input_tokens`、真正的正整数计数不超过登记的 input_token_ceiling、有效 request ID 及有时区、顺序正确且早于派发的收发时间。本次受控入口要求从 started_at 起最多 24 小时；这是一项本地验收策略，不是 OpenAI 保证。普通 completion 的 usage、400、空值、bool/float/string、超限、哈希改变或缺失均不能放行。即使只改了计数正文省略的 max_output_tokens，也须重新取得与新完整生成正文绑定的回执。

现有单次 worker 可显式传入 `--input-count-record PATH`，必须与 `--task` 一起使用，在 prepare/dispatch 前校验。失败仅对已经证明未发送的本次推理尝试记零费用，不释放任何旧未知预留。默认旧入口和历史请求保持原语义；这是本轮受控运行入口的门控，不声称所有外部脚本或数据库调用已强制计数。执行方必须保留计数的成功/失败原始记录及运行输出，纳入私有审计档案；本地 JSON 和哈希并不是独立签名或外部时间证明。

回执 JSON 必须恰好包含以下十个键，不能加入响应头或其他审计字段：

```json
{
  "protocol": "fi.openai-input-count.v1",
  "endpoint": "https://api.openai.com/v1/responses/input_tokens",
  "source_request_sha256": "<原生成正文原始字节的SHA256>",
  "count_request_sha256": "<实际计数请求字节的SHA256>",
  "response_body": "<实际计数响应原始UTF-8文本>",
  "response_sha256": "<上述原始响应UTF-8字节的SHA256>",
  "http_status": 200,
  "request_id": "<实际HTTP请求标识req_…>",
  "started_at": "<实际发出时间ISO8601，含时区>",
  "finished_at": "<实际收到时间ISO8601，含时区>"
}
```

先读取确切生成正文 `source_bytes`，调用 `future_intelligence.providers.input_count.build_count_request(source_bytes)` 得到实际计数请求字节；获准网络入口必须发送这些字节，并保留真实响应。生成、计数、响应三个哈希分别计算，不重新排版原文；响应的 HTTP 请求标识须为实际 `req_…`，不能代填 `resp_…` 对象 ID。上述占位符不是有效回执，不可用历史 usage 或人工数值补造。额外响应头与审核信息旁存；失败记录也全部保留，但不得将其状态改为 200 来满足门控。

[官方帮助中心](https://help.openai.com/en/articles/4936856-understanding-and-counting-tokens) 明确该计数接口免费、不消耗正常推理额度，推理调用另计费用；这不使失败计数变成成功。参数与返回格式依据 [官方端点](https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count) 及 [计数指南](https://developers.openai.com/api/docs/guides/token-counting)，核对日 2026-11-18。

## 3. 预测排除截止瞬间，发布包含下界

新前向 0018 允许 `forecast_deadline <= outcome_not_before <= resolution_deadline`。当两者相等时，预测时间窗仍不重叠，因为合法派发/提交已经要求当前时间严格早于截止；新证明还必须满足 `upper_time < forecast_deadline`。包含精度误差后的上界等于截止也不合格。

`fi.attest_anchor` 仍保存真实但迟到的证明，等号归 VERIFIED_LATE。`batch_catalog` 以严格边界过滤资格，因此历史上标 VERIFIED、上界恰等于截止的证明原行保留，但不会继续给新入场/评分提供及时资格。离线导出复核同步使用严格比较。晚封存批次仍允许保存审计材料，不能得到及时预测资格。

新的评分计算标记 **`fi.score.v2`**，上下文哈希、既有 run 查找和插入标签同步更新。旧 `fi.score.v1` run、scores、attestation 和问题原值不改、不删除。指标公式和完整任务分母不变；版本差异是截止资格语义。

结果证据保持 `outcome_not_before <= published_at <= resolution_deadline`，不能未来入库，不能把实际 published_at 改成抓取时间或人为加微秒。CPI 新合同见 [ADR-022 v2](022-cpi-first-release.md)，旧 Sol 的 +1µs 冻结定义不能原地修正。

迁移通过精确约束表达式定位唯一旧约束；找不到、重复或形状变化即停止，不猜测名称。升级回归比较迁移历史、保留数据、最终函数/权限/约束，并重放旧等号证明与 v1 评分的保留情况。不得在源实验库上未经验证直接试修。

## 4. 下一次受控执行

先在独立副本完成代码、迁移及实际函数定义核对。保留 Sol 运行覆盖的脚本哈希与安装后函数/权限；缺失的 DoH 注入源码和生产 TSA 信任审核仍须补齐，不能用迁移清单匹配替代。

新建官方 Terra DIAGNOSTIC，冻结完整首发规则、真实整点边界、证据、模型、prompt、schema、生成参数、预算和两个实际 task ID。为每个确切请求取得成功计数回执后，分别执行：

```text
python -m future_intelligence.runtime worker <新DIAGNOSTIC_ID> --task <实际二元TASK_ID> --input-count-record <二元计数回执.json> --cycles 1 --allow-network
```

第一题完整验收后，才以另一个指定 task 和对应回执执行同一入口。占位符不是可运行的已成立实验；这里不发起真实调用、不授权重试。新配置变化不能回写旧实验。

全部诊断、正常网络、TSA 独立信任和入场条件通过后，另建 FORMAL；先取得 REGISTRATION 证明再调用模型，严格在截止前封存并取得 FORECAST 证明。没有实际 FORMAL ID 和证明就不创建结算任务。
