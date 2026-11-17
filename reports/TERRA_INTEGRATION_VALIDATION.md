# Terra整合与显式请求合同：本批验收

2026-11-17，PR #12。代码验收提交 **28b3de460b3db0eee7ce9dbd7f31dd1a15a97a42**；双父整合提交 **bce7458176c34d7d42ea69425db6374660bdb01c**，保留用户4d4b91a及PR11 c9cbcae两侧全部祖先。未修改main、用户integration分支或任何原有SQL文件。三份脱敏报告与两个只读清单已收到，不再将它们记为不可访问。

交接ZIP SHA256为43603dac7312eb005d5c04b8740cdcc2ce76fd03b094be1b72314054b2aade9f；21项清单全部匹配。旧fi_live差异来源由f5/f9源码和交接清单定位：f9改写了已经在旧库应用的compatible0014。实际旧库函数/数据尚未连接核验，旧库继续阻断，未修写checksum。fi_live2清单哈希匹配；其非规范应用顺序另作实际SQL回放核查，不能仅凭文件名0015认定协调成功。

## 1. 实际变更

保留双方历史、兼容诊断代码和原16份SQL字节；整合PR11分页、精确实验查询、只读迁移核对。唯一新增SQL为0016，固定官方Terra显式generation配置：medium/standard、default服务层。temperature/top_p在此档位不支持而明确省略，seed未获得Responses支持确认而省略；不把省略标记发送到API，也不宣称固定随机性或上游权重。

Python与数据库同时核对完整配置和请求字节中的参数。旧prompt/request不改，旧诊断不升格；新Terra FORMAL要求显式配置，并保留原时间/权限/证据/费用/完整分母约束。没有新增供应商、来源或Agent，没有改变TSA或SSRF策略。

## 2. 代码28b3de46的实际CI：全部通过

| 检查 | 实际结果 | 证据 |
|---|---|---|
| 完整离线回归 | 290项，Python3.11/3.12/3.13成功；包含整合进来的兼容历史测试和本批8项 | [Offline 44060562892](https://github.com/ecofree/future-intelligence/actions/runs/44060562892) |
| 真实数据库 | 同一套182项在PostgreSQL16/17通过；未把矩阵重复次数当更多独立测试 | [PostgreSQL 44060562745](https://github.com/ecofree/future-intelligence/actions/runs/44060562745) |
| 四条迁移路径 | 13→17、当前兼容14→17、显式14→17、整合16→17，逐项比较全部fi函数定义/权限与新库；原行、旧哈希和应用时间不变，重复迁移为空 | 同上，tests/integration/test_integrated_migration_paths.py |
| 请求合同与门控 | 二元/五类别真实落库、参数篡改拒绝、旧诊断保留、FORMAL缺配置拒绝、权限和两个合成签名门控均通过 | 同上，tests/integration/test_terra_generation_db.py |
| 数据库重启 | 全部原有检查点、暂停、安全恢复、未知费用保留和重复迁移通过 | 同上 |
| 部署与浏览器 | Docker新部署、重复初始化、停库恢复、同集群新库备份还原、累积列表Chromium检查成功 | [Deployment 44060563029](https://github.com/ecofree/future-intelligence/actions/runs/44060563029) |
| 安装包 | 实际wheel构建、源码一致性、仓库外隔离安装检查通过 | [Distribution 44060562789](https://github.com/ecofree/future-intelligence/actions/runs/44060562789) |

已读取PG17 job133961062159日志，确认实际输出Ran 290 tests/OK、Ran 182 tests/OK及四条路径、新门控逐项成功；PG16 job133961062152各步骤也成功。PR测试合并引用9f3a50a3cddc448e9aa23bba9115b97739c20640，测试目标为28b3de46对用户integration分支的整合。

本地仅Python编译和8项隔离生成配置测试通过；使用原有依赖替身，不冒充本地完整仓库、数据库或API验收。全仓/数据库/Docker/Chromium证据来自Actions临时环境，模型及TSA为明确合成夹具。没有连接用户fi_live、fi_live2或fi_pr10_live。

四条升级回归中的compatible14是f9版本，不是旧fi_live的f5历史部署。它们证明已提交SQL在测试环境收敛，不代替旧库恢复、实际库定义采集或真实升级授权。原PR11的13→explicit14测试保持精确范围及断言，未被新迁移数量替代。

本次报告追加只改Markdown；该文档提交自身CI状态另见PR #12最终记录，不能将28b3de46检查冒充后续提交已运行。

## 3. 真实运行与剩余阻碍

本轮新增真实模型请求0次、费用0美元；新DIAGNOSTIC/FORMAL均0、生产时间证明未申请、结算任务未创建。既有真实诊断的摘要和材料哈希已核对，但原请求/证据/回包未随脱敏包提供，不能声称独立重放。原调用估算0.008101美元不是实际账单，旧0.14美元UNKNOWN预留不释放。

已获得源码交接不等于已获得原宿主安全执行连接。当前会话没有原数据库或安全注入的worker凭证；不需要用户再次把API密钥贴到聊天。下一步由原运行环境使用本分支及docs/OFFICIAL_TERRA_RETEST.md，先在隔离副本核对升级，再用显式合同新建两题DIAGNOSTIC，保留所有失败/未知费用。

正常DNS、生产TSA独立信任根/链/精度/政策/撤销策略、独立入场审核仍是FORMAL前置条件。所有条件通过后才另建FORMAL、取得事前证明；随后在现实公布后盲化结算。全程仅官方gpt-6.1-terra、总新调用预算不超过1美元，不调用中转、不换模型、不自动重试。main和现有PR不自动合并，用户生产环境未部署。
