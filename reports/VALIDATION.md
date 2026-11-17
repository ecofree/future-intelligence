# 当前验收索引

更新：2026-11-17。状态与具体提交绑定；新验收不改写旧报告。

- [Grok 实测缺陷修复](CONTRACT_FIX_VALIDATION.md)：显式类别合同v2与正式端点配置约束；新增20项本地离线回归通过。全仓/数据库/部署检查以本批实际提交的CI结果为准；新版真实模型请求与现实结算尚未验收。复测步骤见 docs/CONTRACT_FIX_RETEST.md。
- [工程预览基线 2.0.1.dev1](PREVIEW_BASELINE_VALIDATION.md)：代码 `aa25ddec`；211 项完整离线、148 项真实 PostgreSQL 测试在16/17通过，重启恢复、Docker/Chromium 复验成功；Python3.11/3.13实际构建与安装核对50个包文件和13个迁移。主分支最终收束 SHA 和自身 CI 以 PR #9 收束记录为准。
- [运行准备与隔离部署](OPERATIONAL_READINESS_VALIDATION.md)：历史代码 `1138fc2`；191 项离线、148 项数据库，保留当时的失败修复与边界。
- [P0-04～07 集成工程](P0_04_07_VALIDATION.md)：历史代码 `a0f4d7d`；163 项离线、124 项数据库。
- [P0-03 证据与模型实现](P0_03_VALIDATION.md)：历史代码 `603cb6e`；99 项离线、98 项数据库。真实联网验收跟踪 #5。
- [P0-02 实验与任务账本](P0_02_VALIDATION.md)：历史代码 `db76bbf`；64 项离线、66 项数据库。
- [P0-01 持久化](P0_01_VALIDATION.md)：历史代码 `b3c2bc4`；44 项离线、28 项数据库。
- [初始化历史](BOOTSTRAP_VALIDATION.md)、[初始化 GitHub 发布](GITHUB_BOOTSTRAP.md)：只代表当时状态。

当前是可在隔离环境部署、进行受控集成测试的内部工程预览，不是用户生产部署或真实模型/TSA/前瞻验收。测试 CA 不等于独立 TSA，合成事件不等于真实成绩，同集群还原不等于跨主机灾备，日志告警不等于通知送达，本地配置存在不等于凭证验证。

实际运行门槛继续跟踪 #5/#7。操作：[真实验收交接](../docs/LIVE_ACCEPTANCE_HANDOFF.md)、[运行与部署](../docs/OPERATIONAL_READINESS.md)、[原集成说明](../docs/P0_04_07_RUNBOOK.md)。范围：[实施计划](../docs/IMPLEMENTATION_PLAN.md)。
