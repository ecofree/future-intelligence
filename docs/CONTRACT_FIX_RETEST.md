# Grok 实测缺陷：最小修复与复测

## 本批范围

只修四个边界被误作四类的输入/schema合同，增加数据库正式端点配置约束。无新服务/数据源/Agent/采样系统。当前基线仍为 main `0adde2cb`；远端没有报告所述 `providers/compatible.py` 或其本地迁移，不覆盖未提交工作。

## 新实验必须选择 v2

```python
from future_intelligence.providers.live import CURRENT_PROMPT_SHA256
# 仅在构造全新的参赛配置时使用；不得改写已登记实验。
entry['prompt_sha256'] = CURRENT_PROMPT_SHA256
```

固定值：`3c5510dba1ee4691d8a448bef792eacf3c03e2e434c507643a45e3223c4d38da`。
旧 PROMPT_SHA256 保留 v1，不是新合同。数据库执行新增迁移后，现有 live_context 自动按冻结哈希生成 v1/v2 输入；v2包含显式类别和K。各原生provider的 build_request 和 prepare_live_call 使用同一合同。不要在运行时追加其他提示词，或仅在客户端改类别数。

从干净 main+本修复运行常规迁移。已有 Grok 本地第14迁移的环境不能直接认定兼容：先提交该代码并核对 fi_meta 已应用文件与校验和，禁止改写、删除旧迁移或清库重跑。当前补丁中的0014只适用于已核对的13迁移官方基线；与未共享分支整合时须在应用前完成前向迁移排序评审。

## 最小真实复测顺序

1. 使用官方 OpenAI 的新 LIVE+DIAGNOSTIC 实验，继续沿原有冻结问题、证据、任务和预算账本执行。先二元题，再四边界五类别题。固定模型ID、端点、冻结提示词哈希及证据；不要为了挑成功样本额外重跑。
2. 保存脱敏的代码SHA、问题/实验/attempt/request编号、输入哈希、返回模型字段、输出概率、usage、HTTP状态与账单限制。实际费用未知继续HELD_UNKNOWN。报告的16条表格明细与17次摘要口径，须用真实账本导出核对，不在此补造一条调用。
3. 新版分箱要求五个整数概率、总和1000000；错误仍记INVALID_OUTPUT。成功只说明这条请求合同通过，不代表预测正确。
4. 上一步成功后，用新FORMAL实验及独立审核/生产TSA开展事前预测。沿已有运行说明取得REGISTRATION和FORECAST证明，现实结果公布后盲化判定和独立复算。测试CA/合成响应不能充数。

Subrouter 保留诊断用途；本修复不添加新的relay适配器，不自动将其结果计入官方成绩。原生配置中的自定义端点参数在FORMAL配置/批次/请求阶段被拒绝。传输实现本身仍须审查，配置标签不能证明上游身份。

本轮保留120秒上限、未知调用不重发、严格出网检查与TSA未核实项；不以改这些安全条件让复测变绿。真实服务环境、生产证据未到位时应报告BLOCKED/NOT_RUN。

## 可执行回归

```bash
python -m unittest discover -s tests -v
# 仅指向隔离测试库，禁止在生产库运行集成测试
python -m unittest discover -s tests/integration -v
```

新增的正式流程测试使用真实数据库、合成模型返回与临时测试CA，覆盖新合同进入原有预登记/请求/封存/双批次验签的路径，但不是现实前瞻验收。
