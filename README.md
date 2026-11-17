# Future Intelligence

**专业未来研究与概率预测系统 · 2.0.1.dev1 内部工程预览**

仓库：`ecofree/future-intelligence`。本版本通过 PR #9 收束 #1/#2/#3/#4/#6/#8 的叠加工程成果；最终 main 合并 SHA 与主分支自身 CI 记录见 [PR #9](https://github.com/ecofree/future-intelligence/pull/9)。工程合并不等于生产服务上线，也不代表已发布 PyPI 包或 GitHub Release。

核心产品是专业研究系统；多模型竞争和公开记分用于检验能力，不以排行榜代替研究产品本身。

## 当前能力与真实边界

问题/许可冻结、全量实验与任务/预算账本、统一证据和三家 HTTP 适配器、完整批次承诺、RFC3161 校验、独立结果判定、历史评分、事件簇统计和内部工作台已经连接。数据库级正式/联调门控、来源快照与独立入场确认、官方发布事件分组、暂停/恢复及封存时间预留已经实现。

代码 `aa25ddec` 的 **211 项完整离线测试、同一套 148 项 PostgreSQL 测试在 16/17 上通过**；实际重启/恢复、Docker 新部署、同集群新库备份还原及 Chromium 操作已在隔离 CI 复验。新增安装包检查在 Python3.11/3.13的新虚拟环境、源码目录外验证50个包文件（含13个SQL迁移）与已跟踪源码一致。

**三家真实模型请求/费用、独立外部 TSA、真实未来事件结算、用户生产环境持续部署仍未验收。** 合成响应和测试 CA 不计入真实成绩。FORMAL 是运行政策，不是验收徽章；本地配置预检成功也不证明凭证有效。UI 仍是最小内部只读工作台，不是完整研究编辑器或公开竞争平台。

## 新部署：默认不调用模型

Linux、Docker Engine/Compose v2、Python3；其他宿主须另外验证。以下从完成收束后的 main 新安装；已有配置/数据库的升级先阅读部署说明，不重置密码或删除卷。

```bash
git clone --branch main https://github.com/ecofree/future-intelligence.git
cd future-intelligence
export FI_UID="$(id -u)" FI_GID="$(id -g)"
python3 deploy/configure.py
docker compose -f deploy/compose.yaml up --build -d web
curl --fail http://127.0.0.1:8080/readyz
```

同一宿主浏览器打开 `http://127.0.0.1:8080/`。访问令牌位于本机私有文件 `deploy/.local-secrets/dashboard_token`，使用本机安全方式读取，不贴到聊天、日志或仓库。配置目录已存在则拒绝覆盖；正常停止不要删除数据库卷。默认没有模型密钥，不启动模型 worker，不生成假的预测成绩。

[完整运行与升级说明](docs/OPERATIONAL_READINESS.md) 仍适用；其中开发分支名仅用于追溯本批来源。TSA 申请与独立验证是单独步骤；日志告警不等于通知送达，同集群恢复不等于跨主机灾备。

