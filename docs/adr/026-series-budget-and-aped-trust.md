# ADR-026 替代站点的系列费用账本和 APED 时间证明

2026-11-23。适用于接续历史授权的官方 Terra 预测系列。

## 接续历史费用

迁移 0021 为一个数据库建立一份系列账本。部署者用 `deploy/import_series_budget.py`、交接清单和独立保存的 SHA-256 开账。导入器逐一核对附件长度和摘要，再核对未决费用、总额和授权次数。相同开账可以重放，冲突开账会被拒绝。历史未决费用作为开账负债保留，原数据库里的实验、任务和通话记录仍归原库。

替代库起初封闭。过渡期间的追加占用由 `fi.series_cutover.additional_held_microusd` 记录。账本管理员须从负责供应商项目的账户核实旧派发凭据已撤销或旧执行端已停写，并把从交接快照到停写时刻的账单、在途和延迟入账风险核对完整，保留两份原始材料，再记录各自摘要和覆盖时间。数据库只核验登记结构，供应商侧事实需要外部凭证。历史不确定费用继续占用额度，无法界定的过渡期费用使账本保持封闭。

切换后，管理员只给已获准的精确任务登记 `fi.series_permits`。首题复核完成后才给第二题许可。领取时全站锁定同一条系列记录，原生费用预留、任务和系列记录在同一事务提交。预测准备发送时再次检查站点、许可和预留。每题只能进入一次。已结算记录按供应商实际金额计，未决记录按全额计。并发实验共享次数和费用上限。数据库标识、系统标识、数据目录和端口共同约束本机副本。跨主机排他仍依赖已核实的旧执行端停写或供应商凭据切换。

部署后以操作员凭据运行 `python -m future_intelligence.runtime series-status`。余额采用微美元，`cutover_recorded=false` 时可派发额度为零。初始配置和外部切换凭据只对迁移管理员开放。

## 冻结生产时间源

迁移 0022 增加 APED 专用验证器 `openssl-ts+fi-aped-crl-v1`，沿用现有 v2 证据封装。迁移 0023 将独立配置的七键信任策略固定在站点，并约束正式登记、派发和预测封存。开账命令的 `--trust-profile deploy/aped-trust-profile.json --trust-review <核验记录>` 用于保存审核依据的摘要；私有原件留在运行环境。

可信服务由欧盟签名清单指向的希腊 EETT 清单确定。签名者必须由清单中的 APED Qualified Timestamping Issuing CA 直接签发。正式策略只接受指定 OID 和签名中明确给出的最多一秒误差。验证器以系统当前时间复验完整证书链、两层撤销签名、证书序列号、SHA-256 印记和 nonce。每个请求须使用当时有效的两份 CRL。签发 CA 的 CRL 最长 24 小时，根 CA 的 CRL 最长 365 天，同时受各自 nextUpdate、证书有效期和已审核清单的有效期限制。本版清单复核截止为 2027-02-25 07:00 UTC。 [APED CP/CPS §4.9.6](https://pki.aped.gov.gr/repository/downloads/CP-CPS_APED_en_v1.1.pdf) · [欧盟公报信任入口](https://eur-lex.europa.eu/eli/C/2026/1944/oj/eng/pdf) · [希腊可信清单](https://www.eett.gr/tsl/tl-el.xml)

时间服务地址为 `https://timestamp.aped.gov.gr/qtss`。复验时向 `delivery verify-anchor` 提供冻结的配置、固定的根 PEM 和两份官方 CRL 组成的 PEM bundle。历史已验证窗口保持原记录。到期后须取得并验证最新 CRL。登记与预测各自使用本批数据库封存的摘要、原始 TSQ 和对应 TSR。

公开 QA 工作流只处理公开 Git 提交或明确公开的合成测试查询。候选 worker 在普通 DNS 和 TLS 检查通过后，对 APED 发一次请求，将交换材料加密给接收方。测试批次为 FIXTURE，生产 worker 的出网和生产批次的时间证明分别验收。
