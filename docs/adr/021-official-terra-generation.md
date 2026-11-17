# ADR-021：官方 Terra 的显式生成合同

2026-11-17，状态：本分支实施，真实请求待复测。

只对官方gpt-6.1-terra的prompt v2增加可冻结的fi.openai-generation.v1：reasoning={effort:medium,mode:standard}，service_tier=default。不支持或未确认的temperature/top_p/seed以明确省略原因记入配置，不发送给API。Python与数据库同时比较完整配置和实际请求；不开放任意参数透传，不改用中转。

已有无generation的历史DIAGNOSTIC保持原请求；新Terra FORMAL必须显式配置，且仍通过原独立审核、证据和时间门控。请求改变须先新建DIAGNOSTIC，不能重写旧实验。使用新0016，不改变任一已应用0014/0015或checksum。

该配置固定的是请求，不保证随机可重复性或模型别名背后权重不变，也不提供原始模型身份签名。当前仅代码/隔离测试；用户宿主DNS、生产TSA信任、实际费用与真实未来结算仍分别验收。文档和核查来源：../OFFICIAL_TERRA_RETEST.md。
