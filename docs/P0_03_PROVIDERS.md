# P0-03：冻结证据包与受控模型调用

更新：2026-11-16。**接入实现与模拟供应商/真实数据库测试已通过；三家真实 API 验收尚未完成。** 本文不把测试 fixture、接口名称或 LIVE 配置当成真实调用。

## 1. 本批交付与边界

沿用 P0-02 账本，不新增另一套调度器。新增 `evidence_seals`、`evidence_packets`、`provider_requests`、`provider_observations` 四张追加式表；0005/0006 为前向迁移，旧迁移不改写。

首期仅赛制 A、文字证据、一次请求。二元输出顺序固定为 `[No, Yes]`，分箱数值按题目固定顺序。暂不支持图像、文件上传、联网研究、工具调用、流式返回、多轮修补、模型自动替换或批量供应商 API。

代码路径：
- `providers/live.py`：三家请求映射、固定主机传输、严格响应解析与费用估计。
- `evidence/repository.py`：证据封存、请求登记、响应观测 SQL gateway。
- `tasks/live_worker.py`：真实调用单步运行入口；不是已部署的常驻调度服务。

初始化 `providers/base.py` 保留为历史最小契约。本批使用带分布和证据引用的 v1 协议，不把旧单值示例当生产接口。

## 2. 固定输入与权限

在任何 attempt 之前，operator 一次封存实验内每个问题的材料。每个 packet 必须包含已登记问题版本及 1–30 条证据：

```json
{
  "question_version_id": "已有冻结问题版本的 UUID",
  "items": [{
    "id": "e1",
    "source_id": "该问题绑定的来源 UUID",
    "permission_id": "该来源的许可版本 UUID",
    "title": "材料标题",
    "text": "已获得处理与存储许可的实际材料"
  }]
}
```

`EvidenceRepository.seal(experiment_id, packets)` 接收全部问题的数组。不允许少题、重复题、未登记来源、客户端伪造 observed_at，或已有 attempt 后补换材料。重复相同封存幂等；内容不同必须另建预登记实验，不能覆盖原历史。

来源按稳定顺序全部加锁后检查许可；调用前再次检查。所有参赛模型使用相同的问题与 packet，输出互不可见。`observed_at` 是服务器收到这批材料的时间，acquisition 明示为 `OPERATOR_SUBMITTED_SNAPSHOT`，**不是原网页发布时间的独立证明，也不证明材料内容真实**。自动源抓取、发布时间验证、内容事实审核仍待后续实现。

## 3. 三家适配器

| provider | 请求接口 | 密钥变量 | 结构化输出 |
|---|---|---|---|
| openai | POST api.openai.com/v1/responses | OPENAI_API_KEY | text.format / json_schema |
| anthropic | POST api.anthropic.com/v1/messages | ANTHROPIC_API_KEY | output_config.format / json_schema |
| google | POST generativelanguage.googleapis.com/v1beta/models/{model}:generateContent | GEMINI_API_KEY | generationConfig.responseJsonSchema |
| openai_compatible | POST {endpoint.base_url}/chat/completions（冻结且本地注册的端点，见 ADR-020） | endpoint.api_key_env | response_format：json_schema 或 json_object |

模型 ID 不提供默认值；使用账户实际授权、支持上述输出方式的具体型号。不能把官方文档示例型号当成本账户可用型号。

Anthropic 明确固定 `anthropic-version: 2023-06-01`；已知官方存在 2026-10-21 新版本，本批不悄悄改变已实现请求语义。Google 选择仍有文档的 generateContent，不把它称作最新推荐接口。将来升级版本/API需新增 ADR、回归测试和真实烟测。

固定官方主机、TLS 验证、不跟随重定向、不透传自定义 base_url，不自动读取代理配置。凭证仅写入认证请求头，不写入数据库或日志。OpenAI 请求显式 store=false；这不等于对供应商全部保留策略作保证。外部服务数据政策必须独立核验。

## 4. 配置及价格

每条 entry 沿用 P0-02 的五个字段：`entry_key / provider / model_id / prompt_sha256 / parameters`。prompt_sha256 必须取 `providers.live.PROMPT_SHA256`，不允许运行时自由改提示词。

parameters 仅允许：

| 字段 | 约束 |
|---|---|
| max_output_tokens | 整数128–16384；具体模型也可能有更严格限制 |
| timeout_seconds | 整数1–120 |
| input_token_ceiling | 整数4096–200000，规划限额，不是精确 tokenizer |
| price_quote | 下列四个字段，不能附带密钥 |

price_quote：`input_microusd_per_million`、`output_microusd_per_million`、`valid_until`（带时区，未过期）、`source`（报价来源 HTTPS URL）。费率是**每一百万 token 对应的微美元**，不是每 token 美元。必须依据实际模型、地区、服务层和当期价格填写；仓库不提供伪造的实时价格。

规划上限用输入规划量及最大输出计算，必须不大于 attempt_cap_microusd。输入 UTF-8 字节数加提示词及4096余量只是一种保守规划检查，不能替代各家 tokenizer，也不能保证供应商绝不超收。输出/推理计费差异、缓存与折扣、账单延迟均需真实对账。

估计费用由保存的 usage 和冻结报价可重算；**估值不是实际账单**。真实调用完成后 actual_cost 仍为 null，预算 HELD_UNKNOWN。operator 取得计费证据后通过原有 reconcile 接口结算；不能填零让预算继续放行。因此当前还不是自动对账的无人运行系统。

## 5. 运行步骤

先按 P0-01/P0-02 文档创建独立 PostgreSQL 登录角色、注册许可、审核并冻结问题。数据库管理员身份只用于迁移，不用于 worker。

