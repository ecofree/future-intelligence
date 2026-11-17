# 受控运行与本机部署

本批继承 V2.0.1 与 P0-04～07，不改变研究产品方向。新增代码位于 `codex/operational-readiness`、PR #8。这里是可重复部署与受控实验的工程入口，不是无人值守生产验收声明。

## 1. 先区分四种记录

| 标记 | 含义 | 能否作为真实正式预测 |
|---|---|---|
| FIXTURE | 合成测试；即便真的用了 PostgreSQL/OpenSSL，也不是现实预测 | 否 |
| LIVE + DIAGNOSTIC | 供应商接口资格联调；输入也可使用测试材料，测试还可能注入合成响应 | 不得封存为正式批次；LIVE 标记本身不证明外部调用发生 |
| LIVE + FORMAL | 按预登记规范执行的候选正式实验 | 仍须独立合格时间证明、真实来源/结果等证据 |
| LIVE + UNCONFIGURED | 旧记录或尚未配置模式 | 新领取与发送被阻断 |

`FORMAL` 是运行政策，不是“已通过真实验收”的徽章。工作台把 `run_kind`、`suite`、`operation_mode` 投影为独立列，避免藏在不显示的嵌套 spec 中。

## 2. 新部署：默认不运行模型

以下是 Linux、Docker Engine/Compose v2、Python 3 的路径；实际 CI 使用 Ubuntu 24.04 和 PostgreSQL 17。其他宿主平台须分别验证文件权限与挂载，不假定同样通过。

```bash
git clone --branch codex/operational-readiness https://github.com/ecofree/future-intelligence.git
cd future-intelligence
export FI_UID="$(id -u)" FI_GID="$(id -g)"
python3 deploy/configure.py
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml up --build -d web
curl --fail http://127.0.0.1:8080/readyz
```

浏览器在同一宿主打开 `http://127.0.0.1:8080/`。默认数据为空，不自动灌入演示成绩。访问令牌由本机 `deploy/.local-secrets/dashboard_token` 提供，使用本机安全工具读取；不要贴进聊天、Issue、日志或提交。刷新后不从 localStorage/sessionStorage 恢复令牌。

配置器只创建新目录；目录已存在就失败，绝不重置数据库密码。`.local-secrets` 为 0700，文件为 0600；API 密钥文件初始为空。`setup` 一次性拥有迁移与角色配置权限；web 只有只读 dashboard 身份和令牌。12 个登录身份被分别建立并验证，不把超级用户 DSN 给 web 或模型 worker。

web 通过专用边缘网络发布 **127.0.0.1** 端口；PostgreSQL 仍在私有网络且不发布端口。边缘网络不是完备出站防火墙，不据此宣称 web 完全断网。只读容器、删除 Linux capabilities、禁止提升权限、内存/PID 上限均为纵深措施，不替代主机安全。公网 TLS/SSO/限速、镜像摘要和依赖供应链冻结仍属于发布前工作。

重复执行 setup 用于幂等检查，不会修改既有密码。普通停止用 `docker compose -f deploy/compose.yaml stop`。**真实环境不要使用 `down -v`**；CI 中该命令仅删除本次临时测试栈。

## 3. 升级现有数据库

先停止新的领取/发送，备份并核对正在执行的调用及未知费用；部署同一版本的程序后执行正常迁移入口。迁移 0012/0013 为前向追加，不改写 0001～0011，也不覆盖历史预测。

已有 LIVE 实验若已经开始调用或封存批次，不能事后补成新 FORMAL/DIAGNOSTIC 实验。其历史观测、费用与结果应继续保留/核对；需要新调用时登记新实验。旧实验不能通过修改配置得到追溯性正式资格。没有任何真实旧数据的情况下，也不要用清库代替升级测试。

## 4. 正式入场顺序

1. 创建、独立审核并冻结问题版本；登记完整问题×模型集合、预算和截止时间。
2. operator 在首次尝试和批次之前配置 FORMAL 或 DIAGNOSTIC。模式及窗口随后不可改。
3. FORMAL 为每题保存 CALENDAR 与 TARGET_STATUS 原始材料快照、来源、用途许可、摘要和检查说明；服务器记录提交时间并计算 SHA-256。密钥不作为材料上传。
4. 与提交人、operator、worker 不同的 reviewer 检查目标身份、发布时间、未知答案状态及事件簇；确认明确的 check IDs。
5. 固定预测证据包，捕获并封存 REGISTRATION。入场政策、确认人、检查元数据和文档哈希进入同一承诺。申请并独立验证真实外部时间戳；时间精度上界必须已过去。
6. 绑定独立供应商 worker 登录；只有预算、许可、入场新鲜度、登记证明、租约和剩余时间均符合要求，数据库才准许领取/准备发送。
7. 到预留封存窗口停止新的派发；operator 将完整任务清单捕获、封存为 FORECAST，再单独申请/验证外部证明。随后按原流程采集结果、独立裁决、计分和导出。

有些步骤仍需显式命令/独立人员：本批没有把 TSA 凭证、签名验证身份与预测 worker 合并，也没有部署全自动采集、TSA 调度或自动结果审判。

使用现有 repository API 创建问题/实验；本批命令可在已安全配置各角色 DSN 的运行环境执行：

