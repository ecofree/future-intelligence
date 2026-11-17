# V2.0.1 架构决策索引

状态：设计与实现分别记录，不因文档冻结而声称生产已完成。

## 初始ADR-01～10

| 编号 | 决策 | 验收证据 |
|---|---|---|
| ADR-01 | 实验、完整题集与模型任务预登记；任务仅一份初始提交；披露失败与未披露项；私密承诺加随机nonce | 删除/遗漏/重复正式任务能被批次对账检测；结果后挑版本被拒绝 |
| ADR-02 | TSA与OTS独立状态；校验失败不等于成功；时间误差和截止资格预登记 | 截止后证明不能倒签；故障不伪造VERIFIED |
| ADR-03 | 实验管理、评分、统计、展示逻辑分离，不拆成四服务 | 得分追溯到问题/预测/结果/算法版本 |
| ADR-04 | 样本量依据试点方差与目标效应估计；正式计划冻结 | 假设和来源可导出，不以固定题量宣称显著 |
| ADR-05 | 主比较预登记，Holm调整，探索性结论标记 | 校正值可复核，不显著不等于等效 |
| ADR-06 | 事件簇预登记与聚类Bootstrap | 同簇整体重采样，题量/簇量/种子分别报告 |
| ADR-07 | 数据许可机器门控，默认拒绝 | 过期/撤销/缺用途阻断处理；公开导出另审查 |
| ADR-08 | 外部网页不可信，权限/密钥不受网页指令控制 | 提示注入/SSRF/超大文件与凭据保护 |
| ADR-09 | 早期信号时间、预期、失效条件和观察留档 | 失败信号同样保留；P1 |
| ADR-10 | 研究价值与预测评分独立评价 | 前瞻目标、证据和反馈审计；P1 |

## 已实施阶段补充

- [ADR-011 问题持久化](011-question-persistence.md)
- [ADR-012 实验与任务账本](012-experiment-task-ledger.md)
- [ADR-013 冻结证据与调用](013-frozen-evidence-live-calls.md)
- [ADR-014 完整批次与独立时间](014-complete-batch-time-proof.md)
- [ADR-015 盲化裁决与历史评分](015-blind-outcome-score-history.md)
- [ADR-016 预登记统计与最小工作台](016-fixed-batch-statistics-workbench.md)
- [ADR-017 工程运行与前瞻验收分开](017-operational-versus-prospective-acceptance.md)
- [ADR-018 运行入场与本地部署](018-operational-admission-and-local-deployment.md)
- [ADR-019 工程基线与本地预检](019-preview-baseline-and-offline-preflight.md)
- [ADR-020 配置驱动的 OpenAI 兼容供应商](020-openai-compatible-providers.md)

各实现的真实测试、残余风险和外部依赖，以[验收索引](../../reports/VALIDATION.md)和Issue #5/#7为准。
