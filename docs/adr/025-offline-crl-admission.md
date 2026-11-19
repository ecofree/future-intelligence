# ADR-025：离线 CRL 验证与正式时间资格

日期：2026-11-19。范围：时间证明及其资格，不改变模型请求或费用账本。实现验收绑定本提交实际测试；生产信任仍未批准。

## 问题与范围

4575ddb 的 RFC3161 校验明确报告 `NOT_CHECKED`，但旧 `batch_catalog`、派发和评分仍可能将非 fixture 报告当作生产时间资格。只补 CRL 验证函数而保留这条路径不能修复缺陷。

本批复用既有离线 verifier、批次、派发与评分入口，新增前向 0020 及严格报告协议。已有 query/reply、报告、实验、失败、UNKNOWN 预留和旧评分不改。旧 fixture 仍用于合成验证；旧非 fixture 的未查撤销报告继续留档，不能获得新的生产 FORMAL 资格。不新增 OCSP 客户端、证书下载器、多 TSA 路由、OTS 服务或调度控制器。

## 冻结 profile 与报告

旧 profile 精确五键为 `ca_sha256/policy_oids/max_accuracy_us/missing_accuracy_us/fixture`。严格 profile 精确七键，在原五键上增加：

- `revocation_policy` 固定为 `offline-full-chain-crl-v1`。
- `max_crl_age_seconds` 必须显式为 1 至 31536000 的整数；不接受 bool，没有默认生产值，不能为匹配已有 CRL 临时放宽。

原 CA pin、policy 与时间精度约束继续有效。profile 属于预登记冻结对象，不能在已冻结批次上换根、换规则或补成新 profile。

严格报告为 `fi.rfc3161-verification.v2`。原十二键保持，新增 `crl_sha256/crl_base64/crl_valid_from/crl_valid_until`，总计十六键；`verifier` 为 `openssl-ts+fi-tstinfo-crl-v2`，成功撤销检查为 `revocation_status=GOOD`。CRL Base64 保存实际验证的原始 bundle 字节，SHA256 对这些字节计算，不能用解析后重编码材料冒充原件。

验证器在固定信任材料下，对实际 RFC3161 token 的签名链执行全链 CRL 核验。单独下载的一张 TSA 叶证书通过验证，不能代替 token 签名者验证。核验使用 OpenSSL 的当前时间，不以 token genTime、请求时间或包内 claimed_verification 回拨时间。签名、链、nonce、messageImprint、policy、时间精度与严格截止检查仍须全部通过。

CRL 有效窗口为 `max(thisUpdate)` 至 `min(各 nextUpdate, 各 thisUpdate + max_crl_age_seconds)`。无认可材料、窗口外、缺少链覆盖、签名或适用范围不符、撤销及不支持情形均拒绝，不回退到 NOT_CHECKED。验证过程不自行联网补 CRL；CLI 严格验证入口显式提供 `--crl`。

## 派发与历史评分采用不同时间

内部 `anchor_time_catalog` 统一可信报告条件：legacy v1 只有 fixture 可贡献合成资格；生产使用冻结七键 profile、v2/GOOD 且真实 `anchor_verifications.verified_at` 位于 CRL 窗内的报告。仍要求真实主体、回包、信任配置和精度匹配，不能只相信 GOOD 文本。

`batch_catalog.verified_upper_time` 仅向既有派发门控提供当前时刻仍在 CRL 窗内的证明。登记时有效但派发时已过期，不能继续发送。新 FORMAL 的 REGISTRATION 证明须先于实际模型尝试，FORECAST 证明的误差上界须严格早于预测截止。

新评分标为 `fi.score.v3`，按实际历史 claim/dispatch 记录验证登记证明足够早、核验早于 claim、CRL 在相关时点有效；FORECAST 以原核验时刻在有效窗内为条件。今天的自然到期不会悄悄抹去过去按条件取得的资格或覆盖旧 v1/v2 score run。指标公式与完整任务分母不变。

离线导出默认取留存的 CRL 原字节，也可通过 `verify-export --crl` 显式提供经审核的当前 bundle，重新验证实际 TSQ/TSR 与签名链；不会改写原存储报告。保存的 verification 字段只用于取材料，不能作为签名或新鲜度的证明，也不能回拨 OpenSSL。登记证明的 CRL 窗还须覆盖已封存的 first_attempt_at；后来签发的新 CRL 可通过当前链与撤销检查，却不足以自动恢复历史时间资格，此时 `time_eligible_live=false`。

导出明确标记 `dispatch_admission=REQUIRES_ORIGINAL_DATABASE_AUDIT`，不能靠两份时间戳推导原数据库逐次派发均已准入。多年后旧 CRL 已过期时，当前重新验证可能失败；这不等于数据库自动修改历史评分。本批不声称提供长期证据更新或持续撤销监控。

## 验收与尚未解决的条件

离线回归应覆盖有效/撤销/过期/未来/不匹配及缺失 CRL、精确 profile/report 形状、旧报告保留、非 fixture 未查撤销不能放行、合成 fixture 路径、派发当前窗口、历史评分窗口和独立导出。数据库升级须在独立副本验证数据、原迁移字节、权限及函数/视图；不为此升级旧源实验库。模型二元与五类别固定请求 SHA 必须不变，预算和 UNKNOWN 不改。

2026-11-19 已下载公开 CA、TSA 叶证书及 CRL，并离线验证该叶证书的链、用途和 CRL 签名，细节见[第三关说明](../FORMAL_EGRESS_TSA_REVIEW.md)。这不是实际 token 验证或独立生产信任批准。根 pin 的独立来源、CRL 最大年龄认可、token policy、精度依据、原宿主正常出网及独立入场仍未完成。

本批没有新 DIAGNOSTIC、FORMAL 或生产时间证明，新增模型费用为 0。已有 Terra 诊断和 $0.84 未知占用保留；只改验证/记录不触发收费重跑。以后真正改变模型、prompt、证据、schema 或发送参数时，仍按原要求新建 DIAGNOSTIC，不因剩余预算少而略过。
