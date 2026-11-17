# P0-04～P0-07 集成运行说明

版本：V2.0.1 工程预览。对应分支 `codex/p0-04-07-integrated-mvp`，PR #6。
状态以 `reports/P0_04_07_VALIDATION.md` 和对应提交的 CI 为准。

## 1. 能力与不变的验收边界

已实现完整批次捕获/加盐承诺、RFC3161 请求与独立校验、OTS 独立状态、问题版本级双人裁决、修订/争议和历史评分、事件簇统计、最小内部网页、精确期间采集及有界 worker 命令。

这些实现不能代替三家真实模型验收、外部 TSA 实测、真实未来事件结算或生产部署。测试中的事件、价格、模型输出、CA 和 TSA 均为合成或本地测试资产。所有这类分数只进入 `FIXTURE_DIAGNOSTIC`，不进入正式计分。P0-03 实际供应商验收继续跟踪 Issue #5。

## 2. 安装与角色

```bash
git clone --branch codex/p0-04-07-integrated-mvp https://github.com/ecofree/future-intelligence.git
cd future-intelligence
python -m venv .venv
. .venv/bin/activate
python -m pip install '.[postgres,web]'
python -m unittest discover -s tests -v
python -m future_intelligence.db.migrate
```

需要 Python 3.11+、OpenSSL 3、PostgreSQL 16 或 17。迁移命令读取 `FI_MIGRATION_DATABASE_URL`，必须使用专门的迁移账号。模型密钥和数据库口令由安全环境注入；应用不会自动读取 `.env`。

迁移创建 NOLOGIN 分组角色，不自动创建生产密码。数据库管理员为下表每个职责创建独立 LOGIN 并只授予对应分组角色，不用超级用户运行应用。

| 环境变量 | 分组角色 | 职责 |
|---|---|---|
| FI_OPERATOR_DATABASE_URL | fi_operator | 实验控制、批次捕获、请求时间证明、内部导出 |
| FI_VERIFIER_DATABASE_URL | fi_verifier | 依据独立配置的 CA/政策校验时间证明并登记结果 |
| FI_RESOLVER_DATABASE_URL | fi_resolver | 原始发布证据、结果提案，不读预测 |
| FI_REVIEWER_DATABASE_URL | fi_resolution_reviewer | 独立确认结果，不读预测，不是原问题审核角色 |
| FI_SCORER_DATABASE_URL | fi_scorekeeper | 计算评分与统计 |
| FI_DASHBOARD_DATABASE_URL | fi_dashboard | 只读内部看板 |
| FI_WORKER_DATABASE_URL | fi_worker | 一个实验中绑定一个参赛配置的执行身份 |
| FI_SOURCE_ADMIN_DATABASE_URL | fi_source_admin | 已登记授权范围内的数据采集 |

问题作者/问题审核者仍按 P0-01 的 fi_author / fi_reviewer 分开。结果提案人与确认人必须是不同登录身份；结果人员不得同时具备模型、控制、评分或预测看板权限。验证时间的登录账号不能同时是 operator/worker/resolver。数据库管理员、代码部署者仍处于受信任边界，不能把普通应用权限隔离宣称为抵抗数据库超级用户的完整防篡改系统。

## 3. 一次实验的顺序

### A. 冻结问题、参赛配置和证据

沿用 `docs/P0_01_DATABASE.md`、`P0_02_TASK_LEDGER.md`、`P0_03_PROVIDERS.md` 的接口完成问题审核/冻结、完整题集×配置登记、独立 worker 绑定、实验级证据封存。不要用示例模型名或假价目表执行实际调用。

每个实验首版只支持一个初始预测批次，不支持在同一批次追加更新预测。更新判断应另建预登记实验；不同赛制、问题版本、预测时点不得混排。

### B. 在首次 attempt 之前捕获完整预登记清单

预先创建本地 `analysis.json`，例如：

```json
{
  "protocol": "fi.analysis.v1",
  "seed": 73,
  "resamples": 2000,
  "alpha_ppm": 50000,
  "min_clusters": 20,
  "comparisons": [["openai-entry", "anthropic-entry"]],
  "stopping_rule": "FIXED_REGISTERED_BATCH"
}
```

参赛键必须真实存在；比较对不能重复或反向重复。20 个事件簇是暂定标记阈值，不是预测能力证明的通用样本量。

