# Future Intelligence

**专业未来研究与概率预测系统 · V2.0.1 工程集成预览**

仓库：`ecofree/future-intelligence`。当前集成分支：`codex/p0-04-07-integrated-mvp`，PR #6。main尚未合并这些工程批次。

核心产品是专业研究系统；多模型竞争和公开记分用于检验能力。代码公开不等于研究服务已经上线。

## 当前能力

问题与版本/许可冻结、完整实验预登记、任务/预算/恢复账本、统一证据和三家HTTP适配器、完整预测批次承诺、RFC3161校验、独立双人结果判定、历史评分、事件簇统计、最小内部工作台及有界运行命令已经接入。

代码验收提交 `a0f4d7d` 的完整163项离线测试和同一套124项PostgreSQL集成测试通过；PostgreSQL16/17实际重启与恢复检查通过。

**三家真实模型验收、独立外部TSA、真实未来事件结算与生产部署仍未完成。** 本地测试CA、合成事件、模拟费用均不计入真实成绩。P0-07尚不能称生产验收通过。

## 启动与文档

```bash
git clone --branch codex/p0-04-07-integrated-mvp https://github.com/ecofree/future-intelligence.git
cd future-intelligence
python -m venv .venv
. .venv/bin/activate
python -m pip install '.[postgres,web]'
python -m unittest discover -s tests -v
python -m future_intelligence
```

Python3.11+、OpenSSL3；数据库用PostgreSQL16/17。最后一个命令仍是明确标记的离线演示，不会调用真实模型。

数据库角色、时间证明、结果确认、统计、导出和工作台启动见 **[集成运行说明](docs/P0_04_07_RUNBOOK.md)**。密钥由环境安全注入，应用不自动载入.env；不要把密钥贴到仓库、Issue或聊天里。

- [冻结规格](docs/V2.0.1_SPEC.md)与[实施门槛](docs/IMPLEMENTATION_PLAN.md)
- [当前验收索引](reports/VALIDATION.md)与[本批真实测试结果/限制](reports/P0_04_07_VALIDATION.md)
- [P0-01数据库](docs/P0_01_DATABASE.md)、[P0-02任务账本](docs/P0_02_TASK_LEDGER.md)、[P0-03模型接口](docs/P0_03_PROVIDERS.md)
- [工程代理规则](AGENTS.md)、[ADR目录](docs/adr/README.md)，本批新增ADR-014～017
- [真实供应商验收 #5](https://github.com/ecofree/future-intelligence/issues/5)与[实际上线验收 #7](https://github.com/ecofree/future-intelligence/issues/7)

## 安全与发布边界

内部工作台只监听127.0.0.1，裁决人不读预测和评分；生产远程访问另行加固。内部导出包含研究内容，不自动发布。签名证明绑定的内容和时间，不自动证明来源事实、供应商身份或预测能力。

不提交.env、访问密钥、证书私钥、账单、真实研究数据或未经许可的原文。原始评估附件不在公开仓库，仅保留指纹与材料说明。未选择开源许可证。

仓库已存在，不要运行初始化历史工具 `scripts/publish_github.sh` 去创建新仓库。所有正式阶段以证据和验收为准，不以代码量、Agent数量或模拟分数判断成功。
