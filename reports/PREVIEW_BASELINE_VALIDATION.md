# 工程预览基线验收

日期：2026-11-17。版本 `2.0.1.dev1`。PR #9，分支 `codex/preview-baseline`，继承 `9af6c316`。

## 已验证代码与证据

代码提交 `aa25ddec006f895f828328e00d5925783c636de3`；PR 测试合并提交 `84da8fff12d5c97701efca6a096fe59635d16945`。两者根 tree 均为 `78b884e285f87b957dedf04548cd597bb8439cff`。本报告后的文档提交不改源代码，但其自身 CI 仍须核查。

| 检查 | 实际结果与日志 |
|---|---|
| 完整离线回归 | [43996239437](https://github.com/ecofree/future-intelligence/actions/runs/43996239437)，Python 3.11/3.12/3.13 成功；数据库任务日志同时确认完整 **211 项**通过，不把矩阵次数算成更多独立测试 |
| PostgreSQL 集成 | [43996239427](https://github.com/ecofree/future-intelligence/actions/runs/43996239427)，同一套 **148 项**在 PostgreSQL 16/17 通过；实际重启、全部检查点、暂停保留、安全恢复、未知费用保留和重复迁移全部通过 |
| 实际部署与浏览器 | [43996239453](https://github.com/ecofree/future-intelligence/actions/runs/43996239453)，Docker 全新安装、重复初始化、DB 中断/恢复、同集群新库备份还原与 Chromium 检查通过 |
| 实际 wheel 与安装验证 | [43996239431](https://github.com/ecofree/future-intelligence/actions/runs/43996239431)，Python 3.11/3.13 成功；**50 个包文件、13 个 SQL 迁移**逐字节匹配源码；新虚拟环境、checkout 外、Python -I 执行离线演示与缺配置拒绝检查 |
| 本地局部检查 | Python 3.13.5，新增 **20 项**测试和 compileall 通过；本地为局部工作目录，不冒充完整仓库验收 |

上述完整仓库、数据库、Docker、浏览器和安装检查来自 Actions 临时环境，不是用户宿主的部署验收。本地 git 访问因 DNS 解析失败，未通过本地 clone 执行全仓测试。

## 变更与修复

- 包与模块版本一致固定为 `2.0.1.dev1`，没有改变历史迁移或评分算法。
- 本地配置预检只读取所选角色的明确变量/私有文件，输出字段名和状态，不输出值或路径，不尝试 DNS、数据库、模型或 TSA。exit 0 仅表示配置存在，不能解读为凭证有效或可投入生产。
- 修复 secret-file 为 FIFO 时在类型检查前阻塞的问题：非阻塞打开后检查私有普通文件。回归在独立子进程且无 FIFO 写入方的情况下验证不会挂住。
- 构建检查拒绝缺失/变化的源码或迁移、额外文件、路径穿越、重复 ZIP 项和符号链接。实际已安装文件再次比对，避免只验证源码目录能运行。

## 主分支收束记录

本批按普通 merge 保留叠加历史，顺序为 #9 → #8 → #6 → #4 → #3 → #2 → #1 → main，不使用 force push、squash 或 rebase。每次合并须检查 head 与 base，核对合并后的内容 tree；最终 main 还须运行自身 push 检查。

最终主分支 SHA、各层合并 SHA 与 main 检查链接记录在 [PR #9](https://github.com/ecofree/future-intelligence/pull/9) 的收束记录中。本文不提前宣称尚未发生的合并或检查通过。旧阶段报告保留原时点状态。

这是版本化源码工程预览基线，不是已发布 PyPI 包或 GitHub Release。实际 wheel 被构建和安装验证，但本轮不公开二进制附件、不自动发布包、不改仓库可见性或许可证。

## 仍未验收与已知限制

三家真实请求/身份/usage/费用、独立生产 TSA/CA/撤销与长期验证、现实未来事件及独立结果确认、用户宿主持续运行、外部告警送达、跨主机恢复与 RPO/RTO、真实外部网络故障仍分别跟踪 #5/#7。本轮不进行付费请求，也不把工程测试转换为真实成绩。

wheel 文件一致性不是第三方签名或完整供应链审计；整个 wheel 的哈希可因构建元数据变化，不宣称可复现构建。基础镜像与传递依赖尚未全量摘要锁定，Actions 仍有 Node20 兼容告警，旧 statsmodels 交叉校验为可选检查。公开平台和专业研究引擎不是本轮实现范围。

下一运行入口见 [真实验收交接](../docs/LIVE_ACCEPTANCE_HANDOFF.md)。
