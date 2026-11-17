# 累积列表分页与迁移只读审计：验收记录

日期：2026-11-17。PR #11，分支 `codex/cumulative-pagination-audit`，基于可读的PR10 head `12c5d11c3bba585ccec1b752f220cb6373796743`，不是用户未推送的 `integration/pr10-official-retest`。main未改、未部署生产、没有修改SQL迁移或模型请求。

代码验收提交：**`097ce460813d728727afed90ca1516a3e23c7a21`**。首个功能提交为 `987e856743c7181287ed64ec869b853a8d472afa`；后一个提交仅重命名同名集成测试模块，内容blob `7bd0b9d96c01c02104e9f337422892081be22949` 未改变，全部断言保留。

## 1. 实际执行结果

| 检查 | 实际结果 | 证据 |
|---|---|---|
| 完整离线回归 | **256项**，Python3.11/3.12/3.13成功，含新增25项 | [Offline core 44042342135](https://github.com/ecofree/future-intelligence/actions/runs/44042342135) |
| 真实PostgreSQL | 同一套 **164项** 在16/17成功，含本批6项，不把矩阵次数当更多独立测试 | [PostgreSQL 44042342147](https://github.com/ecofree/future-intelligence/actions/runs/44042342147) |
| 累积列表 | 205个FIXTURE实验，默认200条以外的ID精确可查；完整多页遍历无重复、重复遍历一致；四种实际视图及盲化权限通过 | 同上 |
| 可读取远端的数据库升级 | 临时数据库先应用远端13迁移、保存测试行和旧哈希，再应用PR10的0014；行和旧哈希不变，重复迁移为空 | 同上；**不是用户本地两份0014与0015验收** |
| 只读迁移审计 | 实际DB元数据读取前后相同，脚本不执行迁移/修复/DDL | 同上 |
| 实际数据库重启 | 原有检查点、暂停、安全恢复、未知费用和重复迁移均通过 | 同上 |
| 实际部署与浏览器 | Docker新安装、重复初始化、停库恢复、同集群新库备份还原；Chromium实测100/100/5三页、上一页、精确过滤、四标签与令牌清空通过 | [Deployment 44042342009](https://github.com/ecofree/future-intelligence/actions/runs/44042342009) |
| 安装包 | 实际wheel构建、源码一致性与隔离安装通过 | [Distribution 44042342130](https://github.com/ecofree/future-intelligence/actions/runs/44042342130) |
| 本地局部回归 | 新增25项通过、compileall通过 | 局部目录，不冒充完整clone、数据库或生产宿主验收 |

已读取PG17 job `133899670225` 完整日志，确认实际输出 `Ran 256 tests / OK`、`Ran 164 tests / OK`，本批6项逐项通过。PG16 job `133899669855` 全部步骤亦为success。上述数据库、Docker与Chromium证据来自GitHub Actions临时环境，没有连接用户fi_live/fi_live2。

PR测试使用合并引用 `eac281719f4ed357ba6ceb38e0fa7d63405ad903`，对应097ce460对PR10基线的测试。后续若仅更新验收文档，必须记录新提交自身检查，不能将旧提交绿灯冒充新提交执行结果；最终状态见PR #11留言。

## 2. 失败与修复记录

首轮提交987e856的离线、安装包、实际部署/Chromium成功，但数据库检查 [44041751943](https://github.com/ecofree/future-intelligence/actions/runs/44041751943) 在测试发现阶段失败：unit与integration均叫 `test_list_pagination.py`，原有fixture helper把tests目录加入sys.path后产生模块导入位置冲突。

修复为把新增集成文件改名为 `tests/integration/test_cumulative_list_pagination.py`。没有删测试、放松断言或改产品代码。修复后上述四类检查均成功。旧运行类型测试继续断言LIVE/DIAGNOSTIC/UNCONFIGURED等值，只将读取方式改为精确实验ID，避免假设新实验总在前200个随机UUID中。

原repository.py与workbench.py在本地重建后按Git blob逐字节核对，分别与远端 `a84605e9d573755db7fd14a6f2e3c3779247fa24`、`d96013c5ab26965410560f1382fdfbdea87aad4e` 相同。本地为加载分页测试隔离了未使用的crypto导入；未模拟数据库或宣称本地全仓验收。

## 3. 修复边界

四类列表保留v1 JSON数组，显式page_size启用keyset继续游标；时间排序使用UUID打破同时间记录的并列，游标绑定资源/筛选/页大小。权限仍由原数据库身份保证。旧无参数调用明确标记仅第一页，页面只报本页条数。

分页是实时视图，不是事务一致性快照；并发新增需刷新。正式审计与完整分母继续使用既有批次导出，不以列表分页代替。没有修改任何预测、评分、TSA验证、SSRF防护、采样参数、数据库迁移或原实验。

## 4. 实际闭环状态与阻塞

| 项目 | 当前结果 |
|---|---|
| 用户既有Terra DIAGNOSTIC | 用户报告成功；ID保留在私有交接材料。本环境未读取原始账本，不否定报告，也不标为已独立核验 |
| 本地4d4b91a/f5f66a8/f9eafb1及三份交接文件 | 无法访问；远端精确SHA查询未找到，不能假设已推送 |
| 两份0014、0015与fi_live2升级 | 未见文件/DB，不作正确性结论 |
| 旧fi_live校验和差异 | 实际来源尚未定位；已提供只读清单与不丢数据的定位顺序，未执行修复 |
| 新增真实模型调用/费用 | **0次 / 0美元**；用户1美元上限未消耗 |
| 新DIAGNOSTIC / FORMAL | 均未创建 |
| 新事前时间证明 / 生产信任审核 | 未申请 / 未完成 |
| 自动结算安排 | 未创建；待全新FORMAL成立后才安排 |

官方参数文档和CPI日历核查见 [交接缺口说明](../docs/OFFICIAL_RETEST_HANDOFF_GAPS.md)。文档核查不等于请求参数已实际固定，也不等于取得日历原始快照或正式准入。

下一步必须取得原integration分支的推送或包含完整祖先的bundle，以及三份脱敏交接材料。先核对迁移历史与精确请求，再新建总预算不超过1美元、仅官方 `gpt-6.1-terra` 的二元/五类别诊断。正常出网、独立TSA信任审核与其他前置条件未通过前，不创建FORMAL，不补造事前证明。PR保持未合并。
