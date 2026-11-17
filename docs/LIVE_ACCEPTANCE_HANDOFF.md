# 真实验收交接：从工程基线到受控运行

适用版本：`2.0.1.dev1`。目标是沿现有问题、预算、调用、时间证明和结果账本完成真实闭环，不另造控制器，也不把模拟样本或历史回放冒充前瞻成绩。

## 1. 本轮能够确认与不能确认的事

安装包、数据库、重启恢复、隔离部署和浏览器通过的证据见 [基线报告](../reports/PREVIEW_BASELINE_VALIDATION.md)。这些证据不能证明目标服务器已部署、API 凭证有效、指定模型可用、费用已核对、TSA 独立可信或系统具备预测优势。

编写本交接时，执行环境没有可用于本项目的三家模型密钥或目标数据库连接，也没有指定生产宿主、真实模型 ID/预算和生产 TSA 信任配置。因此真实验收仍为 **BLOCKED / NOT_RUN**，没有以替代密钥、模拟响应或管理员直接写库制造成功。

## 2. 需要落实的运行输入

| 输入 | 要明确的内容 | 保管方式 |
|---|---|---|
| 目标环境 | 宿主/操作系统、可授权使用的执行入口、已有或新数据库、备份位置、允许访问的网络 | 非秘密说明可留文档；登录凭证不能进入公开仓库 |
| 模型与预算 | 每家实际可用 model ID、API 所属项目、单次和总额上限、输入/输出限制、价格核查时间与依据 | 冻结于新实验配置；不能沿用示例型号或过期价格 |
| 运行身份 | 各模型 worker、operator、来源管理员、入场复核、盲化裁决、时间验证等角色 | 环境或明确私有文件，按角色分开注入 |
| 时间服务 | 实际 endpoint、允许主机、独立核实 CA 指纹、policy OID、精度、撤销/长期校验策略及使用许可 | 独立验证者保管信任配置；测试 CA 不可升级为生产根 |
| 前瞻题源 | 来源权限、官方日历、预测截止、结果最早可知时刻、目标时期、原始/修订值规则和事件簇 | 真实原始快照留在受控存储，不随代码公开 |

只需要交接非秘密配置和安全执行入口；不要把 API key、数据库密码、访问令牌或账单贴进聊天/Issue。曾在别的项目使用过密钥，不代表本项目当前进程已经获得该密钥或付费授权。

## 3. 无联网预检

安装当前版本后，按角色执行：

```bash
python -m future_intelligence.runtime.preflight --role dashboard
python -m future_intelligence.runtime.preflight --role operator
python -m future_intelligence.runtime.preflight --role worker --provider openai
python -m future_intelligence.runtime.preflight --role worker --provider anthropic
python -m future_intelligence.runtime.preflight --role worker --provider google
python -m future_intelligence.runtime.preflight --role verifier
```

每条命令只检查所属角色。worker 必须指定 provider，其他角色禁止携带 provider。支持现有 `<VARIABLE>_FILE` 私有文件形式；直接变量与 FILE 同时提供会拒绝，空白/缺失、不可读、不安全权限、目录/管道等不会被当作有效配置。

Linux 新部署的文件读取示例（只有路径，不是密钥）：

```bash
FI_WORKER_DATABASE_URL_FILE="$PWD/deploy/.local-secrets/worker_openai_dsn" \
OPENAI_API_KEY_FILE="$PWD/deploy/.local-secrets/openai_key" \
python -m future_intelligence.runtime.preflight --role worker --provider openai
```

首次生成的模型 key 文件为空，预检应报告阻塞。不要为通过预检填假字符串。新配置目录的生成与权限见 [部署说明](OPERATIONAL_READINESS.md)；不要覆盖已有配置或重置数据库密码。

退出码 0：`LOCAL_CONFIGURATION_PRESENT_UNVERIFIED`。退出码 2：`BLOCKED_LOCAL_CONFIGURATION` 或参数错误。0 **不表示**数据库连接、凭证语法、网络、模型、预算、来源许可或时间资格已经验证；报告中的 `provider_verified`、`external_timestamp_verified` 和 `production_acceptance` 始终为 false。预检不会创建实验、领取任务或触发账单。Windows/其他宿主文件权限未在此轮验收，不假定与 Linux 相同。

## 4. 目标环境与三家真实接口验收

先按现有部署说明完成目标宿主初始化、角色隔离、只读工作台和备份检查。针对旧库必须走前向迁移，核对在途调用与未知费用，不使用清库重新开始。

每家登记新的 **LIVE + DIAGNOSTIC** 实验，使用获准输入，固定实际型号、请求限制、有效报价及单次/总额预算。分别绑定 worker，先执行小规模明确授权调用，再检查已落库的 request ID、返回型号、规范输出、usage 和真实费用依据。成功、拒绝、截断、超时及持久化失败分别记录；供应商错误路径只有实际观察到才记为真实，故障注入仍明确标为模拟。

既有命令和数据库操作顺序见 [模型接口](P0_03_PROVIDERS.md)、[集成说明](P0_04_07_RUNBOOK.md) 与 [运行说明](OPERATIONAL_READINESS.md)。不要绕过账本直接用一次随意 HTTP 请求替代系统验收。

请求已发出但返回或落库不确定时，保留 UNCERTAIN/HELD_UNKNOWN，不自动重发，也不把费用写成 0。报价估算不能当账单。原始调用与账单证据私下核对，公开仓库只记录脱敏结果与可定位的内部证据编号。单家通过不等于三家通过；DIAGNOSTIC 不能被事后升格为 FORMAL。

## 5. 独立外部时间服务与新正式实验

由独立验证角色确认生产 trust profile 后，再用新 FORMAL 实验开展完整入场审核与预登记。先取得并验证 REGISTRATION 的外部证明，等待其精度上界满足调用门槛，再执行模型任务；在截止前保留时间余量封存完整 FORECAST 批次并取得外部证明。不得以数据库时间、Git 提交时间、本地测试签名或事后每日根代替事前合格证明。

保留请求 nonce、原始签名回包、信任根/政策指纹、验证器版本与时间不确定度，独立导出重验。证明失败、晚到、撤销策略未落实或外部验证材料不足时，不应登记为已完成的生产时间验收。现有 `NOT_CHECKED` 的撤销状态需明确处理，不能在报告里隐去。

## 6. 真实前瞻结算与交付

首批只选择结果尚未知、来源已获准且规则可明确判定的少量题。日历和目标快照的 NOT_FOUND 仅是局部检查结果，仍需独立核查真实发布时间和资料完整性；同一官方发布的相关题按共同事件簇统计。

现实结果发布前保持 PENDING。发布后依冻结的首次值/修订规则保存原始证据、双人盲化确认、共享问题版本结果、保留所有失败/缺失分母，重新独立导出和计算。预测得分与覆盖率、成本和事件簇数量一起报告；小样本或单次成功不构成长期优越性证明。

最终私有验收记录必须包含代码 SHA、部署环境、实验/批次/请求/结果编号、实际测试命令、每项 PASS/FAIL/BLOCKED/NOT_RUN、时间/费用证据、故障与恢复过程、未测项和审查人。#5 只以三家真实证据关闭；#7 还需持续运行、外部告警送达、跨主机恢复与实际网络故障证据。工程主分支合并不自动关闭这些事项。
