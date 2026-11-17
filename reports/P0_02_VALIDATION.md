# P0-02 实验预登记与任务账本：验收记录

日期：2026-11-16。范围：数据库控制面、受控任务执行契约和显式 FIXTURE 执行器。
**结论：此切片已通过真实 PostgreSQL 16/17 验收；不是完整 MVP，不包含真实模型或预测成绩。**

## 版本与可核验记录

- 基线：`f869605456e05a2463d1d94a304120724f2d7d82`（P0-01）。
- 首版：`f71cdbf3c8a111b2a2d42c1af2c3a53397a120cf`，首次数据库检查暴露一个 UUID 类型兼容错误。
- 修复及代码验收：`db76bbf023c33b3ef61125074ba4d9a748bf2fce`。
- [PR #3](https://github.com/ecofree/future-intelligence/pull/3)，叠加于 `codex/p0-01-question-ledger`，未合并 main。
- [离线检查 #14](https://github.com/ecofree/future-intelligence/actions/runs/43858897212)：completed / success。
- [PostgreSQL 检查 #11](https://github.com/ecofree/future-intelligence/actions/runs/43858896998)：16、17 两组全部步骤 success。

CI 按 PR 合并视图检出；代码验收运行的合并视图为 `dbff339455b438ace4714ee25445818350f13ed2`（db76bbf 合入未改动基线）。PR 未实际合并。文档提交后的最新 CI 以 PR 检查为准，不能把上述提交结果自动冒认为未来提交结果。

## 实际执行

| 环境 / 命令 | 结果 |
|---|---|
| 本地 Linux / Python 3.13.5：`python -m unittest discover -s tests -v` | 50 passed；本地工作副本为初始化30项＋新增20项，缺少 P0-01 的14项，不声称完整本地回归 |
| 本地 `python -m compileall -q future_intelligence tests scripts` | 通过；不等于 SQL 执行通过 |
| 远端 Python 3.11/3.12/3.13 离线 workflow | 全部 success |
| 真实数据库 workflow：`python -m unittest discover -s tests -v` | 64 passed，0 failed |
| 真实数据库 workflow：`python -m unittest discover -s tests/integration -v` | 每个 PostgreSQL 版本执行同一套66项，全部通过；含旧28项＋新增38项 |
| 安装包外运行迁移 | 0001～0004 执行通过，重复执行 applied=[] |
| 实际 `docker restart` PostgreSQL | 两版本均通过；旧8表和新增11表内容指纹不变 |
| 重启后恢复控制 | 未发出任务重新排队可领取；旧 fence 拒绝；已发出未知调用保留 UNCERTAIN 和费用预留 |

本地不能解析 github.com，也没有可用 PostgreSQL/psycopg；没有声称在本地重克隆或运行数据库。真实数据库、真实并发锁、真实服务器重启均来自 GitHub Actions，不使用 SQLite/mock 替代数据库。

## 首轮失败与修复

首轮 PostgreSQL workflow `43857967249` 运行60项数据库测试，1项 ERROR：驱动返回的 `UUID` 对象传到只处理字符串的取消任务接口，触发 `AttributeError`。其余59项通过，不能把该轮记为通过。
已通过 `uuid_text` 明确支持字符串和 UUID，保留原测试，并增加类型回归。另用前向迁移0004阻止超支后已经领取但尚未派发的任务开始。0001、0002、0003 不改写。

## 关键验收证据

- 完整实验任务集合、登记顺序规范化、相同请求幂等、冲突请求拒绝、事务回滚无孤立任务/outbox。
- 冻结问题版本引用；同题多版本不能被当成不同独立任务。
- 并发领取只获得一个 attempt；预算争抢原子化；并发重复完成只记一次候选结果及费用。
- 有限已知失败重试；一个题失败不拖停其他题；失败/取消/超时保留在分母。
- 调用开始标记重复返回 false，不能作为重发许可；租约超时未知请求需要显式核对。
- 成功但费用未知仍持有预算，不用零代替；实际超支真实记账并阻止后续派发。
- worker 只能执行绑定 entry；不能读其他模型候选结果；resolver 无概率读取权限；直接写表和内部 helper 执行被拒绝。
- 真实预算锁等待后重新验证租约；新 attempt 完成后旧 attempt 的迟到结果不能覆盖。
- outbox 失联可重新投递，过期确认 token 无效，重复合法确认幂等。
- 许可证撤销在调用门口被拒绝；一个来源失效不让同实验其他健康题停止。

## 未执行 / 未交付

没有真实供应商 API 请求、供应商账单、token 统计、冻结证据包、TSA/OTS、正式预测外部锁定、现实结果裁决、公开计分、工作台或部署中的常驻调度服务。所有费用测试数字是合成数据，不是实际开销。

已提供有界恢复接口和单步 FIXTURE runner；调度器轮询、供应商退避、凭证配置、请求层输出上限和实际计费核对在 P0-03/后续部署验收。

本轮重启是实际服务器正常重启，不等于断电损坏恢复、备份恢复、生产滚动升级、大容量压力或长期无人值守验收。预算账户按实验隔离，未实现跨实验全局供应商额度池；数据库管理员仍能改 schema。

`SUCCEEDED` 仅表示唯一候选结果被收到，不表示事件预测正确，也不表示已具有公开成绩资格。登记基线/披露政策不等于实际完成基线评分/批次公开。
