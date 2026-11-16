# Future Intelligence

**专业未来研究与概率预测系统 · V2.0.1 工程初始化**

仓库：`ecofree/future-intelligence`。开发分支：`codex/v2.0.1-bootstrap`。
核心产品是专业研究系统，不是通用排行榜。代码公开不等于研究服务已经上线。

## 当前状态

- 已冻结：V2.0.1 设计基线、ADR-01～10、实施依赖与验收门槛。
- 已实现：二元 Brier / Log Loss、分箱 RPS、规范化内容承诺、随机 nonce、Merkle 证明、模型调用契约、离线演示及 30 项单元测试。
- 尚未实现：PostgreSQL、API/工作台、三家真实模型适配器、任务调度、独立裁决、外部时间戳、统计实验引擎和生产安全。
- **没有真实模型调用、外部时间戳、外部基准成绩或真实前瞻预测。**
- 本次本地复测通过；远端 CI 状态以对应提交的 GitHub Actions 为准。

## 开发入口

1. [工程规格](docs/V2.0.1_SPEC.md)：冻结的设计基线与实现边界。
2. [ADR](docs/adr/README.md)：10 项工程决策。
3. [实施计划](docs/IMPLEMENTATION_PLAN.md)：按依赖排序的开发与验收任务。
4. [承诺协议](docs/protocols/commitments.md)：编码和证明格式。
5. [AGENTS.md](AGENTS.md)：Codex / 工程代理约束。
6. [本次验证](reports/GITHUB_BOOTSTRAP.md)：复测和发布边界。

## 克隆与运行

需要 Python 3.11 或以上。核心测试和演示只用标准库，不需要模型 API 密钥。

```bash
git clone --branch codex/v2.0.1-bootstrap https://github.com/ecofree/future-intelligence.git
cd future-intelligence
python -m unittest discover -s tests -v
python -m future_intelligence
```

演示只验证本地计算和内容完整性。`external_timestamp_status=NOT_REQUESTED` 表示没有第三方时间证明。不得将模拟分数计入真实成绩。

## 下一批实施

从 P0-01 开始：PostgreSQL 迁移、问题版本、冻结约束、UTC/原时区、事件簇和来源授权门控。随后实现实验任务账本和真实模型适配器。每批以对应验收为准，不以接口或类名存在视为完成。

## 发布与资料边界

仓库已由所有者手动创建，不要再运行用于创建新私有仓库的 `scripts/publish_github.sh`；该脚本作为初始化包历史工具保留，遇到已有同名仓库会拒绝操作。

公开提交不包含用户原始 `evaluation.md`、`sources.md` 和 `sample_size_sim.py`；仅保留其 SHA-256 清单。原件仍在对话上传及本地初始化包中，见 [材料说明](docs/PROVENANCE.md)。后续代理无需等待原件即可按照工程规格开发；引用其具体外部陈述时应重新核查。

未选择开源许可证。不提交 `.env`、访问密钥、真实研究数据或未经许可的第三方原文。本地测试通过不代表完整 MVP 或生产验收通过。