```bash
python -m future_intelligence.runtime configure EXPERIMENT_UUID --mode FORMAL \
  --seal-margin 120 --check-max-age 3600 --note '正式登记前固定执行与封存窗口'
python -m future_intelligence.runtime check EXPERIMENT_UUID VERSION_UUID PERMISSION_UUID \
  --kind CALENDAR --finding REVIEWED --reference https://official.example/calendar \
  --document /private/calendar-snapshot --note '已核查指定目标、参考期间及实际发布时间'
python -m future_intelligence.runtime check EXPERIMENT_UUID VERSION_UUID PERMISSION_UUID \
  --kind TARGET_STATUS --finding NOT_FOUND --reference https://official.example/release \
  --document /private/target-snapshot --note '本快照未发现目标结果，另有独立复核'
python -m future_intelligence.runtime review EXPERIMENT_UUID VERSION_UUID \
  --checks CALENDAR_CHECK_UUID TARGET_CHECK_UUID --cluster FROZEN_EVENT_CLUSTER \
  --note '独立核对来源、发布时间、目标身份和事件相关性'
python -m future_intelligence.runtime status EXPERIMENT_UUID
```

示例中的域名/UUID 是占位值，不能直接作为正式证据。各命令分别读取 `FI_OPERATOR_DATABASE_URL`、`FI_SOURCE_ADMIN_DATABASE_URL`、`FI_ADMISSION_REVIEWER_DATABASE_URL` 或相应 `_FILE`，不能将所有操作伪装为同一身份。Compose 数据库不向宿主开放；运行角色 CLI 时应使用同一私有容器网络或自行配置的安全数据库连接，不要为了方便公开 5432。

## 5. “已知答案”和事件分组的边界

`runtime.release.draft_bls_question()` 读取实际目标 JSON 与日历文件，不再靠调用者的 `known_target` 布尔值证明未知。目标已存在、API 错误、不同序列、非有限数字或重复字段会拒绝；没有找到只标记 `NOT_FOUND_IN_THIS_SNAPSHOT_ONLY`，仍是待审核候选。旧 `delivery.operations.release_question()` 保留为历史草稿兼容入口，不是正式入场证据。

新版 BLS 事件簇使用“发布族＋规范化 UTC 发布时间”，同一 CPI 发布中的不同序列可归同簇，而不是每条序列各算独立事件。其他领域仍需独立评审相关性；本批不是自动因果聚类。

检查是人工/外部采集后提交的记录，不自动证明文档真实性或全网不存在答案。KNOWN/UNCERTAIN 后再追加 NOT_FOUND 不会清除风险。原确认检查过期后，剩余正式任务会被拒绝/留档，不悄悄刷新时间或更换已承诺材料。需要不同资料窗口时新建实验。

## 6. 运行、停止与费用

```bash
python -m future_intelligence.runtime operator EXPERIMENT_UUID --cycles 100 --interval 10
python -m future_intelligence.runtime worker EXPERIMENT_UUID --cycles 100 --interval 10 --allow-network
python -m future_intelligence.runtime pause EXPERIMENT_UUID --note '人工确认异常，暂停后续领取和派发'
python -m future_intelligence.runtime resume EXPERIMENT_UUID --note '已核查停止原因，允许规则内继续执行'
```

默认一次执行；循环有界，异常退出不由 Compose 无条件重启。程序接收 SIGINT/SIGTERM 停止后续迭代；不承诺强行取消供应商已经收到的请求，也不承诺外部只计费一次。未知调用保留账本和费用，恢复不自动重发。

Compose 中 operator 和三个 worker 为显式 profile；默认只启用 db/setup/web。开始 worker 前必须已有合法实验、角色绑定、预算以及当前凭证。每个 worker 只挂自身密钥和 DSN，不共享其他供应商密钥。启用模型 profile 会产生联网和可能费用；不要用示例未配置的 UUID 运行。

结构化日志会报告预算预警（已花费＋已预留≥80%）、超支、未知费用核对及门控原因。**只是日志事件，不是已接通短信/邮件/外部告警接收器**。费用应由 operator 依据真实证据用原 `reconcile_attempt` 核对；估算不能冒充账单，未知不能自动归零。

封存命令输出 `REQUIRES_SEPARATE_ANCHOR_AND_VERIFIER` 不是第三方证明成功。若在截止前未取得合格证明，批次保持无正式时间资格，不能倒签。

## 7. 备份与恢复演练

```bash
bash deploy/backup.sh /private/new-backup-directory
bash deploy/restore-drill.sh /private/new-backup-directory
# 仅所有写入已静止的测试环境：
FI_COMPARE_QUIESCENT=1 bash deploy/restore-drill.sh /private/new-backup-directory
```

备份路径必须不存在；完整完成才生成 COMPLETE。角色备份不包含密码哈希，凭证管理须另外安全保留。备份包含可能敏感的研究数据，不上传 Actions artifact 或公开仓库。摘要能发现损坏，不证明备份提供者可信。

恢复脚本只创建并随后删除随机 `fi_restore_*` 新数据库，不覆盖原库。现有演练为同一数据库集群内还原，依赖已经存在的角色；**不是跨主机灾备、凭证恢复或 RPO/RTO 证明**。只还原可信归档，因为数据库备份可能包含可执行 SQL。

## 8. 本批不关闭的门槛

三家真实 API/费用（#5），生产 TSA/CA/撤销/长期验证，真实未知事件及现实结算，用户环境持续运行、外部告警与真实网络故障（#7），完整编辑工作台、公网身份安全与公开成绩平台，仍须单独完成。CI 模型响应与未来事件是合成的；测试 CA 只验证代码。

主分支与历史 PR 的合并发布尚未执行，不能把本分支交付说成主分支已发布。验证结果见 `reports/OPERATIONAL_READINESS_VALIDATION.md`；历史报告保留，不把新状态覆盖到旧提交。
