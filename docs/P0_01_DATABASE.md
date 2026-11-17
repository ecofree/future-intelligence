# P0-01：PostgreSQL 问题账本

本批是持久化与问题版本切片，不是预测 MVP。代码、CI 与验收状态见 `reports/P0_01_VALIDATION.md`。

## 安装和迁移

使用专用空数据库。PostgreSQL 16/17 为本批测试目标，Python 3.11+。

```bash
python -m pip install '.[postgres]'
# 由环境/密钥管理器提供 FI_MIGRATION_DATABASE_URL，不能提交实际连接串。
python -m future_intelligence.db.migrate
python -m unittest discover -s tests -v
# 独立测试数据库 + 能创建测试角色的管理员，不能指向生产库：
python -m unittest discover -s tests/integration -v
```

迁移账号需要创建 schema 和 NOLOGIN group roles 的权限；应用不能使用此账号。
迁移按版本和 SHA-256 检查，整个批次一个事务，事务级 advisory lock 防并发迁移。
已应用迁移的内容不同则拒绝；增加新迁移而非改写旧迁移。不提供自动降级或清库命令。
SQL 作为 Python 包资源交付，CI 在仓库目录外验证安装后可迁移。

## 五个组角色

| 角色 | 能做什么 | 不能做什么 |
|---|---|---|
| fi_source_admin | 登记来源、追加许可版本、撤销许可 | 不能代表模型发预测或审核问题 |
| fi_author | 创建问题版本、冻结已审版本、关闭已过提交截止的版本 | 不能直接写表、登记自己的来源许可、审核自己 |
| fi_reviewer | 审核问题定义 | 不能改定义、冻结、与作者使用同一登录身份 |
| fi_reader | 读取问题/许可与审计记录 | 不能写 |
| fi_resolver | 读取问题定义与来源 | 无写权限；未来预测表不自动获得权限 |

管理员为实际使用者创建独立 LOGIN，再 GRANT 相应组角色。审核者与作者应使用不同登录。
组角色本身不可登录，不存储任何默认生产密码。`session_user` 为真实数据库登录；不能用提交参数伪造 actor。
本批未交付 HTTP 认证/RBAC，服务部署时仍需建立用户到数据库权限的可信映射。

## 定义、时间与判定口径

`fi.create_version(key, spec_jsonb, permission_ids_uuid_array, expected_parent)` 每次追加一个不可改定义。
已有 key 必须提交当前 parent，防止并发覆盖；重试冲突须重新读取，不能无条件重试覆盖。
所有版本（含草稿）采用追加式定义；变更等于新建版本，不修改已审内容。
支持 MACRO/POLICY、FAST/STRATEGIC、binary/binned_numeric；字符串时间必须有明确 UTC offset。
存储 `timestamptz` 时间点与原始 IANA 时区；数据库显示受 session timezone 影响，生产连接应设 UTC。
强制 `forecast_deadline < outcome_not_before <= resolution_deadline`，在锁等待后再次按数据库时钟判断截止。
时间顺序校验不能证明模型没有提前知道结果；自动检漏仍属于 P0-03/07。

`data_revision_policy` 明确 FIRST_RELEASE 或 LATEST_AT_RESOLUTION_DEADLINE。
分箱边界为有限、严格递增的十进制字符串，分箱采用 `[-inf,e0), [e0,e1), ..., [last,+inf)`。
数据修订政策、分箱、规则、事件簇和许可绑定随版本固定。
**实际统计数据获取、修订值挑选与问题级结果实体在 P0-05 实现；本批只保留不含糊的口径。**
CLOSED 只表示提交窗口关闭，不表示结果为 FALSE、VOID 或任何结算结果。

## 来源许可与防绕过

许可用途明确区分内部研究、模型处理、模型评估、存储、公开展示。内部冻结需前四项；
不因为来源“官方/公开”就自动授权，许可事实由来源审核者登记并附证据。
每题绑定 1–10 个来源许可版本；顺序为预登记的主源/备选优先级。
新许可版本使旧版本失去本次准入资格；撤销、未生效、过期、用途缺失均拒绝。
审题和冻结分别检查；许可更改与检查共用来源行锁。
迁移 0002 要求许可准入使用 READ COMMITTED，拒绝调用方的旧快照事务。
先取得全部来源锁，再在同一时点校验全部许可，避免等待后续来源锁时前一许可已经到期。
许可冻结后失效不会篡改历史问题。**未来每次抓取/模型调用/公开导出仍必须重新检查许可，尚未交付这些环节。**
不自动判断合同是否合法；许可证据是人工决策的审计记录。

应用角色没有表级 DML 权限。写函数使用固定安全 search_path 并收回 PUBLIC EXECUTE；
辅助函数不开放。定义、许可、绑定、审计表有 UPDATE/DELETE/TRUNCATE 拒绝触发器。
数据库 owner/superuser 仍可更改 schema/触发器；这不是抵御管理员的密码学保证。

## 内容快照不是可信时间证明

创建版本保存 `snapshot_text` 的精确 UTF-8 字节，SHA-256 对这些字节计算。
协议 `fi.question-snapshot.v1` 包含定义、问题身份/版本和许可 ID/优先级。
消费者验证存储/导出的原始 `snapshot_text`，不要重排 JSON 再计算；不是 JCS 编码。
冻结只新增状态时间与审计事件，不改变内容。重复冻结幂等，只有一个 QUESTION_FROZEN 事件。
数据库 frozen_at 和内容哈希不代表 RFC3161/OTS 证明；外部时间状态仍为 NOT_REQUESTED。

## 后续接口依赖

P0-02 的预登记任务必须外键绑定具体 `question_versions.id`，不能只绑定 external_key 或“最新版”。
P0-05 的结果必须绑定该同一问题版本，所有模型读取同一版本结果，不能逐模型裁决。
后续预测表不得给 fi_resolver 通配读权限；审计字段也不得向该角色泄漏预测值。

## 设计参考

- PostgreSQL CREATE FUNCTION：SECURITY DEFINER、固定 search_path、收回 PUBLIC EXECUTE。
  https://www.postgresql.org/docs/16/sql-createfunction.html
- PostgreSQL 并发控制与行锁：
  https://www.postgresql.org/docs/16/explicit-locking.html
- Psycopg 连接上下文提交/回滚行为：
  https://www.psycopg.org/psycopg3/docs/basic/transactions.html

本文件描述本项目的工程决策，不把参考资料当作本项目验收证据。
