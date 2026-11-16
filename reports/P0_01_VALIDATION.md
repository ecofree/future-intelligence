# P0-01 验收记录（候选提交）

日期：2026-11-16。基于远端 `99046e2e814409982a28e039bd00830c64c3a107`。

## 已实际运行

- Linux / Python 3.13.5：`python -m unittest discover -s tests -v`，44 passed，0 failed。
- `python -m compileall -q future_intelligence tests scripts/check_persistence.py`，通过。
- 原离线评分/Merkle/Provider 契约测试保留，无真实模型调用或外部时间戳声明。

## 当前环境限制与待验收

本地环境不能解析 github.com，无法 git clone；读取和提交使用已授权 GitHub 接口。
本地无 PostgreSQL 服务和 psycopg，依赖安装尝试失败。**没有以 SQLite 或 mock 代替 PostgreSQL 通过验收。**
`PostgreSQL question ledger` CI 将分别运行 PostgreSQL 16/17：包安装后迁移、真实角色权限、
定义冻结、并发版本/冻结、许可撤销/替代、审计和迁移校验，以及实际重启服务器后的数据指纹比对。
这些集成检查在本候选报告生成时为 **PENDING**，以实际 CI 结果及后续验收记录为准。
缺少 FI_TEST_DATABASE_URL 或驱动会导致集成测试失败，不会静默 skip 为成功。

## 实现边界

交付 PostgreSQL schema、追加式定义、review/freeze/close、来源许可和 Python gateway。
尚无 HTTP API、工作台、任务账本、真实模型适配器、TSA/OTS、独立结果裁决和真实前瞻成绩。
修订口径在问题版本保存，真正的发布值选择/结算及多模型共享结果在 P0-05 验收。
应用不能更新/删除定义，但数据库管理员能更改 schema；不声称绝对不可篡改。
冻结时间来自数据库，不是外部可信时间证明。
