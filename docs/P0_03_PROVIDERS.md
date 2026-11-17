# P0-03：冻结证据包与受控模型调用

更新：2026-11-17。**原生接入与数据库工程已验证；用户提供的Grok报告包含真实DIAGNOSTIC调用，但新版合同的真实请求、完整三家验收和FORMAL现实结算仍未完成。** 本文不把fixture、接口名称或LIVE配置当成真实调用。

## 1. 范围与合同版本

沿用现有账本，不新增调度器。evidence_seals、evidence_packets、provider_requests、provider_observations保存追加式证据和调用。原0005/0006迁移保留，新合同由0014前向迁移启用。

目前仅赛制A、文字证据、单次请求。二元顺序为 `[No, Yes]`。数值分箱明确给出所有类别，包括两端无界区间；有限下界包含，上界不包含，与原评分规则一致。不支持图像、文件上传、自由联网研究、工具调用、流式返回、多轮修补或自动换模型。

主要代码：providers/live.py、questions/spec.py、evidence/repository.py、tasks/live_worker.py。新实验使用 `CURRENT_PROMPT_SHA256`，当前冻结为：

```text
3c5510dba1ee4691d8a448bef792eacf3c03e2e434c507643a45e3223c4d38da
```

该哈希选择fi.model-input.v2，包含answer_contract（category_count、categories、unit、probability_total_ppm）。旧PROMPT/PROMPT_SHA256仍是v1，仅为保留旧实验及重放；不能修改已登记entry的哈希。旧providers/base.py仍是初始化最小契约，不当作完整运行接口。

**已存在Grok本地兼容迁移的数据库不得直接套用官方分支。** 当前远端没有compatible.py或其本地第14迁移，先保留并整合本地代码/已应用迁移；不得删迁移、改校验和或清库绕过。详见[本轮复测说明](CONTRACT_FIX_RETEST.md)。

## 2. 固定输入与权限

任何attempt之前，operator一次封存实验内全部问题的材料。每个packet绑定已登记问题版本，包含1–30条合法证据：

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

EvidenceRepository.seal(experiment_id, packets)接受全量数组；不允许漏题、重复题、未登记来源、伪造获取时间或已有attempt后换材料。相同封存幂等，不同内容需另建实验。来源先加锁再检查许可，调用前再检查。模型互不可见输出。

observed_at只表示系统收到材料的时间，OPERATOR_SUBMITTED_SNAPSHOT不证明原网页发布时间或事实真伪。来源日历/已知答案的独立入场检查仍按现有运行说明执行。

## 3. 原生适配器与输出约束

| provider | 固定接口 | 密钥变量 | v2约束 |
|---|---|---|---|
| openai | POST api.openai.com/v1/responses | OPENAI_API_KEY | text.format/json_schema；minItems=maxItems=K |
| anthropic | POST api.anthropic.com/v1/messages | ANTHROPIC_API_KEY | output_config.format/json_schema；显式描述K，本地/数据库校验 |
| google | POST generativelanguage.googleapis.com/v1beta/models/{model}:generateContent | GEMINI_API_KEY | generationConfig.responseJsonSchema；minItems=maxItems=K |

Anthropic官方文档子集不支持任意K的数组长度/数值上下界，因此不发送不受支持的约束。三家都使用相同明确类别输入，严格检查精确维度、整数范围、总和及引用。不静默删schema约束重试、不修补概率。账户具体模型是否支持请求格式仍需真实验收。

model_id没有默认值，必须使用账户可用型号。Anthropic仍固定anthropic-version:2023-06-01，未在本补丁中改变API版本。主机固定、TLS验证、无重定向、无任意base_url、无自动代理读取。凭证仅进入认证头，不落库或日志。OpenAI store=false不等于供应商全部保留策略的保证。

FORMAL只允许当前原生适配器的精确配置；携带自定义endpoint/base_url/protocol/trust_level等参数会在数据库政策/批次/请求阶段被拒绝。这是配置资格，不是对上游身份的独立证明。报告中的中转实现仍需单独审查，不因标注official而获得资格。

## 4. 配置、费用和请求限制

entry字段仍为entry_key/provider/model_id/prompt_sha256/parameters；新计划显式冻结CURRENT_PROMPT_SHA256。parameters仅四项：

| 字段 | 约束 |
|---|---|
| max_output_tokens | 整数128–16384；模型可能有更严格限制 |
| timeout_seconds | 整数1–120；本补丁不延长 |
| input_token_ceiling | 整数4096–200000，仅为规划上限 |
| price_quote | 合法报价对象，不含凭证 |

