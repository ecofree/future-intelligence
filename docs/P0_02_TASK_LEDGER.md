# P0-02：实验预登记与任务账本

状态以 `reports/P0_02_VALIDATION.md` 和对应提交的 CI 为准。本批是数据库/执行契约切片，不是完整预测产品。

## 范围与实体

新增 11 表：`experiments`、`experiment_entries`、`worker_bindings`、`expected_tasks`、`task_states`、`task_attempts`、`budget_accounts`、`budget_reservations`、`task_submissions`、`outbox_events`、`outbox_delivery`。
问题、授权、审核、冻结沿用 P0-01。0003 为新增迁移；0004 为调度前预算加固；旧迁移不改写。

实验一次性登记 **冻结问题版本 × 参赛配置** 的完整笛卡尔积，并产生完整任务清单和 outbox。不得运行时换成最新问题版本；同一逻辑问题的两个版本不能同时进入同一实验。
`external_key` 为登记幂等键：同一内容重试返回原实验；更换内容则报冲突。实验定义、参赛配置、预期任务、候选结果、outbox 事件只追加。

首版只支持 `suite=A`。`FIXTURE` 与 `LIVE` 分开，只有 FIXTURE 执行器已实现。允许登记 LIVE 身份不代表已具备真实供应商调用、证据包或评测资格。

## 固定协议

`fi.experiment.v1` 必须包含：

- `protocol`、`suite`、`run_kind`、`forecast_deadline`；
- `budget_microusd`、`attempt_cap_microusd`、`max_attempts`、`lease_seconds`；
- `failure_policy=REPORT_MISSING`、`baseline_policy=UNIFORM_CATEGORIES_V1`；
- `disclosure_policy=COMPLETE_BATCH_AFTER_RESOLUTION`。

1–500 个问题版本，1–10 个参赛配置；单任务最多 5 次尝试，租约最多 3600 秒；截止时间不能晚于任何问题的预测截止。预算和金额用整数 microUSD（1 USD = 1,000,000 microUSD），不是浮点值。
每个参赛配置冻结 entry_key、provider、model_id、prompt_sha256、parameters。凭证不属于配置；常见顶层凭证字段被拒绝，但这不是通用秘密检测器，不应把任何密钥放进参数或文本。

配置清单保留原始字节与 SHA-256；它不是外部时间证明。批次披露和基线策略在这里登记，真正计分、公开导出分别在后续关卡实现。

## 权限与调用

新增 NOLOGIN 分组角色：

| 角色 | 权限 |
|---|---|
| fi_operator | 登记实验、绑定 worker、恢复/取消任务、费用核对、查看任务与候选结果 |
| fi_worker | 领取自己绑定的参赛任务、续租、登记调用开始、提交回执 |
| fi_dispatcher | 领取和确认 outbox 事件，不读概率 |

为每个执行器创建独立 LOGIN，再授予分组角色。每个 login 在同一实验只绑定一个 entry；不能把共用数据库账号当成模型隔离。worker 无权读候选结果表或其他模型任务，resolver 仍无权查看概率。operator 是受信任控制面，不是预测模型账户；超级用户和迁移账号不属于隔离边界。

Python `TaskRepository` 每个操作使用短事务，事务提交完成后才返回；不在事务或数据库锁内执行网络请求。事务需要 READ COMMITTED。使用 `FI_WORKER_DATABASE_URL` 提供专用 worker 连接，不把数据库口令写入仓库。

```python
# Connection strings must come from secret-managed configuration.
operator.bind_worker(experiment_id, 'model-0', worker_login)
lease = worker.claim(experiment_id, 'FIXTURE')
if lease is not None:
    permitted = worker.start_call(lease)
    # Only True from a committed marker permits one dispatch. False, timeout or
    # an ambiguous commit is NOT permission to dispatch again.
```

`python -m future_intelligence.tasks.fixture_worker EXPERIMENT_ID` 只运行一次明确标识的模拟任务；它不联网、不调用 AI，不接受 LIVE 实验。测试中的费用全部为合成值，不能当实际支出。

## 状态、幂等与恢复

任务和尝试分开：任务唯一键为 `(experiment_id, question_version_id, entry_key)`；重试新增 attempt，不新增任务。每次领取递增 fence，完成/续租/调用开始均校验当前 attempt、fence、登录身份、租约和实验截止。

| 情况 | 处理 |
|---|---|
| 未开始调用就失去租约 | 释放预留，按次数和截止规则重新排队 |
| 已登记调用开始但结果未知 | UNCERTAIN，保留费用预留；禁止自动重试 |
| 失败且费用已知 | 记录费用，按预登记次数允许有限重试 |
| 返回候选概率但费用未知 | 结果可收到，费用继续 HELD_UNKNOWN，不记为零 |
| 已到截止或取消 | 任务仍在完整分母中；已发出的调用保留未知费用 |
| 同一完成请求重试 | 返回原回执，不重复记录结果/扣费 |
| 同一尝试提交不同内容 | 拒绝；不择优覆盖 |
| 旧 worker 迟到 | 拒绝写入；恢复后的新任务不会被旧结果覆盖 |

`recover(experiment)` 为有界恢复操作；`reconcile(attempt, actual, evidence, allow_retry)` 需要明确费用和证据。只有获得显式重试授权、尚未截止且次数未耗尽的 UNCERTAIN 任务可重试。重试可能产生新的供应商费用，不承诺网络请求或账单 exactly-once。

本批提供持久化控制接口和单步模拟执行器，**尚未部署持续运行的调度服务**。后续运行器负责定期调用 recover、处理数据库暂时不可用、限流和有界退避；数据库重新上线本身不会自动启动调度器。

## 预算与事务 outbox

统一锁顺序是预算账户 → 任务；同一实验的短写事务串行保护预算。预算预留、任务变更、事件发布在同一事务，回滚不得留下孤立任务或漏发事件。
若实际成本高于预留，账本诚实记录超支，而不是把数字截断到上限。超支会阻止新领取，以及已领取但未派发的调用；已经在外部执行的调用不能撤销。预算是准入控制，不是对外部价格/总账单的绝对保证；P0-03 还须验证定价快照与请求输出上限。

outbox 是 at-least-once：dispatcher 领取持有租约/fence，成功处理才 ack。失联后可再次投递；消费者必须按 event_id 幂等。它不是直接执行 AI 的外部消息队列；未接入 broker/生产消费者。

## 候选结果与公开成绩边界

`task_submissions` 的 `fi.task-receipt.v1` 只表示收到当前任务的唯一候选分布；不表示已获取外部时间戳或进入正式排行榜。二元概率顺序固定为 `[No, Yes]`，分箱题按已冻结升序分箱，均用整数 ppm，总和 1,000,000。
`SUCCEEDED` 不是“预测正确”，`TIMED_OUT` 也不是事件判为 No。全部任务必须继续参与完成率分母。缺失处理、成绩重算、正式预测锁定、外部时间资格、完整披露须在 P0-04/05/06 联合验收。

## 下一批接入约束

P0-03 必须把冻结证据包、可核验模型标识、请求/响应引用、token 与费用观测接入本账本；密钥缺失标 BLOCKED，不自动回退模拟。必须使用已提交的 call-start marker 之后才发请求，存疑提交先恢复/核对而非盲重发；网络 I/O 期间不持有数据库事务。
