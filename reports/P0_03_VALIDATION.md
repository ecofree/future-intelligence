# P0-03 验收报告

日期：2026-11-16。代码验收提交：`603cb6e88a8574340fa9cd571aeda740c995b432`。

## 结论

**冻结证据、三家适配器代码、受控运行器和真实数据库契约已通过本批测试；真实供应商API验收BLOCKED。不是完整P0-03运行验收，也不是完整MVP。**

## 实际执行

| 环境/命令 | 实测结果 | 边界 |
|---|---|---|
| 本地Linux/Python3.13.5，unittest discover -s tests -v | 65 passed | 初始化30+本批新增35；本地未包含上一批34项测试，不是完整远端回归 |
| 本地compileall | 通过 | 不能替代SQL/网络验收 |
| 本地live_worker --preflight | 三家BLOCKED_MISSING_OR_INVALID_CREDENTIAL；退出2 | 未尝试供应商请求，real_api_verified=false |
| GitHub完整离线回归 | 99 passed，Python3.11/3.12/3.13检查成功 | 旧64+新增35 |
| GitHub真实PostgreSQL16/17 | 每版本同一套98项数据库测试通过 | 旧66+新增32；模型HTTP响应全为合成fixture |
| 实际数据库重启 | 原8表、任务11表、新增证据/调用4表指纹一致 | 重启≠备份恢复/完整灾备 |
| 任务恢复及迁移重跑 | 安全恢复通过；applied=[] | 没有生产常驻服务 |

可复核运行：
- [Offline core #21](https://github.com/ecofree/future-intelligence/actions/runs/43869039133)
- [PostgreSQL #18](https://github.com/ecofree/future-intelligence/actions/runs/43869039100)

PostgreSQL16日志实际显示：`Ran 99 tests ... OK`、`Ran 98 tests ... OK`；新4表重启校验PASS；旧8表和任务11表校验及恢复继续通过。PostgreSQL17对应所有步骤success。CI已在安装后的包内、仓库外执行迁移，未依赖当前工作目录找SQL。

## 首轮失败与修复

初始提交94116a6的[数据库运行](https://github.com/ecofree/future-intelligence/actions/runs/43868066207)失败1项：旧测试`test_fixture_runner_and_live_guard`期望类型不匹配42501，新包装却优先抛出缺少证据55000。没有删除测试或改变期望；0006前向迁移恢复先身份/赛制检查，再证据检查。首轮其余新增24项已通过，最终扩大为新增32项并全量复验。

追加防护：请求与模型输入拒绝重复JSON键；冻结输出schema不得改动；响应观测入库前验证概率和引用；prepare等待后再次检查剩余租约；精确请求/响应摘要追加式保存。旧迁移0001–0005未改写。

## 关键覆盖

覆盖全量证据包、同题跨模型输入一致、来源撤销、绕过Python的数据库校验、低层函数权限、跨worker越权、并发prepare仅一次true、迟到观测可保存但候选结果不可提交、重复完成不重复收取、估值不可冒充实付、缺钥无派发、超时未知费用不重试、格式失败不修补、内部思考不落库及错误正文脱敏。

本地计算的11个新增/修改代码与测试文件blob哈希回填远端树后，仍得到相同tree `18c0be5f68d3566266f74f640d0aaf81ffeca11b`。这验证这些文件字节一致，不声称完成本地重新clone后的全仓验收。

## 没有完成

- 真实OpenAI、Anthropic、Google请求各一组：本执行环境无对应凭证，无配置好的生产数据库。
- 真实429/断连/超时/拒绝行为、账户型号授权、真实token账单对账、生产网络环境。
- 自动可信题源采集、网页发布时间验证、证据事实真实性审核。
- RFC3161/OTS、正式预测锁定与公开计分、现实结果裁决、HTTP工作台和常驻调度。
- API错误完整正文回放：主动不保存原正文，只存哈希及受限摘要；见ADR-013。

真实供应商调用次数0，供应商API费用0；这是没有发出请求的事实，不是把未知账单记零。CI计算费用由GitHub账户规则决定，本报告未读取账单。

剩余条件与安全运行命令见`docs/P0_03_PROVIDERS.md`。仅有环境变量或LIVE标签不能解除真实验收BLOCKED。
