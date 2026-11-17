# ADR-020：配置驱动的 OpenAI 兼容供应商

日期：2026-11-17。状态：本批采用（本地分支，尚未合并）；真实验收覆盖 OpenAI 自身的兼容接口与一个 Claude 中转（subrouter.ai，DIAGNOSTIC）。

## 背景

现有三家适配器固定官方主机，不接受 base_url。DeepSeek、通义千问（DashScope 兼容模式）、OpenRouter、xAI 和各类中转网关都提供 OpenAI Chat Completions 兼容接口；逐家写适配器成本高，而直接开放任意 base_url 会让数据库中的配置把密钥发往任意主机。

## 决定

新增第四个 LIVE provider `openai_compatible`，不改变 openai/anthropic/google 的请求语义。

- 端点档案（endpoint profile）只含非秘密字段：`name`、`base_url`、`protocol`、`api_key_env`、`response_format`。`base_url` 必须是 `https://公开域名[/固定路径]`，不允许端口、查询串、用户信息或 `..`；`api_key_env` 为大写变量名且不得以 `FI_` 开头（防止把数据库/令牌变量发给模型）；`response_format` 为 `json_schema` 或 `json_object`；`protocol` 为 `openai_chat` 或 `anthropic_messages`。
- 完整档案冻结在参赛配置 `parameters.endpoint` 中，进入实验 manifest 和 registration 哈希，可追溯每个模型实际调用的位置。
- worker 侧本地注册表 = 代码内置预设（deepseek、dashscope、dashscope-intl、openrouter、xai）+ 可选运维文件 `FI_COMPATIBLE_PROVIDERS_FILE`（JSON 列表，同名覆盖预设）。发送前要求冻结档案与本地注册表**逐字段相等**，否则 `ENDPOINT_NOT_REGISTERED`，记录 BLOCKED 且不联网。数据库记录本身不能改变发送主机或使用的密钥变量。
- 请求固定为 `{model, messages:[system=PROMPT, user=冻结输入], max_tokens, stream:false, response_format}`，不允许工具、温度或其他透传参数。迁移 0014 在 PostgreSQL 中复核请求形状、提示词哈希、冻结输入、输出 token 上限，以及 response_format 与冻结档案一致。
- `protocol=anthropic_messages` 时请求 `{base_url}/messages`，请求体与官方 anthropic 适配器完全相同（system=PROMPT、单条 user、max_tokens、stream:false、`output_config.format=json_schema`），鉴权头为 `x-api-key` + `anthropic-version: 2023-06-01`，并按 anthropic 规则解析；该协议必须使用 `json_schema`。0014 对两种协议分别复核请求形状。
- openai_chat 协议解析 `choices[0]`：`finish_reason=stop` 才接受；`refusal`/`content_filter` 记为 REFUSED，`length` 记为 INCOMPLETE，tool_calls 视为非法输出；`reasoning_content` 等推理字段丢弃不落库。usage 读取 `prompt_tokens`/`completion_tokens`，缓存读取 `prompt_tokens_details.cached_tokens` 或 DeepSeek 的 `prompt_cache_hit_tokens`。
- `json_object` 模式下供应商不保证 schema，本地与数据库仍按原规则严格校验，不修补、不剥 Markdown 代码块。

## 为什么需要 protocol 字段

2026-11-17 真实测试 subrouter.ai：Chat Completions 接口对 `response_format`（json_schema 与 json_object）均静默忽略，模型返回 Markdown 代码块包裹的 JSON，严格解析器按设计拒绝（INVALID_OUTPUT）；同一中转的 `/v1/messages` 接口执行 `output_config` json_schema，输出可直接通过校验。因此“OpenAI 兼容”不足以描述中转，需要显式冻结协议。

## 后果与限制

新增供应商只需配置，不需改代码或迁移。0014 以 `CREATE OR REPLACE` 复制 0003/0005/0006 中的三个函数并各增加一个分支，旧迁移校验和不变。

兼容接口在细节上并不一致（`max_tokens` 与 `max_completion_tokens`、schema 支持程度、usage 字段），每个端点仍需单独的 DIAGNOSTIC 真实烟测；预设格式是保守选择，不是已验证事实。中转网关的返回模型名、计费和数据保留政策不由本项目验证。acceptance_gates 仍只以三家官方接口关闭 Issue #5。
