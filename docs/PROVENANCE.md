# 材料与验证边界

## 原始输入

`evaluation.md`、`sources.md`、`sample_size_sim.py` 为用户上传材料。本地初始化包中按字节归档于 `docs/reference/`；公开仓库只提交 `reference/manifest.json` 中的文件名、大小与 SHA-256，不公开原件正文。

三个指纹在本次发布准备中与上传原件重新比对一致。指纹只能标识本次所依据的文件，不能替代对外部事实或法律适用性的核查。不要把原作者的来源清单当作当前事实，也不要把此前对话所称的运行记录当作新测试结果。

## 仓库状态

所有者手动创建 `ecofree/future-intelligence`。本次连接实际读取该仓库，确认初始为空、默认分支为 main、可见性为 public，并成功创建 main 的说明性初始提交及 `codex/v2.0.1-bootstrap` 开发分支。

这取代初始化包中的“尚无远端/仅可读取”的旧环境说明。本次通过已授权 GitHub 连接提交，不运行创建新私有仓库的历史脚本，不改变仓库可见性，不强推。

## 验证范围

最新离线复测见 `reports/GITHUB_BOOTSTRAP.md`；原初始化测试记录保留在 `reports/VALIDATION.md`。GitHub 提交、PR 和 Actions 是各自独立的远端证据，不与本地测试混淆。

当前代码不依赖上述私有原件即可运行。授权不足的数据源、真实模型调用和外部时间证明仍须独立验收。
