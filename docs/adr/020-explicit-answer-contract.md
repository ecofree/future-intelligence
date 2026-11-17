# ADR-020：修复类别合同，不扩大产品范围

日期：2026-11-17。基于 main `0adde2cb`。本次只修用户提供的 Grok 报告中的分箱合同缺陷和正式端点准入，不新增数据源、Agent、重复采样或基础设施。

## 决定

问题 v1 的含义和评分算法保持不变。新增由冻结问题派生的 `fi.answer-contract.v2`：明确 category_count、categories、单位、整数概率总和。二元顺序为 No/Yes；数值区间与既有 outcome_bin 一致，左闭右开，包括两端无界区间。四个边界明确生成五类，不依赖模型猜测。

新实验显式冻结 `providers.live.CURRENT_PROMPT_SHA256`，当前值为 `3c5510dba1ee4691d8a448bef792eacf3c03e2e434c507643a45e3223c4d38da`。旧 `PROMPT`/`PROMPT_SHA256` 保持 v1 原值与行为，供历史重放；不把既有实验升级或重算为新版。输入合同版本由参赛配置的冻结提示词哈希决定。请求内容、原始配置、证据摘要继续使用既有账本保存。

OpenAI/Google v2 schema 将数组 minItems/maxItems 固定为 K，并约束整数范围。Anthropic 的文档子集不支持任意 K 的数组长度及数值边界，故其原生 schema 使用明确 description；相同的显式输入、Python 与数据库校验仍执行精确 K、范围、总和和引用检查。不静默丢约束再重试，不声称其拥有与另两家相同的原生长度保证。账户具体模型支持仍须真实请求验收。

数据库以新增迁移独立派生相同合同，核对 request 中的 schema 与输入；不是只依赖提示词。非法输出仍失败，不补齐概率、不归一化、不剥除代码块、不覆盖旧失败。

主分支正式实验只允许现有固定官方端点适配器的精确配置；自定义 endpoint/base_url/protocol/trust_level 等参数不能靠标注官方而进入 FORMAL。模式配置、批次捕获和请求写入分别检查。该门控只是配置资格，不是上游身份的密码学证明。DIAGNOSTIC 不可事后升格。Grok 报告中的 compatible.py 和本地第14迁移未出现在本轮远端，因此不伪称已合并或测试其私有兼容实现；回接前必须解决迁移编号与协议冲突。

## 保持原约束

超时仍为未知调用，不盲重试、费用不归零；不放开 fake-IP 网段；本轮不扩展超时上限或 TSA 信任。缺乏真实模型/生产 TSA/现实结果证据时不计为正式前瞻通过。

## 核对的官方协议资料

2026-11-17 读取：
- OpenAI：https://developers.openai.com/api/docs/guides/structured-outputs
- Google：https://ai.google.dev/gemini-api/docs/structured-output?lang=rest
- Anthropic：https://platform.claude.com/docs/en/build-with-claude/structured-outputs

这些资料描述协议能力，不是本账户实测结果。尤其不将第三方宣称兼容等同于约束生效。
