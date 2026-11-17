# 当前验收索引

更新：2026-11-17。所有状态与具体提交绑定；新验收不改写旧报告。

- [运行准备与隔离部署](OPERATIONAL_READINESS_VALIDATION.md)：代码 `1138fc2`；191 项完整离线回归、148 项真实数据库测试，在 PostgreSQL 16/17 全部重启/恢复步骤通过；实际 Compose 新安装、数据库中断恢复、同集群新库备份还原与 Chromium 操作通过。不是用户生产部署或真实模型/TSA/前瞻预测验收。
- [P0-04～07 集成工程](P0_04_07_VALIDATION.md)：历史代码 `a0f4d7d`；163 项离线、124 项数据库，保留当时的边界与失败修复。
- [P0-03 证据与模型实现](P0_03_VALIDATION.md)：历史代码 `603cb6e`；99 项离线、98 项数据库。真实联网验收跟踪 Issue #5。
- [P0-02 实验与任务账本](P0_02_VALIDATION.md)：历史代码 `db76bbf`；64 项离线、66 项数据库。
- [P0-01 持久化](P0_01_VALIDATION.md)：历史代码 `b3c2bc4`；44 项离线、28 项数据库。
- [初始化历史](BOOTSTRAP_VALIDATION.md)、[初始化 GitHub 发布](GITHUB_BOOTSTRAP.md)：只代表当时状态。

当前是可在隔离环境部署、进行受控集成测试的内部工程预览。测试 CA 不等于独立 TSA，合成事件不等于真实前瞻成绩，同集群恢复不等于跨主机灾备，日志告警不等于通知送达。实际运行门槛继续跟踪 Issue #5/#7。

操作：[运行与部署](../docs/OPERATIONAL_READINESS.md)、[原集成说明](../docs/P0_04_07_RUNBOOK.md)。范围：[实施计划](../docs/IMPLEMENTATION_PLAN.md)。主分支/历史 PR 合并尚未执行。
