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

## 4. Claude 经中转 subrouter.ai（DIAGNOSTIC，2026-11-17）

端点档案在仓库外文件中注册（`FI_COMPATIBLE_PROVIDERS_FILE`）：`subrouter-claude`（`https://subrouter.ai/v1`，`anthropic_messages`，json_schema）与 `subrouter-chat`（同主机，`openai_chat`，json_schema）。同一组 BLS CPI 冻结问题、同一驱动；每次尝试上限 150000 µUSD（opus 规划上界约 0.106 美元），`max_attempts=1`，超时 120 秒（项目合同上限）。报价取自 https://subrouter.ai/api/pricing 列出的官方参考价（sonnet $2/$10、opus $4/$20 每百万 tokens）；中转实际倍率与账单未知，下表金额只是报价估算。

| 运行 | 模型 / 协议 | 题型 | 结果 | 输入/输出 tokens（含缓存） | 延迟 |
|---|---|---|---|---|---|
| sonnet-1 | claude-sonnet-5-5 / messages | 二元 | OK，[No, Yes]=[550000, 450000] | 64482 / 1701 | 7.8 s |
| sonnet-1 | claude-sonnet-5-5 / messages | 分箱 | TIMEOUT（120 s） | — | 120.2 s |
| sonnet-2 | claude-sonnet-5-5 / messages | 二元 | OK，[650000, 350000] | 3162 / 1002 | 18.6 s |
| sonnet-3 | claude-sonnet-5-5 / messages | 二元 | OK，[625000, 375000] | 3162 / 1059 | 9.2 s |
| sonnet-4 | claude-sonnet-5-5 / messages | 二元 | OK，[730000, 270000] | 3162 / 1149 | — |
| sonnet-4 | claude-sonnet-5-5 / messages | 分箱 | OK，5 个概率 [15000, 50000, 230000, 560000, 145000] | 3195 / 810 | — |
| opus-1 | claude-opus-5-5 / messages | 二元 | OK，[560000, 440000] | 3162 / 1200 | — |
| opus-1 | claude-opus-5-5 / messages | 分箱 | OK，5 个概率 [50000, 100000, 200000, 400000, 250000] | 3195 / 960 | — |
| opus-2 | claude-opus-5-5 / messages | 二元 | OK，[720000, 280000] | 7887 / 1452 | 48.5 s |
| opus-3 | claude-opus-5-5 / messages | 二元 | OK，[720000, 280000] | 8493 / 1500 | 33.1 s |
| sonnet-chat | claude-sonnet-5-5 / chat | 二元 | INVALID_OUTPUT | 7236 / 2103 | 48.1 s |

sonnet-4 与 opus-1 两题合计延迟分别为 41.2+9.7 s 与 42.8+11.4 s（驱动按尝试计时，未与题型一一对应）。11 次账本内调用报价估算合计约 0.44 美元；另有 7 次账本外探针调用（模型列表、协议与参数探测、token 计数），估算约 0.27 美元。

发现：

- 返回的 `model` 字段始终等于请求值，但响应 ID 有 `msg_01…`（Anthropic 原生格式）与 `msg_<32 位十六进制>` 两种，usage 字段结构也不同，说明请求被路由到不同上游；`/api/pricing` 为 sonnet 列出 28 个、为 opus 列出 35 个上游分组。返回模型名不能证明实际执行的模型。
- **计费 token 远大于实际提示**：同一短提示 `count_tokens` 为 191，而实际调用 usage 报告 6933 输入 + 49692 缓存写入；sonnet-1 二元题报告 64482 输入 tokens（OpenAI 同题约 950）。不带 system 时模型自称 “Claude Code”。中转很可能注入隐藏的系统提示/工具定义，实验无法保证模型只看到冻结提示词。输出 tokens（约 800–2100）也远多于可见 JSON（约 150–250 tokens）。
- Chat Completions 接口静默忽略 `response_format` 和未知参数（如 `fi_bogus_param`），输出被 Markdown 代码块包裹，被严格解析拒绝；Messages 接口执行 `output_config` json_schema。据此新增 `protocol` 字段（ADR-020）。
- Claude 两个模型在分箱题上都返回 5 个概率，gpt-4o-mini 3/3 返回 4 个：类别数缺失的合同缺陷表现为“依赖模型猜测”，不是稳定失败，仍需修合同。
- 二元题方差：sonnet P(No) 0.55/0.65/0.625/0.73，opus 0.56/0.72/0.72，gpt-4o-mini 0.40/0.62。单次调用不构成稳定预测。
- 可用性：1 次 120 s 超时、单次延迟 8–48 s；项目把超时上限硬编码为 120 s，慢中转上的长输出题可能系统性超时。

## 3. 回归测试

| 范围 | 结果 |
|---|---|
| 离线 `python -m unittest discover -s tests` | 239 项通过（原 211 + 新增 28） |
| PostgreSQL 17 `python -m unittest discover -s tests/integration` | 154 项通过（原 148 + 新增 6） |

未在 PostgreSQL 16、Docker Compose 部署、Chromium 冒烟或 GitHub Actions 上复测；DeepSeek、DashScope、OpenRouter、xAI 预设未做真实调用；仅 subrouter.ai 一个中转做过真实调用。