```bash
python -m pip install '.[postgres]'
# 从安全环境注入 FI_MIGRATION_DATABASE_URL 后执行：
python -m future_intelligence.db.migrate
# 只检查环境变量是否存在，不访问供应商，也不证明密钥有效：
python -m future_intelligence.tasks.live_worker --preflight
```

preflight 检查全部三家：任一家缺失退出2，全部存在退出0；单供应商隔离进程只注入自己的密钥时，应查看对应字段，不把其他家缺失误判成当前供应商无效。应用不自动载入 `.env`。

operator 准备不含秘密的运行计划文件，保存在仓库外。下例执行真实数据库登记，但不会调用模型：

```python
import json
import os
from future_intelligence.evidence.repository import EvidenceRepository
from future_intelligence.providers.live import validate_entry

with open(os.environ['FI_PLAN_PATH'], encoding='utf-8') as stream:
    plan = json.load(stream)
for entry in plan['entries']:
    validate_entry(entry, plan['spec']['attempt_cap_microusd'])
repo = EvidenceRepository(os.environ['FI_OPERATOR_DATABASE_URL'])
experiment_id = repo.register(plan['external_key'], plan['question_version_ids'],
                              plan['entries'], plan['spec'])
repo.seal(experiment_id, plan['packets'])
for entry_key, db_login in plan['worker_logins'].items():
    repo.bind_worker(experiment_id, entry_key, db_login)
print(experiment_id)
```

plan.spec 必须遵循 `fi.experiment.v1` 的完整字段：suite=A、run_kind=LIVE、forecast_deadline、budget_microusd、attempt_cap_microusd、max_attempts、lease_seconds、failure_policy=REPORT_MISSING、baseline_policy=UNIFORM_CATEGORIES_V1、disclosure_policy=COMPLETE_BATCH_AFTER_RESOLUTION。截止时间不得超过任何问题截止；租约应覆盖 timeout 加充足的存储/锁等待余量。只绑定每个 worker 到单一参赛模型，使用不同数据库登录。

在对应 worker 进程注入 `FI_WORKER_DATABASE_URL` 与该家的 API 密钥，并显式授权费用后：

```bash
python -m future_intelligence.tasks.live_worker "$FI_EXPERIMENT_ID"
```

这条命令最多领取一项任务、发一次请求。不要用无限 shell 重试包裹它。缺钥/配置不符在发请求前记录 BLOCKED/FAILED；有条件修复后使用新的预登记实验，旧失败不得删除。出现 NO_CLAIM 不等于整批完成，也可能是预算不足或已过截止。

## 6. 一次执行的持久化边界

领取和预留预算 → 验证配置及密钥 → 取得冻结输入 → 登记精确请求正文/哈希并提交事务 → 再检查剩余租约 → 唯一一次 HTTPS → 保存筛选后的响应观测 → 依据原租约收取候选结果。

prepare 返回 false、提交结果不明确或数据库失联，均不授权再次发送。发送后超时/断网/限流保持保守未知费用；没有自动重试。迟到响应允许保存用量和摘要用于对账，但不得恢复过期租约或覆盖新 attempt。

数据库同时校验请求的提示词、模型ID、用户输入、输出格式和无工具约束；0006额外拒绝外层/嵌套重复JSON键。概率以整数 ppm 保存，必须总和1000000、维度正确；不归一化、不调用另一个模型修补。证据引用只能取 packet 内ID。

## 7. 数据最小化与可信边界

保存完整**无认证信息**的请求正文、输入哈希、供应商返回的模型/请求标识、选定 usage、最终结构化输出和响应原始字节的 SHA-256。错误正文和模型内部思考块不落库；只存最终简要理由。响应正文不保留，故仅凭哈希不能重放全部解析失败现场或独立证明供应商真的返回过该响应。此取舍记录在 ADR-013。

模型版本字段缺失保留 null，不用请求型号伪装为返回版本。worker、operator和数据库管理员仍是信任边界；当前不是防恶意管理员的远程执行证明。Prompt Injection 的工具权限被隔离，但文字证据仍可能误导概率；不声称完全消除幻觉或投毒。

同步请求的网络超时不是操作系统级总时长保证；DNS/网络行为、主机时钟偏移及进程崩溃需生产级监控。迟到结果由数据库截止和 fencing 拒收。当前无长期运行监督器、健康检查编排、自动账单导入、完整灾备或真实前瞻验收。

## 8. 真实验收清单（当前全部待办）

每家至少一项真实成功调用：具体模型、请求ID、实际返回版本（或明确未知）、冻结输入哈希、用量、候选结果及费用核对证据。失效凭证、429、超时、截断及非法结构的受控故障演练需要单独注明故障注入，不能说供应商自然返回过这些错误；不得为制造429故意冲击限额。

必须从部署环境的安全密钥配置运行，不在聊天或公开PR粘贴密钥。三家通过前，P0-03保持“实现通过、真实联网验收BLOCKED”，不能宣布完整验收。

## 9. 本次核对的一手文档

检索日2026-11-16；这些链接支持请求契约，不是本项目真实调用成功证据。
- OpenAI Responses：<https://developers.openai.com/api/reference/resources/responses/methods/create>
- OpenAI structured outputs：<https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses>
- Anthropic structured outputs：<https://platform.claude.com/docs/en/build-with-claude/structured-outputs>
- Anthropic版本迁移：<https://platform.claude.com/docs/en/api/migration-2026-10-21>
- Google generateContent：<https://ai.google.dev/api/generate-content?hl=en>
- Google迁移说明（仍支持generateContent）：<https://ai.google.dev/gemini-api/docs/migrate-to-interactions>
- PostgreSQL16唯一JSON键：<https://www.postgresql.org/docs/16/functions-json.html>
