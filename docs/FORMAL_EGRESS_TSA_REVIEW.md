# Terra 诊断之后：第三关的具体剩余工作

更新：2026-11-19。本次诊断 `e1e67317-4416-445b-b5c5-2f0197bf251d` 已有两次成功保存记录；[交接审计](../reports/TERRA_DIAGNOSTIC_AUDIT.md) 已复算请求与计数。本批补严格离线 CRL 验证与前向资格合同，见 [ADR-025](adr/025-offline-crl-admission.md)。模型请求不变，不新增收费诊断；正常出网、生产信任独立审核和原库只读补证仍未通过。

## 1. 一次只读补证

在原环境使用已有授权审计连接，分别对 `fi_pr12_terra_diag` 和规范参考库运行 [只读 SQL](../scripts/audit_terra_readonly.sql)。该文件只查询，使用 REPEATABLE READ READ ONLY，并以 ROLLBACK 结束。不执行项目业务函数，不修改权限、历史函数、实验或预留；权限不足时停止，不临时扩大账号权限。用原有 PGSERVICE/凭据注入，不把带密码的 DSN 写到命令行或输出。

```sh
psql -X --set=ON_ERROR_STOP=1 --file=scripts/audit_terra_readonly.sql --output=terra-readonly-audit.txt
```

这是待在原环境执行的命令，不代表此会话已经连接原库。输出包含冻结研究原文，只留在私有审核档案，脱敏后交接，不提交公开仓库。两个数据库各用独立输出文件；比较时记录代码提交和目标库身份。

验收三个具体结果：实际库与 9f0a994 的函数/视图/触发器启用状态/权限一致；前置失败实验 7f6a447a 确实没有领取、派发和预留（若非零先保留并核算）；原 snapshot_text、evidence seal、packet 和 v1 observation 的哈希链完整。内部 FK 触发器生成名、随机登录名和 owner 名可不同，应比较安全属性、实际授权和对应约束，不误判 OID 差异为业务漂移。

旧 v1 没有保存的响应参数和 HTTP req_ ID 无法通过查询恢复。任何补证不得用请求值填成“服务端已返回”。0019 的新协议只在未来使用；先在独立副本验证，源实验库无需为此升级。

## 2. 在真正运行环境恢复正常解析

由掌握原宿主/容器配置的运行方修正实际 DNS/TUN 的 fake-IP 模式，或使用配置正常且已审核的执行环境。应同时在宿主、实际 worker 的网络命名空间，以没有 sitecustomize/临时 DoH 注入的进程验证实际会访问的域名：api.openai.com、api.bls.gov、freetsa.org，以及证据源 www.bls.gov、审核批准的证书/撤销端点。不得复制这份 DoH 钩子作为生产修复。

保留普通解析返回的完整 A/AAAA、实际公网连接对端、TLS SNI 与主机名检查、证书链及 CA 文件/系统包指纹、获准代理设置的脱敏摘要。不能只保留过滤后的公网子集；任何私有/保留地址仍按原 SSRF 规则阻断。仅记录代理是否存在、类型和经审核主机，不输出凭据或全量环境变量。

请求必须通过现有 PublicHTTPS/官方直连约束。外部快照逐跳限定获准 HTTPS 主机、端口、地址和 TLS；保存独立 hop 对象，避免自引用日志。不得放行 198.18/15、固定绕过 IP、关闭证书验证、接受任意重定向或自动换供应商。连通探测不触发生成；一次官方 HTTP 200 或 TSA 302 不替代这套证据。

此会话不能访问原宿主，也未更改它的网络。2026-11-19 本会话的研究下载通过继承环境代理的 HTTPS 取得公开证书及 CRL；同日普通 `socket.getaddrinfo` 对 api.openai.com、api.bls.gov、freetsa.org、www.bls.gov 均返回 EAI_AGAIN(-3)。下载可达不表示直连 PublicHTTPS 已通过，更不能替代原宿主或实际 worker 网络命名空间的复验；原交接中 fake-IP 阻断仍未解除。

## 3. TSA 的独立审核必须解决的事实

2026-11-18 查询以下官方来源：