`trust-profile.json` 必须由操作者和独立验证者事先核实，包含：`ca_sha256`、`policy_oids`、`max_accuracy_us`、`missing_accuracy_us`、`fixture`。生产配置的 CA 指纹应从独立可信渠道确认；不能信任审计包自己带来的根证书。TSA 未声明精度时，默认拒绝；只有已核实政策支持时才能预先设定缺失精度处理上界。测试 CA 必须 `fixture=true`。

```bash
python -m future_intelligence.delivery capture EXPERIMENT_UUID REGISTRATION \
  --plan analysis.json --profile trust-profile.json
```

返回 `batch_id`、承诺摘要，状态仍为 `NOT_REQUESTED`。哈希不是时间证明。

### C. 请求并独立校验预登记时间

```bash
python -m future_intelligence.delivery anchor REGISTRATION_BATCH_UUID \
  --url https://APPROVED_TSA_HOST/APPROVED_PATH --allowed-host APPROVED_TSA_HOST --allow-network
python -m future_intelligence.delivery verify-anchor ANCHOR_REQUEST_UUID \
  --ca independently-approved-ca.pem --profile trust-profile.json
```

地址中的大写内容是占位符，不能直接执行。操作者负责请求/接收，验证命令使用 `FI_VERIFIER_DATABASE_URL`。请求正文和 nonce 在联网前持久化；返回错误不会升级为已验证。验证会检查 CA 指纹、链、签名、消息摘要、nonce、TSA policy、时间精度和截止时间。资格使用 `gen_time + accuracy` 的上界，不忽略误差。

当前未实现 CRL/OCSP，报告明确标记 `revocation_status=NOT_CHECKED`；也未提供多年归档后的历史证书有效性重建。不能宣传为法律认证或完整长期电子签章服务。

### D. 运行真实模型任务

需要先完成 Issue #5 的供应商、型号、账单和错误路径实测。以下命令每个独立 worker 只处理自己的配置：

```bash
python -m future_intelligence.delivery worker EXPERIMENT_UUID --cycles 10 --interval 10 --allow-network
```

该新命令要求生产信任配置下的预登记时间已验证。旧 P0-03 单步接口作为低层接入仍存在，不能把新命令的门控说成旧入口也已全部强制门控；绕过运行顺序的记录不会取得正式评分所需的预登记时间资格。

网络请求不持有数据库事务。缺失密钥、未知提交或未知供应商结果会阻塞/停止，不自动补模拟输出或盲重发。恢复控制单独使用 operator：

```bash
python -m future_intelligence.delivery recover EXPERIMENT_UUID
```

这是有界 CLI，不是已经部署的 24 小时服务。部署者应配置进程监督、预算告警、数据库备份、轮询与停止策略；不得用恢复操作清除未知费用。

### E. 在预测截止前封存全部任务

```bash
python -m future_intelligence.delivery capture EXPERIMENT_UUID FORECAST
```

无论成功、失败、超时、取消或未完成，所有预登记任务都在批次里。捕获后禁止新增 attempt、请求和候选预测；迟到观测可以留账，不能改写已承诺的预测。

再对该 FORECAST 批次执行 C 的外部请求和独立校验。只有预登记证明早于首次 attempt，预测批次证明上界不晚于截止，并且是 LIVE/非测试信任，才具备代码中的时间资格。实际模型来源与实际外部信任仍属于单独验收责任。

### F. 现实结果、独立确认、版本化评分

采集精确期间的 BLS 数据候选需要先有登记许可：

```bash
python -m future_intelligence.delivery collect-bls PERMISSION_UUID CUUR0000SA0 2026 M10 --allow-network
```

这里只是接口格式示例，不是正式预测题或授权声明。返回时序数据不等于原始首次发布版本；采集函数永远标记 `publication_time_verified=false`、`first_release_verified=false`、`requires_blind_review=true`。缺少目标月份不是事件 FALSE。当前并未实现完整自动新闻采集、官方日历发现或可靠的自动已知答案检测。

结果人员必须核对指定机构原始发布、期别、时间、单位、修订政策及来源优先级，再保存证据：

```bash
python -m future_intelligence.delivery add-evidence outcome-evidence.json
python -m future_intelligence.delivery resolve resolution-proposal.json
python -m future_intelligence.delivery confirm PROPOSAL_UUID --note '独立核对原始来源、期别和已冻结判定规则'
python -m future_intelligence.delivery score EXPERIMENT_UUID
```

