# P0-01 验收报告：问题账本持久化切片

日期：2026-11-16。状态：**本批持久化切片通过；完整 MVP 尚未完成**。
代码验收提交：`b3c2bc4caf9b561031562ceabc8faef7913fae30`。
初始数据库提交：`5d67bba76514916bcdd577bf7d08c319847a588a`。
开发分支：`codex/p0-01-question-ledger`；PR #2 叠加在初始化分支上，尚未合并 main。

## 实测结果与可追溯证据

| 检查 | 实际结果 | 环境/证据 |
|---|---|---|
| 离线测试 | 44 passed，0 failed | 本地 Linux / Python 3.13.5，再次执行确认 |
| 编译 | 通过 | `python -m compileall -q future_intelligence tests scripts/check_persistence.py` |
| 离线 CI | completed / success | [Offline core #7](https://github.com/ecofree/future-intelligence/actions/runs/43842914021) |
| PostgreSQL 16 | 全部步骤 success | [PostgreSQL question ledger #4](https://github.com/ecofree/future-intelligence/actions/runs/43842913952)，job 133215780982 |
| PostgreSQL 17 | 全部步骤 success | 同一运行，job 133215780702 |
| 数据库测试 | 每个数据库版本运行同一套 28 项测试 | 22 项基础 + 6 项事务边界；不是 56 个独立测试用例 |
| 安装后迁移 | 成功 | 在 /tmp 运行安装包内迁移，确认 SQL 包资源存在 |
| 实际数据库重启 | 通过 | docker restart 后，8 张表的行数、内容指纹与冻结记录一致 |
| 再次迁移 | applied=[] | 没有重复执行或改写历史迁移 |

已读取 PostgreSQL 16 的完整 job 日志：44 项离线测试和 28 项集成测试均为 OK，
两份迁移正常应用；重启验证输出 `PASS: all eight tables and frozen records survived actual PostgreSQL restart.`。
另核对 PostgreSQL 17 的全部 job steps 均 completed/success。
原始第一轮（提交 5d67bba）两版本也通过，当时数据库用例为 22 项；不能拿旧运行替代新提交验收。

## 已验证的行为

问题创建、独立身份审核、冻结、重新连接读取；草稿直接冻结被拒；内容修改产生新版本且旧版本不变。
应用直接写表被拒；作者即使同时有审核角色也不能自审；审计 actor 来自实际数据库登录。
并发同父版本只接受一个新版本；并发冻结只保留一次冻结审计事件。
缺失用途、过期/未生效许可、撤销、被新版本替代、重复或不存在的许可均被拒。
绕过 Python 直接调用 SQL 仍执行结构、时区、分箱和时间范围验证。
已应用迁移内容被改后拒绝继续；事务回滚不留下半份问题。

## 发现并修复的事务边界

1. 旧 REPEATABLE READ / SERIALIZABLE 快照可能漏看已提交的许可撤销。
   新增 0002 将许可准入限制为 READ COMMITTED，不改写 0001 的历史校验值。
2. 逐来源“锁定后立即检查”可能在等待后续来源锁时让先前许可过期。
   改为先锁全部来源，再用同一时点检查所有许可。
3. 新增真实锁等待测试：确认 pg_stat_activity 已显示 Lock 等待后，才提交撤销或等截止到达并释放锁。
   验证撤销在等待后可见、许可和预测截止在等待后再次校验；不是 mock 并发。

## 代码一致性与环境限制

通过已授权 GitHub 接口读取及提交；本地不能解析 github.com，未声称完成本地 git clone 复测。
本地无 PostgreSQL 服务且 psycopg 安装失败；真实数据库验收由上述 GitHub Actions 完成，未用 SQLite 替代。
按本地文件计算 Git blob SHA 重建远端树，初始树 `fa03d1b02d3f07ffd29236b4301b9385cf02982a`、
加固后树 `047e7c7188c083c1b5a000574a02d2e324ae0543` 均与提交树一致。
本报告后续提交仅更新文档；新增代码变更必须重新执行对应测试。

## 尚未完成 / 不包含在本次验收内

- P0-02 实验预登记、任务账本、预算预留及 worker 恢复。
- HTTP API、登录到数据库权限的可信映射、工作台与生产部署。
- 三家真实模型调用、证据采集、RFC3161/OTS、现实结果裁决、统计比较、真实前瞻预测。
- 数据修订政策已随问题版本保存；实际发布值选择、修订重算、所有模型共享问题级结果属于 P0-05 跨阶段验收，尚未通过。
- 数据库管理员可以更改 schema/触发器；应用只追加权限不等于绝对不可篡改。
- frozen_at 为数据库时间，不是外部可信时间证明；时间顺序检查不是实际信息泄漏检测。
- 后续每次采集、模型处理和公开导出仍需检查许可，不能把冻结时许可有效视为永久授权。

## 成本与维护提示

本批没有付费模型调用、时间戳请求或行情订阅。GitHub Actions 计费/配额未查，不能宣称总运行成本为零。
日志提示现有固定 SHA 的 Actions 面向 Node20、运行器改用 Node24；本轮成功，但需独立维护任务升级并验证。
28 个集成测试不是所有生产场景的完备证明；服务器重启验收不等于备份恢复、长期容灾或生产容量验收。
