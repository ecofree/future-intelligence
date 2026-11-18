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

## 4. 2026-11-18 续报：401 停止修复与真实复测阻碍

以上第 1–3 节保留 2026-11-17 的验收范围。本节追加后续状态，不把离线成功替代真实调用验收。

### 原运行方报告，待本轮审计包独立核验

- 源库 `fi_pr10_live` 保存原诊断 `902fdb78-b014-406a-9df8-5189128cfd33`；16 项已应用迁移的校验和及函数定义匹配，只缺 0016。独立副本 `fi_pr12_upgrade_copy` 升级通过，源库未升级、未清理。
- 本地分支 `integration/pr12-explicit-retest` 指向 `deaa94f1`；新诊断使用独立库 `fi_pr12_live`，实验为 `ff4a4bb1-1449-4e5d-9776-57ad1d85d119`。两题各一次官方 `gpt-6.1-terra` 请求均返回 HTTP 401，运行方确认密钥作废；无重试、换模或 FORMAL。
- 运行方报告离线 304 项、隔离库集成 185 项通过。这是原环境反馈，不冒充当前会话读取过日志或连接过实库。
- 0.14 美元旧未知预留加本轮 0.30 美元为 0.44；估算、预留和账单不同，401 不能自动释放预留。按每题 0.15 继续做两题新诊断和两题 FORMAL 将占用 1.04 美元，超限 0.04。恢复前须有可追溯费用对账或足以覆盖剩余四次请求的保守成本上界；不提高 1 美元授权，不为凑数降低预留。
- 本轮审计目录不在当前会话可访问的文件系统中，未取得其原始请求、错误与时间记录。不能据此认定两次 401 的派发先后或将其归因于下面的代码问题。

### 代码确认的缺陷及最小修复

在 `deaa94f1`，`parse_response` 保留 HTTP 401，但 `run_once` 返回的控制结果不含 HTTP 状态；有界 supervisor 只对 BLOCKED/NO_REDISPATCH/STOPPED_UNCERTAIN 停止。HTTP_ERROR 因此可能继续下一次领取，并最终退出 0。纯离线两轮复现可确认该控制流缺陷，不依赖现场两次请求的时间顺序。

补丁仅在工作器完成 `observe` 和 `fail` 后，返回经过类型和范围限制的 `http_status`；supervisor 遇 401 立即退出 2，不休眠、不领取下一题。既有 HTTP_ERROR、未知费用、失败 attempt 和未派发任务保持原语义。单独工作器原有非零退出行为保持；不引入迁移、新控制器或新依赖，不改变模型请求、提示词、证据、schema、SSRF 或 TSA 条件。

保证范围是当前 supervisor 循环，不能撤回在途并行请求或阻止另一个独立进程。后续运行使用单 worker、顺序两题，首个认证失败即停止，并复用既有 operator pause；不忽略非零退出码或自动重启。

### 当前工作区的实际检查

环境：当前 ChatGPT 工作区，由远端精确 `deaa94f1` 全历史克隆后追加修复；未连接用户数据库或模型凭据。

| 检查 | 命令或方法 | 实际结果 |
|---|---|---|
| 新回归先失败 | 未修实现上运行新增测试 | 预期退出 2，实际 0；确认可检出缺陷 |
| 完整离线回归 | `python -m unittest discover -s tests -v` | **305 项通过**；均无真实 API 调用 |
| Python 编译 | `python -m compileall -q future_intelligence tests` | 通过 |
| wheel 构建及内容核对 | `pip wheel . --no-deps --no-build-isolation`；既有 `check_distribution.py wheel` | 55 个包文件与源码快照匹配；wheel SHA256 `5d6bad862af1c46aac6dbf2e75457fefb9ab8cc44a590c54da22d3f806491bab` |
| 仓库外隔离安装 | 新 venv 仅安装本地 wheel；`python -I .../check_distribution.py installed ...` | 55 个包文件、17 份迁移全部匹配；不声称已安装 postgres/web 可选依赖 |
| 迁移保护 | 逐文件对比 `git show deaa94f1:<path>` 与工作区字节 | **17 份完全相同**，没有新增或修改 SQL |
| 用户历史保护 | `git merge-base --is-ancestor` | 4d4b91a、f5f66a8、f9eafb1 均保留 |
| 补丁空白检查 | `git diff --check` | 通过 |

新增回归串联真实 `run_once` 控制流与 `loop`，仅 transport/仓库使用合成夹具：验证恰好一次 claim/send、observe→fail 保存顺序、HTTP_ERROR/401、actual_cost 与估算仍为 None、retryable=False、不休眠、不出现第二次领取，结果和 observation 不含模拟密钥或原始错误正文。

此修补提交的数据库升级、重启、打包及部署回归使用 PR #12 现有隔离 CI；结果绑定新提交并在该 PR 追加，不沿用 deaa94f1 的绿灯。当前工作区没有 PostgreSQL 服务，不宣称本地跑过数据库集成。

### 阶段结果

本次 ChatGPT 修补阶段新增真实调用 **0**、费用 **0 美元**、新实验 **0**；最近一次运行方 DIAGNOSTIC 为 `ff4a4bb1-1449-4e5d-9776-57ad1d85d119`，两次认证失败不证明新请求合同已被官方接受。FORMAL、生产时间证明和结算任务均未创建。官方有效凭据、完整后续预算、正常 DNS、生产 TSA 信任及独立入场仍待解决。PR 保持草稿，不合并 main 或部署生产。
