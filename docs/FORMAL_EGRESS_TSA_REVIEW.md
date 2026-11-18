# Terra 诊断之后：第三关的具体剩余工作

更新：2026-11-18。本次诊断 `e1e67317-4416-445b-b5c5-2f0197bf251d` 已有两次成功保存记录；[交接审计](../reports/TERRA_DIAGNOSTIC_AUDIT.md) 已复算请求与计数。接下来修正常出网、补齐生产 TSA 独立审核和少量只读记录；不为日志补证重跑收费诊断。

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

此会话不能访问原宿主，也未更改它的网络；本会话获取公开证书时的 DNS 失败只说明本会话环境，不能冒充原宿主新的验证结果。

## 3. TSA 的独立审核必须解决的事实

2026-11-18 查询以下官方来源：

| 官方页面 | 公布的 TSA 证书 SHA256 |
|---|---|
| [英文首页](https://www.freetsa.org/index_en.php)、[CPS](https://www.freetsa.org/freetsa_cps.html) | `8bfb0305bb64e2571ca507552ef3245cb1c2fee8728e0ff8689225081ea13467` |
| [德文首页](https://www.freetsa.org/index_de.php) | `aaeb619d11a63f75d06d104a926b85c76f8276bd9eae616e82d0db5cc5ff149d` |

这些页面公布相同 CA 文件摘要 `2151b61137ffa86bf664691ba67e7da0b19f98c758e3d228d5d8ebf27e044438`，但没有说明上述 TSA 摘要差异。它可能涉及页面更新或编码，尚不能定性，更不能任选一个批准生产。应取得官方实际证书原字节，分别记录文件 SHA 与 DER 证书指纹，核对签发者、序列号、密钥、有效期及链；不要混淆 PEM 文件哈希、DER 指纹和当前网站 TLS 证书。

CPS 公布 [根证书](https://freetsa.org/files/cacert.pem)、[TSA 证书](https://freetsa.org/files/tsa.crt)、[CRL](https://www.freetsa.org/crl/root_ca.crl) 和 OCSP 地址。URL 是待审核来源，不是已信任的根或撤销结果。取得真实材料后，由独立于执行方的审核者确认根的来源和身份、timestamp 专用签名用途、认可的 policy OID、签名/链有效期、撤销查询和失败策略；保留原签名材料和审核结论。不能把自签根可自验签当成身份信任。

当前 `delivery/timestamps.py::verify_reply` 能检查固定 CA、OpenSSL 验签、nonce/messageImprint、OID 和时间精度区间，但明确写 `revocation_status=NOT_CHECKED`；0011 的报告合同也不接受其他状态。下一步必须依据审核后的撤销策略补对应实现与协议，不能手填 CHECKED、只在文档声称已查，或将 NOT_CHECKED 当作生产放行。此批不在缺少真实链、CRL/OCSP 材料与策略时猜造另一套信任框架。

保留一份**非 FORMAL**预检 query/reply 及验证材料，绑定当时实际生成的审计摘要。核验 nonce、messageImprint、token 内 policy、genTime/accuracy；页面泛称 UTC 来源不能替代量化误差。没有可信精度依据时继续阻断，不能为了通过填 missing_accuracy 或扩大允许精度。网站示例里的 -no_nonce、关闭 TLS 检查不采用。验证依据为 [RFC3161](https://datatracker.ietf.org/doc/html/rfc3161)；最终证据必须是实际 TSQ/TSR 与已审核信任材料，不是本地时间、网页截图或哈希本身。

## 4. 通过后只建全新的 FORMAL

前提：只读补证闭合、正常网络与 TSA 独立审核完成、相应实现已在独立副本验证、现有独立入场审查通过。目标仍为官方 Responses/gpt-6.1-terra，保留完整 v2 规则、prompt/schema/证据和显式请求合同；任何请求变化先按原约束处理，不挪用旧诊断结果或旧计数回执。

当前已报告未知预留 $0.84；候选两次 FORMAL 各 $0.07，总额 $0.98，只有新完整输入计数、适用报价与全局未结账本支持才成立。当前报价于 2026-11-19 19:30 北京时间失效，不能在未来几周盲用；未获账单证据的旧预留不释放，$0.02 不是重试授权。

新建 FORMAL → 冻结登记/证据/入场 → 先取得及时 REGISTRATION 证明 → 指定二元一次调用、核对已保存输出/实际报告模型和参数/费用 → 指定五类别一次调用 → 全量封存 → 截止前取得及时 FORECAST 证明。参数 MISSING/INVALID/MISMATCH 时保留事实并停止推进，不仅看 output OK。任何不确定失败保留预算，不自动重发。

[BLS CPI 日历](https://www.bls.gov/schedule/news_release/cpi.htm) 与 [2026 年 12 月日历](https://www.bls.gov/schedule/2026/12_sched_list.htm) 本次复核仍为 2026-12-10 08:30 纽约时间，即北京时间 **21:30**。预测和时间证明误差上界必须严格早于该瞬间；结果发布时间包含该瞬间；结算截止 **2027-01-31 23:59:59 北京时间（含）**。正式登记前再次保存官方日历快照。

目前没有 FORMAL ID 和生产证明，不安排自动结算。正式实验成立后才提供绑定实际 ID 的采集、独立裁决、评分命令和时间；没有定时能力就人工执行，不声称会自动等待。