price_quote包含input_microusd_per_million、output_microusd_per_million、valid_until和source。费率单位为每百万token对应微美元，不是每token美元；按账户、型号、地区/服务层的有效价格填写，不使用测试报价。

规划上限使用输入规划量和最大输出计算，不得超过attempt_cap_microusd。UTF-8字节加提示词及4096余量不是精确tokenizer，不保证外部服务绝不超收。缓存、推理、折扣和账单延迟须单独核对。

usage与报价可生成估算，但**估算不是账单**。真实调用的actual_cost保持null、预算HELD_UNKNOWN，取得计费依据后通过既有reconcile接口结算；不能填零让预算继续放行。

## 5. 在现有账本中执行

先按运行说明建立独立数据库身份、来源许可、冻结问题、合法证据与预算。管理员只用于迁移，不作为worker。

```bash
python -m pip install '.[postgres]'
# 仅对核对过迁移历史的目标库，安全注入迁移身份后执行
python -m future_intelligence.db.migrate
# 不联网，只检查选定角色配置；exit0不证明凭证有效
python -m future_intelligence.runtime.preflight --role worker --provider openai
```

在仓库外保存不含密钥的新计划；构造新entry时使用CURRENT_PROMPT_SHA256，不在重放旧计划时替换原哈希。以下只登记数据库，不调用模型：

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

plan.spec仍为完整fi.experiment.v1：suite=A、run_kind=LIVE、forecast_deadline、budget_microusd、attempt_cap_microusd、max_attempts、lease_seconds、failure_policy=REPORT_MISSING、baseline_policy=UNIFORM_CATEGORIES_V1、disclosure_policy=COMPLETE_BATCH_AFTER_RESOLUTION。截止不超过问题截止，租约覆盖请求上限和存储/锁等待余量。

第一次attempt前显式配置新实验为DIAGNOSTIC或FORMAL；前者不能事后升格，后者需要独立入场复核和合格REGISTRATION时间证明。具体命令见[运行说明](OPERATIONAL_READINESS.md)。各worker使用自己的数据库登录与密钥。明确授权费用后使用现有有界运行入口：

```bash
python -m future_intelligence.runtime worker "$FI_EXPERIMENT_ID" --cycles 1 --allow-network
```

一次只执行有界任务，不用无限shell重试包裹。NO_CLAIM不等于全批完成；缺钥、预算、过期或已封存均可能阻止领取。无权限/缺配置不得回退为模拟。所有失败保留。

## 6. 持久化与不确定调用

领取与预留预算 → 配置/凭证检查 → 冻结输入 → 精确无密钥请求先提交账本 → 检查剩余租约 → 单次HTTPS → 保存最终结构化观测 → 原租约提交结果。

prepare返回false、事务提交不明确或数据库失联都不授权发送。已发送请求超时、断网、限流保持保守未知费用，不自动重发。迟到观测可留作核对，但不能复活租约或覆盖新attempt。新合同不改变这些规则。

数据库复核提示词哈希、冻结输入、模型、schema、无工具和唯一JSON键。概率是总和1000000的整数ppm，不归一化、不补缺项、不剥代码块，引用限于packet ID。

## 7. 数据与信任边界

保存无认证信息的请求正文、输入哈希、返回型号/请求标识、选定usage、最终结构化输出和原始响应哈希。供应商错误正文与内部推理块不落库。仅有哈希不能重建全部解析失败现场，也不能独立证明供应商身份；返回型号缺失仍为null，不用请求名代替。

worker、operator与数据库管理员仍是信任边界；固定配置不是远程执行证明。网络超时不是操作系统级总时长保证，DNS、网络和进程故障需实测。来源文字仍可误导预测，不能宣称消除了幻觉或投毒。

## 8. 本轮真实复测要求

先官方OpenAI的新DIAGNOSTIC二元与四边界五类问题，固定请求配置并记录提交SHA、输入哈希、request/attempt ID、实际usage和费用限制；再进行新FORMAL事前预测与现实结算。旧报告已有真实调用，不再写成从未调用，但它不能替代修复后的验收。

三家完整验收、生产TSA/信任和现实结算按#5/#7保留。故障注入与自然供应商错误分别记录，不为制造429冲击限额。当前修复的真实数据库/正式流程测试使用合成响应与测试CA，不算生产通过。

## 9. 协议依据

2026-11-17核对的官方资料支持请求合同，不证明本账户成功：
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- https://ai.google.dev/gemini-api/docs/structured-output?lang=rest

操作：[本轮复测](CONTRACT_FIX_RETEST.md)；决定：[ADR-020](adr/020-explicit-answer-contract.md)；结果：[验收记录](../reports/CONTRACT_FIX_VALIDATION.md)。