| 官方页面 | 公布的 TSA 证书 SHA256 |
|---|---|
| [英文首页](https://www.freetsa.org/index_en.php)、[CPS](https://www.freetsa.org/freetsa_cps.html) | `8bfb0305bb64e2571ca507552ef3245cb1c2fee8728e0ff8689225081ea13467` |
| [德文首页](https://www.freetsa.org/index_de.php) | `aaeb619d11a63f75d06d104a926b85c76f8276bd9eae616e82d0db5cc5ff149d` |

2026-11-19 实取 [根证书](https://freetsa.org/files/cacert.pem)、[TSA 证书](https://freetsa.org/files/tsa.crt)、[CRL](https://www.freetsa.org/crl/root_ca.crl) 和英文页面链接的[已过期旧 TSA 证书](https://www.freetsa.org/files/tsa.crt_expired)。研究下载继承环境代理，均记录 HTTP 200、TLS 验证结果 0，未使用 `-k`、固定解析 IP 或临时 DoH 注入。保留原文件字节，分别计算文件摘要与 DER 证书指纹：

| 材料 | 原文件 SHA256 | DER 证书 SHA256 |
|---|---|---|
| CA PEM | `2151b61137ffa86bf664691ba67e7da0b19f98c758e3d228d5d8ebf27e044438` | `a6379e7cecc05faa3cbf076013d745e327bbbaa38c0b9af22469d4701d18aabc` |
| TSA 叶证书 | `8bfb0305bb64e2571ca507552ef3245cb1c2fee8728e0ff8689225081ea13467` | `32e841a95cc1164101ffde41298ef2fc75c1c4372ef095e88a6bbd47dfb191fc` |
| CA CRL | `13d85acc0dd6a7ace445822b09dcba0de4236e84d60494dcbe6d7a6c97031935` | 不适用 |
| 已过期旧 TSA 证书 | `aaeb619d11a63f75d06d104a926b85c76f8276bd9eae616e82d0db5cc5ff149d` | `4694be23c3a53004444b1705bfe5a7f50a6d2a1638194f23c0f389b68bd78a75` |

叶证书有效期为 `2026-02-15T19:44:22Z` 至 `2040-02-02T19:44:22Z`，critical EKU 仅含 Time Stamping。其 certificatePolicies 为 `1.3.5.8`，**不能据此填入实际 token 的 TSTInfo policy**。CRL 编号 4096，thisUpdate 为 `2026-09-18T10:30:55Z`，nextUpdate 为 `2027-09-18T10:30:55Z`，未列出撤销条目。较远的 nextUpdate 不等于独立审核已经认可它的新鲜度。

本会话离线执行 `openssl crl -in root_ca.crl -verify -CAfile ca.pem -noout` 成功；对下载的叶证书执行 `openssl verify -CAfile ca.pem -no-CApath -no-CAstore -auth_level 2 -purpose timestampsign -CRLfile root_ca.crl -crl_check_all tsa.crt` 返回 OK。这只验证这份**下载的叶证书**及给定链/CRL，不是对实际 RFC3161 token 签名者的验收，也没有取得生产时间证明。

实取当前叶证书摘要与英文页/CPS 相同；德文页所列摘要恰好对应官方已过期旧证书，其有效期为 `2016-03-13T01:57:39Z` 至 `2026-03-11T01:57:39Z`。摘要差异已对上这两份不同证书，不据此推断网站被攻陷，也不能拿旧证书验证新 token。同一域名的下载和自签链验证不能替代独立 pin 来源。独立审核仍须确认根身份、实际签名链、认可的 token policy、精度依据、CRL 最大年龄及失败策略；保留材料与审核结论。

### 严格离线 CRL 合同

4575ddb 的旧报告固定为 `revocation_status=NOT_CHECKED`，却仍可能贡献生产 FORMAL 时间资格；本批前向 0020 修复这条资格路径。生产新 profile 恰为旧五键加 `revocation_policy="offline-full-chain-crl-v1"` 与显式整数 `max_crl_age_seconds`（1 至 31536000），没有默认生产最大年龄。不可为了让目前这份 CRL 通过而自行放宽年龄。旧五键 fixture 保留合成测试用途；旧非 fixture 报告保留原文，不能取得新生产资格。

严格验证使用实际 token 的签名链与离线 CRL 全链核验，并使用 OpenSSL 的当前时间，不用 token genTime、数据库 verified_at 或导出包中的时间把时钟回拨。新 `fi.rfc3161-verification.v2` 报告只有检查成功才标 `GOOD`，保存 CRL 原字节 Base64、SHA256 及有效窗口；窗口为全部 CRL 的最大 thisUpdate 至各 nextUpdate、各 thisUpdate 加最大年龄的最小值。材料不全、过期、不匹配或撤销时拒绝，不能退回 NOT_CHECKED 放行。

`verify-anchor` 的严格路径显式提供 `--crl`。原核验时刻必须在 CRL 窗内；派发门控还要求**当前**时刻在窗内。历史 score v3 根据实际 claim/dispatch 和原核验时间判断资格，不因材料后来正常到期改写旧评分。

`verify-export` 默认取证明中留存的 CRL 字节，或显式 `--crl` 提供经审核的当前 bundle；均按复核时的当前时间重新验签，不回写原报告。登记证明的 CRL 窗还须覆盖已封存的 first_attempt_at：晚签发的新 CRL 即使通过当前链与撤销核验，也不能自动补历史资格。导出标记 `dispatch_admission=REQUIRES_ORIGINAL_DATABASE_AUDIT`，两份时间戳不能替代逐次派发账本核查。长期证据更新、OCSP、自动下载或撤销监控不在本批范围。

保留一份**非 FORMAL**预检 query/reply 及验证材料，绑定当时实际生成的审计摘要。核验 nonce、messageImprint、token 内 policy、genTime/accuracy；页面泛称 UTC 来源不能替代量化误差。没有可信精度依据时继续阻断，不能为了通过填 missing_accuracy 或扩大允许精度。网站示例里的 -no_nonce、关闭 TLS 检查不采用。验证依据为 [RFC3161](https://datatracker.ietf.org/doc/html/rfc3161)；最终证据必须是实际 TSQ/TSR 与已审核信任材料，不是本地时间、网页截图或哈希本身。

## 4. 通过后只建全新的 FORMAL

前提：只读补证闭合、正常网络与 TSA 独立审核完成、相应实现已在独立副本验证、现有独立入场审查通过。目标仍为官方 Responses/gpt-6.1-terra，保留完整 v2 规则、prompt/schema/证据和显式请求合同；任何请求变化先按原约束处理，不挪用旧诊断结果或旧计数回执。

当前已报告未知预留 $0.84，尚未承诺 $0.16；候选两次 FORMAL 各 $0.07，总额 $0.98，只有新完整输入计数、适用报价与全局未结账本支持才成立。2026-11-19 北京时间 09:09 核验时，冻结报价的 19:30 截止尚未到；这不是再次核价，也不表示未来 FORMAL 可通用。未获账单证据的旧预留不释放，$0.02 不是重试授权。CRL 补丁不改变生成请求，本批新增模型调用和费用均为 0。

新建 FORMAL → 冻结登记/证据/入场 → 先取得及时 REGISTRATION 证明 → 指定二元一次调用、核对已保存输出/实际报告模型和参数/费用 → 指定五类别一次调用 → 全量封存 → 截止前取得及时 FORECAST 证明。参数 MISSING/INVALID/MISMATCH 时保留事实并停止推进，不仅看 output OK。任何不确定失败保留预算，不自动重发。

[BLS CPI 日历](https://www.bls.gov/schedule/news_release/cpi.htm) 与 [2026 年 12 月日历](https://www.bls.gov/schedule/2026/12_sched_list.htm) 本次复核仍为 2026-12-10 08:30 纽约时间，即北京时间 **21:30**。预测和时间证明误差上界必须严格早于该瞬间；结果发布时间包含该瞬间；结算截止 **2027-01-31 23:59:59 北京时间（含）**。正式登记前再次保存官方日历快照。

目前没有 FORMAL ID 和生产证明，不安排自动结算。正式实验成立后才提供绑定实际 ID 的采集、独立裁决、评分命令和时间；没有定时能力就人工执行，不声称会自动等待。
