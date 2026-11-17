# Future Intelligence

**专业未来研究与概率预测系统 · V2.0.1 内部工程预览**

仓库：`ecofree/future-intelligence`。当前开发分支：`codex/operational-readiness`，PR #8，继承 PR #6 的集成成果。主分支和历史 PR 尚未合并，不要将默认 main 当作最新功能。

核心产品是专业研究系统；多模型竞争和公开记分用于检验能力。代码公开不等于研究服务已经上线。

## 当前能力与真实边界

问题/许可冻结、全量实验与任务/预算账本、统一证据和三家 HTTP 适配器、完整批次承诺、RFC3161 校验、独立结果判定、历史评分、事件簇统计和内部工作台已经连接。

本批新增数据库级正式/联调门控、来源快照与独立入场确认、官方发布事件分组、暂停/恢复及封存时间预留；提供角色分离的本机 Compose 部署、备份还原与真实浏览器检查。

代码验收 `1138fc2`：**191 项完整离线测试、同一套 148 项 PostgreSQL 测试在 16/17 上通过**；实际重启/恢复、Docker 新部署、数据库中断恢复、同集群新库备份还原及 Chromium 操作均已在隔离 CI 实测。

**三家真实模型请求/费用、独立外部 TSA、真实未来事件结算、用户生产环境持续部署仍未验收。** 合成响应和测试 CA 不计入真实成绩。FORMAL 只是运行政策，不能自行证明真实验收完成。UI 是最小内部只读运行台，不是完整研究编辑器或公开竞争平台。

## 新部署：默认不调用模型

Linux、Docker Engine/Compose v2、Python 3；其他宿主须另外验证。

```bash
git clone --branch codex/operational-readiness https://github.com/ecofree/future-intelligence.git
cd future-intelligence
export FI_UID="$(id -u)" FI_GID="$(id -g)"
python3 deploy/configure.py
docker compose -f deploy/compose.yaml up --build -d web
curl --fail http://127.0.0.1:8080/readyz
```

同一宿主浏览器打开 `http://127.0.0.1:8080/`。本机访问令牌位于新生成的私有文件 `deploy/.local-secrets/dashboard_token`，使用本机安全方式读取，不贴到聊天、日志或仓库。配置目录已存在则拒绝覆盖；正常停止不要删除数据库卷。默认没有模型密钥，不启动模型 worker，不生成假的预测成绩。

**[完整运行与升级说明](docs/OPERATIONAL_READINESS.md)** 包含身份、正式入场顺序、恢复、封存、费用和备份限制。TSA 申请和独立验证仍是单独步骤；日志告警不等于短信/邮件送达；同集群恢复不等于跨主机灾备。

## 开发与验收

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install '.[postgres,web]'
python -m unittest discover -s tests -v
python -m future_intelligence
```

Python 3.11+、OpenSSL 3；数据库回归覆盖 PostgreSQL 16/17。最后一个命令是离线演示，不调用真实模型。数据库测试需要隔离的专用测试库，不要指向生产库。

- [冻结规格](docs/V2.0.1_SPEC.md)、[实施门槛与剩余工作](docs/IMPLEMENTATION_PLAN.md)
- [当前验收索引](reports/VALIDATION.md)、[运行准备实测报告](reports/OPERATIONAL_READINESS_VALIDATION.md)
- [原集成说明](docs/P0_04_07_RUNBOOK.md)、[本批 ADR-018](docs/adr/018-operational-admission-and-local-deployment.md)、[历史 ADR](docs/adr/README.md)
- [P0-01 数据库](docs/P0_01_DATABASE.md)、[P0-02 账本](docs/P0_02_TASK_LEDGER.md)、[P0-03 模型接口](docs/P0_03_PROVIDERS.md)、[工程代理规则](AGENTS.md)
- [真实供应商验收 #5](https://github.com/ecofree/future-intelligence/issues/5)、[实际运行验收 #7](https://github.com/ecofree/future-intelligence/issues/7)

## 安全与发布边界

默认只向本机发布工作台，数据库不发布端口；远程 TLS、身份、限速和主机安全另行加固。应用不自动载入 .env，凭证由环境或明确的私有文件注入。内部导出含研究内容，不自动公开。签名绑定内容与时间，不自动证明资料真实、供应商身份或预测能力。

不提交 .env、访问密钥、证书私钥、账单、真实研究数据、备份或未经许可的原文。原始评估附件仅保留指纹和材料说明。尚未选择开源许可证。仓库已存在，不要用历史 `scripts/publish_github.sh` 创建新仓库。
