# 当前验收索引

更新：2026-11-17。验收绑定具体提交；历史报告不改写。整合分支不是生产部署。

## 当前整合

[Terra整合与显式请求合同](TERRA_INTEGRATION_VALIDATION.md)：已取得integration/pr10-official-retest的4d4b91a与脱敏交接包，以双父提交bce7458整合PR11的c9cbcae；保留双方历史和原16份SQL。新0016固定官方Terra生成参数。当前提交的完整CI结果须按本批PR实际记录，不沿用父分支状态。操作与历史差异分析见[官方复测说明](../docs/OFFICIAL_TERRA_RETEST.md)。

## 已有报告（保持各自时点与范围）

- [历史迁移顺序漏检修复](HISTORY_ORDER_AUDIT_FIX.md)：PR #11，代码c9cbcae；268项离线、同一套167项PG16/17与部署/打包检查见该PR实测留言。哈希一致不代表执行顺序或最终定义已核验。
- [累积列表分页与只读迁移核对](CUMULATIVE_PAGINATION_VALIDATION.md)：097ce460，256项离线/164项数据库；205记录分页和精确查询。
- [Grok兼容通道历史诊断](OPENAI_COMPATIBLE_VALIDATION.md)：保留f5f66a8/f9eafb1阶段记录。不是当前仅官方Terra试点的参赛资格。
- [显式答案合同v2](CONTRACT_FIX_VALIDATION.md)：a31e1e9，231项离线/158项数据库；旧提示词和失败记录保留。
- [工程预览基线](PREVIEW_BASELINE_VALIDATION.md)、[运行准备](OPERATIONAL_READINESS_VALIDATION.md)、[P0-04～07](P0_04_07_VALIDATION.md)。
- [P0-03](P0_03_VALIDATION.md)、[P0-02](P0_02_VALIDATION.md)、[P0-01](P0_01_VALIDATION.md)、[初始化](BOOTSTRAP_VALIDATION.md)、[初始化发布](GITHUB_BOOTSTRAP.md)。

本轮只允许官方OpenAI Responses、gpt-6.1-terra，总新增调用预算不超过1美元。不调用中转，不换模型；兼容代码保留只为保护原历史。未读取运行方原始请求/证据/账单，不将脱敏摘要等同完整独立验收。没有新FORMAL、生产时间证明或结算任务；模型真实费用仍须与供应商核对。主分支和现有PR不自动合并。
