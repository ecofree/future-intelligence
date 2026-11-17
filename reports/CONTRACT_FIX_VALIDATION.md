# 分箱合同与正式端点约束：验收记录

日期：2026-11-17；基线 main `0adde2cb7bab54eabb39c85af910cef4d3892283`。
代码验收提交：`a31e1e9fc7704e279dcc7b5ed5a7f28467aefc5a`，PR #10。

本批只修用户所提供Grok实测报告中的分箱合同缺陷和正式端点配置资格。未新增数据源、Agent、服务、表或采样系统；不公开原始附件、真实调用材料或秘密。

## 实际执行结果

| 验证 | 结果 | 记录 |
|---|---|---|
| 完整离线回归 | 231项通过，Python3.11/3.12/3.13成功 | [Offline core](https://github.com/ecofree/future-intelligence/actions/runs/44030080729) |
| 真实PostgreSQL集成 | 同一套158项在PostgreSQL16/17通过，包括本批10项；不是把矩阵次数算作更多独立测试 | [PostgreSQL](https://github.com/ecofree/future-intelligence/actions/runs/44030080694) |
| 实际数据库重启与恢复 | 所有原有检查点、暂停约束、安全任务恢复、未知费用保留及重复迁移通过 | 同上，重跑迁移applied=[] |
| 安装包 | 实际wheel构建、源码一致性和隔离安装检查通过 | [Distribution](https://github.com/ecofree/future-intelligence/actions/runs/44030080719) |
| 实际部署与浏览器 | Docker新部署、重复初始化、停库/恢复、同集群新库备份还原、Chromium操作及清理全部成功 | [Deployment](https://github.com/ecofree/future-intelligence/actions/runs/44030080764) |
| 本地局部回归 | Python3.13.5，新增20项通过，compileall通过 | 本地仅本批所需模块，不冒充完整clone或完整旧回归 |

已读取PG17 job日志133859625017，确认实际输出“Ran 231 tests / OK”“Ran 158 tests / OK”，本批10项逐项通过。PG16的全部执行步骤亦为success。真实数据库与浏览器证据来自Actions临时环境，不是用户生产宿主验收。

源代码已先与原始Git blob核对：live.py为0f676d28159ed278301e7ed80059768c4119b476，questions/spec.py为c3e16cf009fc00683a99265e6cf4c781064de1f2。本地无PostgreSQL/Docker，git访问仍有DNS限制。最终文档提交不改代码/迁移/测试，其CI结果及合并状态在PR #10另行记录，不预填成功。

## 这次测试具体验证了什么

- 四个边界派生五个明确类别，二元为No/Yes，有限下界包含、上界不包含，单位和十进制字符串保留。
- SQL与Python在不同类别数下生成相同合同；新提示词哈希选择v2，v1请求内容与旧记录保持不变。
- 三家原生请求按各自schema子集生成；OpenAI/Google有原生精确K约束，Anthropic没有不受支持的长度/数值关键字，仍有相同语义输入和严格本地/数据库校验。
- 篡改类别顺序、边界、数量或schema被拒绝；原始错误的四概率输出继续失败，不补齐、不归一化；失败留在分母、费用保持HELD_UNKNOWN。
- LIVE FORMAL配置拒绝额外端点/协议/信任标签；DIAGNOSTIC不能改成FORMAL。此门控只证明配置符合现有原生适配器，不证明上游身份。
- 新合同确实经过原有正式入场、预登记、请求、预测封存与两个批次的签名验证路径；临时测试CA和合成模型输出均明确为fixture，不能算真实前瞻。

## 上线与真实复测边界

远端没有Grok本地compatible.py或其第14迁移。当前0014适用于已核对的13迁移基线；对已有额外本地迁移的库，现有migrate会因未知文件或校验和差异拒绝，不得删除历史记录、改旧迁移或清库绕过。必须先保留该环境代码/数据并完成分支与迁移整合。

Grok此前的真实调用报告仍有效地表明其环境发生了调用；本轮尚未取得该环境的完整提交/账本，也没有重新调用OpenAI验证v2。生产TSA、证书撤销/精度依据和真实结果结算未在本轮完成。#5/#7保持开放。

保留120秒上限、未知调用不盲重发、严格出网策略、未知费用不归零。本轮不把重复采样作为重试，不修补模型答案，不扩张产品范围。

下一步是按 [复测说明](../docs/CONTRACT_FIX_RETEST.md) 在可联网安全环境执行官方OpenAI的新DIAGNOSTIC二元/五分类请求；成功后新建FORMAL实验并等待真实结果，不再扩展基础设施。协议决定见[ADR-020](../docs/adr/020-explicit-answer-contract.md)。
