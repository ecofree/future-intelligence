# OpenAI 真实诊断调用与 OpenAI 兼容供应商验收

日期：2026-11-17（UTC+8）。分支 `feat/openai-compatible-providers`，基于 main `0adde2c`。本报告只记录 DIAGNOSTIC 级接口验收，不构成 FORMAL 成绩、TSA 生产验收或 Issue #5 关闭。

## 环境

Debian 13 本机，Python 3.13.5，PostgreSQL 17.11（本地临时集群，独立 `fi_live` 库与按角色 LOGIN）。OpenAI 密钥仅经环境变量注入，未写入仓库、数据库、日志或本报告。

## 1. OpenAI 真实调用（项目自身账本路径）

运行方式：仓库外驱动脚本按 P0-01～03 接口登记许可、审核冻结 2 个真实未来问题（BLS CPI-U CUUR0000SA0 2026 M11 首次发布：二元“是否 ≥ 337.511”与 12 个月涨幅 5 分箱），登记 LIVE 实验并 `configure_operation(DIAGNOSTIC)`，封存 BLS API 实际数值作为证据，然后调用 `tasks.live_worker.run_once` 与真实 `HTTPSOnce`。每次尝试预算上限 20000 µUSD，实验总额 100000 µUSD，`max_attempts=1`。

| 路径 | 题型 | 结果 | 输入/输出 tokens | 报价估算 |
|---|---|---|---|---|
| openai（Responses API）第 1 次 | 分箱 | INVALID_OUTPUT | 936 / 103 | 203 µUSD |
| openai（Responses API）第 2 次 | 二元 | OK，[No, Yes] = [400000, 600000] | 949 / 89 | 196 µUSD |
| openai（Responses API）第 2 次 | 分箱 | INVALID_OUTPUT | 958 / 113 | 212 µUSD |
| openai_compatible → api.openai.com/v1 | 分箱 | INVALID_OUTPUT | 941 / 115 | 211 µUSD |
| openai_compatible → api.openai.com/v1 | 二元 | OK，[620000, 380000] | 931 / 119 | 212 µUSD |

模型 `gpt-4o-mini-2024-07-18`，返回模型字段与请求一致；报价依据 https://platform.openai.com/docs/pricing（$0.15 / $0.60 每百万 tokens）。另有 1 次账本外诊断调用（936 / 93 tokens）用于查看失败原因。合计 6 次计费请求，5651 输入、632 输出 tokens，估算约 0.0012 美元；预算保持 HELD_UNKNOWN，实际账单未对账。

## 2. 发现

- **分箱题 3/3 失败**：模型对 4 个边界返回 4 个概率（应为 5 个分箱）。冻结提示词与输入没有显式给出类别数，JSON schema 也未约束数组长度。本地与数据库按设计拒绝，不修补。建议新增输入/schema 版本（例如在 schema 中写死 `minItems=maxItems=K`，或在输入中给出 `categories`），需新迁移与 ADR。
- 二元题两次结果差异较大（0.60 与 0.38），单次调用不应视为稳定预测。
- 本机出口 DNS 将公网域名解析到 198.18.0.0/15，项目 `PublicHTTPS` 的 SSRF 防护会拒绝，因此 `collect-bls` 与 `anchor`（TSA）在此机器不能直接联网；模型调用使用 `HTTPSConnection`，不受影响。
- **RFC3161（库级，不入账本）**：用 curl 从 FreeTSA 取得真实时间戳，再由项目 `verify_reply` 校验，状态 VERIFIED，`revocation_status=NOT_CHECKED`。FreeTSA 不声明 accuracy，需信任档案显式设置 `missing_accuracy_us` 才能通过；CA 文件取自 TSA 自身站点，不是独立核实的信任根。DIAGNOSTIC 实验按设计不能进入正式批次，因此未执行数据库内 REGISTRATION/FORECAST 时间证明。

## 3. 回归测试

| 范围 | 结果 |
|---|---|
| 离线 `python -m unittest discover -s tests` | 236 项通过（原 211 + 新增 25） |
| PostgreSQL 17 `python -m unittest discover -s tests/integration` | 153 项通过（原 148 + 新增 5） |

未在 PostgreSQL 16、Docker Compose 部署、Chromium 冒烟或 GitHub Actions 上复测；DeepSeek、DashScope、OpenRouter、xAI 预设未做真实调用。