## 开发、安装检查与无联网预检

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install '.[postgres,web]'
python -m unittest discover -s tests -v
python -m future_intelligence
python -m future_intelligence.runtime.preflight --role operator
```

Python3.11+、OpenSSL3，数据库回归覆盖 PostgreSQL16/17。演示命令明确离线，不调用真实模型。最后一条命令在没有配置时应退出2；配置可读时退出0仍只代表 `PRESENT_NOT_VALIDATED`。数据库测试需要专用隔离库，禁止指向生产库。

下一实际运行步骤、缺少的环境输入、各角色预检和真实接口证据要求见 **[真实验收交接](docs/LIVE_ACCEPTANCE_HANDOFF.md)**。

- [冻结规格](docs/V2.0.1_SPEC.md)、[实施门槛与剩余工作](docs/IMPLEMENTATION_PLAN.md)
- [当前验收索引](reports/VALIDATION.md)、[本版基线实测报告](reports/PREVIEW_BASELINE_VALIDATION.md)
- [原集成说明](docs/P0_04_07_RUNBOOK.md)、[ADR-019](docs/adr/019-preview-baseline-and-offline-preflight.md)、[ADR-018](docs/adr/018-operational-admission-and-local-deployment.md)、[历史 ADR](docs/adr/README.md)
- [P0-01 数据库](docs/P0_01_DATABASE.md)、[P0-02 账本](docs/P0_02_TASK_LEDGER.md)、[P0-03 模型接口](docs/P0_03_PROVIDERS.md)、[工程代理规则](AGENTS.md)
- [真实供应商验收 #5](https://github.com/ecofree/future-intelligence/issues/5)、[实际运行验收 #7](https://github.com/ecofree/future-intelligence/issues/7)

## 添加模型供应商

官方三家（`openai`、`anthropic`、`google`）固定主机。其他提供 OpenAI Chat Completions 兼容接口的服务使用 `openai_compatible`，**只改配置，不改代码**（设计见 [ADR-020](docs/adr/020-openai-compatible-providers.md)）。

1. 选择端点档案。内置预设：`deepseek`、`dashscope`（通义千问国内）、`dashscope-intl`、`openrouter`、`xai`。中转网关或自建端点写入仓库外的 JSON 文件，并用 `FI_COMPATIBLE_PROVIDERS_FILE` 指向它（同名会覆盖预设）：

   ```json
   [{"name": "my-relay", "base_url": "https://relay.example.com/v1", "protocol": "openai_chat",
     "api_key_env": "MY_RELAY_API_KEY", "response_format": "json_object"}]
   ```

   `base_url` 只能是 `https://域名[/路径]`；`api_key_env` 是**变量名**，不是密钥，且不能以 `FI_` 开头；`protocol` 为 `openai_chat`（`/chat/completions`）或 `anthropic_messages`（`/messages`，Bearer 换成 `x-api-key`，必须配 `json_schema`，用于只在 Messages 接口执行结构化输出的 Claude 中转）；端点支持结构化输出时用 `json_schema`，否则用 `json_object`。中转网关可能忽略 `response_format`，接入前务必先跑 DIAGNOSTIC 烟测。
2. 在参赛配置中冻结完整档案（模型 ID 可含一个 `/`，如 OpenRouter 的 `deepseek/deepseek-chat`）：

   ```python
   from future_intelligence.providers.compatible import endpoint
   from future_intelligence.providers.live import PROMPT_SHA256
   entry = {"entry_key": "deepseek-chat", "provider": "openai_compatible", "model_id": "deepseek-chat",
            "prompt_sha256": PROMPT_SHA256,
            "parameters": {"max_output_tokens": 1024, "timeout_seconds": 60, "input_token_ceiling": 16384,
                           "endpoint": endpoint("deepseek"),
                           "price_quote": {"input_microusd_per_million": 270000, "output_microusd_per_million": 1100000,
                                           "valid_until": "2026-12-01T00:00:00Z",
                                           "source": "https://api-docs.deepseek.com/quick_start/pricing"}}}
   ```

   价格必须按当期官方价目填写，上例数字仅示意。
3. worker 进程只注入该端点的密钥变量（如 `DEEPSEEK_API_KEY`）与 `FI_WORKER_DATABASE_URL`，先预检：

   ```bash
   python -m future_intelligence.runtime.preflight --role worker --provider openai_compatible --endpoint deepseek
   ```

   发送前 worker 会核对冻结档案与本机注册表逐字段一致，不一致记为 `ENDPOINT_NOT_REGISTERED`，不联网。之后按[模型接口](docs/P0_03_PROVIDERS.md)先做 LIVE + DIAGNOSTIC 小额实测。

## 安全与发布边界

默认只向本机发布工作台，数据库不发布端口；远程 TLS、身份、限速和主机安全另行加固。应用不自动载入 .env，凭证通过环境或明确私有文件注入。内部导出含研究内容，不自动公开。签名绑定内容和时间，不自动证明资料真实、供应商身份或预测能力。

安装包源文件一致性不等于完整依赖供应链审计，镜像/传递依赖摘要固定和 Actions 兼容告警仍待处理。不提交 .env、访问密钥、证书私钥、账单、真实研究数据、备份或未经许可的原文。原始评估附件仅保留指纹与材料说明，尚未选择开源许可证。仓库已存在，不要用历史 `scripts/publish_github.sh` 创建新仓库。
