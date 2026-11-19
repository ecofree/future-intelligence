# ADR-024：向前保留官方响应的参数标签

日期：2026-11-18。范围：响应审计与存储，不改变生成请求或预测评分。

## 问题与取舍

9f0a994 的新 Terra 诊断已保留完整请求、有效计数和两次成功结果。交接复算能证明发送参数，但原 `fi.provider-observation.v1` 丢弃响应顶层 `reasoning/service_tier`，还用响应对象 `id` 覆盖 HTTP 请求标识。原始 HTTP 正文只保留摘要，不能从请求或模型名称推导当时实际返回的参数。

本批只修这项审计损失。既有 v1 不回填，不为补日志再调用模型；任何以后真正改变模型、提示词、证据、schema 或发送参数的调整仍须新 DIAGNOSTIC。

## v2 的精确结构

显式官方 Terra worker 使用 `fi.provider-observation.v2`。原八键保持语义：`protocol/status/http_status/response_sha256/model_reported/request_id/usage/output`，仅新增 `response_id/generation_reported`，总共十键。

- `request_id` 保存经过校验的 HTTP 请求标识；`response_id` 单独保存响应 JSON 的 `id`。缺失或非法标识为 null，互不替代。
- `generation_reported` 仅含 `reasoning_effort/reasoning_mode/service_tier`，各项恰有 `state/value`。
- 返回值在白名单内为 `PRESENT`；缺失或 null 为 `MISSING`；类型或值不合法为 `INVALID`。后两种 value 必须为 null，不能保存任意原值。
- effort 白名单为 none/minimal/low/medium/high/xhigh/max；mode 为 standard/pro；tier 为 auto/default/flex/priority/scale。观察白名单用于识别不匹配，不授权发送这些其他配置。
- 不保存 reasoning 内容、加密推理项、错误正文、请求头或凭据。HTTP/传输错误的参数状态保持 MISSING。

worker 结果另有 `generation_contract_status`，从已保存字段与冻结 entry 计算：已知模型不同为 MISMATCH；否则任一参数 INVALID 为 INVALID；否则已知参数不同为 MISMATCH；否则模型或字段缺失为 MISSING；完整一致才为 MATCH。参数核验与输出是否合法分别报告，不能只凭 `status=OK` 声称参数通过。这个状态不改变原成功预测入账、失败分母或未知费用保留规则。受控执行方必须在下一题前审核它；不把 MISSING 默认为 MATCH。

## 数据库与升级

新增前向 `0019_provider_observation_metadata.sql`，原 19 份 SQL 字节不改。原 v1 的精确八键、重放与冲突规则继续有效。v2 额外核对实际已登记请求和 experiment entry：必须为官方 openai、gpt-6.1-terra 和原显式 generation 合同；不能靠 observation 自称 Terra 获准。

嵌套字段校验函数不授予 PUBLIC 或 worker。只读 capability 函数只授予 worker。新 worker 在 `prepare` 和任何 HTTP 派发前探测 capability，缺少 0019 时以 `OBSERVATION_V2_MIGRATION_REQUIRED` 停止，证明未发送的本次尝试可按原规则记零；数据库查询异常也不会发送，不擅自释放旧预留。

capability 分为 `to_regprocedure` 与实际调用两步，避免旧库在 SQL 解析阶段就引用不存在的函数。它证明接口存在，不替代对实际函数、权限、视图和触发器的安装审核。

原 observation、响应摘要、request、attempt、reservation、budget account 不更新；0006 输出检查触发器保持。先在独立副本验证升级，不能为部署此补丁升级历史源实验库。

## 验收与边界

回归覆盖 v1 字典不变、两类生成正文固定 SHA 不变、缺失/非法字段脱敏、两个 ID 分离、旧库零派发、权限、实际身份绑定、重放冲突、迟到 observation、HELD_UNKNOWN 以及升级前后历史行与迁移校验和不变。实际结果见 [Terra 交接审计](../../reports/TERRA_DIAGNOSTIC_AUDIT.md)。

这不是新的时间证明、网络修复或 FORMAL 准入。响应标签仅为供应商自报元数据，不能证明模型内部权重、执行细节或逐 token 可复现。