`outcome-evidence.json` 字段：`version`、`permission`、`release_key`、`published_at`、`revision`、`value`、`sha256`、`source_reference`。`resolution-proposal.json` 字段：`version`、`kind`、`evidence_id`、`reason`、`parent`、`fallback_reason`。`kind` 为 RESOLVED/VOID/DISPUTED；后二者不填 evidence_id。parent 是上一提案 ID，用于并发冲突检测。

FIRST_RELEASE 选择已核验并登记的最早发布；LATEST_AT_RESOLUTION_DEADLINE 选截止前最后发布，且不可提前结算。数据库不能自行证明“已经收齐所有官方版本”，完整性由盲化审核把关。

补录版本改变应选证据时，当前结果成为 `STALE_EVIDENCE`，新评分暂停，需新提案和独立确认。旧确认、旧评分始终保留。所有模型共享同一 question_version 结果。

`REPORT_MISSING` 保留完成率分母，但不暗中填 50% 概率。只对已提交且已结算题给出的均分是条件均分，必须同时展示覆盖率；基线全已结算题均分与条件均分不是同一分母。二元 Log Loss 对错误的绝对确定保留 Infinity，不静默裁剪。

### G. 统计与独立导出

```bash
python -m future_intelligence.delivery analyze SCORE_RUN_UUID
python -m future_intelligence.delivery analyze SCORE_RUN_UUID --diagnostic
python -m future_intelligence.delivery export EXPERIMENT_UUID private-audit.json
python -m future_intelligence.delivery verify-export private-audit.json \
  --ca independently-approved-ca.pem --profile trust-profile.json
```

默认统计只使用 TIMED_LIVE；diagnostic 必须显式指定。成对比较按同一事件簇整体重采样，固定随机种子和预登记停止规则；Holm 校正作用于比较对×实际分层的整族。符号翻转检验依赖簇独立与零假设下差值对称，结果仅作探索性比较。不显著不等于相等，更多题不等于更多独立事件。样本量函数仅供规划，不能用于未预登记的边算边停。

导出包含原始清单、完整任务、精确记录字节、随机 nonce、承诺、时间请求与回包、结果记录。独立程序从原始回包重验签名，不相信存储的“已验证”标签；同时检查问题定义哈希和全量题目×模型组合，再重算分数。结果真伪和供应商身份不是由哈希自动证明。

导出是内部资料，不自动上传、不自动发布；文件以 0600 权限排他创建。公开前另审查数据许可及隐私。不要把私有导出、证书私钥、账单或研究原文提交到公开仓库。

## 4. 内部工作台

配置 `_TOKEN` 与 `_DATABASE_URL` 配对，TOKEN 至少32字符且使用密码学随机生成，四角色各不相同。启动：

```bash
python -m future_intelligence.delivery.workbench
```

默认 `127.0.0.1:8080`。页面提供实验/预算、批次/时间、独立结果、评分档案；变更操作通过已鉴权 API/CLI。不是完整研究报告编辑器、复杂图谱或公开排行榜。

这是 FastAPI 同源最小 HTML 实现，暂不引入原计划的独立 Next.js 前端；见 ADR-016。不存在跨站自动登录、公共注册或免费暴露付费调用。裁决和复核令牌无法读预测与评分。生产远程访问需要额外的 TLS、身份/会话管理和边界代理评审，不能只把监听地址改为 0.0.0.0。

## 5. OTS 与故障恢复

`delivery.ots` 是可选官方 OTS CLI 桥接；需单独安装审核版本并配置 Bitcoin 验证节点。本批未实际进行外部日历或节点验收。仅持有 .ots 文件保持 PENDING_UNVERIFIED；Bitcoin 验证通过也不替代精确截止所需 TSA 证明。数据库不接受把调用者传入的区块时间直接登记为已确认。

TSA 网络异常：保留失败请求，另建请求可重试；证据和预测内容不改。数据库提交不确定时先查询持久化记录，不自动再派发模型。来源被撤销：停止新的相关处理，不能伪造许可延期。结果争议：新提案暂停计分，不改历史分数。工作台错误只返回受控错误码，不回显 DSN、模型响应正文或密钥。
