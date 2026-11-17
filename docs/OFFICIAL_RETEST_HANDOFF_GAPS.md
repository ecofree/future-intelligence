# 官方 Terra 闭环：本地交接缺口与本轮修复

日期：2026-11-17。仅允许 OpenAI 官方 Responses 与 `gpt-6.1-terra`，新增真实调用总预算不超过 1 美元。本文不是新的实验登记，也没有授权更换模型、扩大预算或降低 TSA/网络检查。

## 1. 本轮直接核验

- 远端 main 为 `0adde2cb7bab54eabb39c85af910cef4d3892283`；PR #10 开放、未合并，head 为 `12c5d11c3bba585ccec1b752f220cb6373796743`。
- 当前环境没有 `/workspace/dev/fi-live-run` 或其 `pr10-audit/REPORT.zh-CN.md`、`status.json`、`SETTLEMENT.md`。
- GitHub 分支列表未出现 `integration/pr10-official-retest`。对 `4d4b91a`、`f5f66a8`、`f9eafb1` 的精确提交查询均返回找不到该 SHA。不能把它们当作已读或已推送。
- 用户报告实验 （ID保留在私有交接材料） 的官方 Terra 二元/五类别诊断成功，以及两份未改动0014、0015协调与fi_live2验证：记录为 **USER_REPORTED / 未独立核验**，不否定该报告，也不伪称已读取原始账本。
- 无项目运行DSN或模型密钥注入。本执行环境对 GitHub/OpenAI/BLS/TSA 域名均遇到 DNS 解析失败；这与用户环境的 fake-IP 是不同观测，不能混称同一故障。没有发模型请求、没有新增费用、没有创建新DIAGNOSTIC或FORMAL。

## 2. 最小交接

优先将原分支（先自查无密钥/账单/私有原文）推送到原仓库，不合并main，不重写历史。也可在拥有该分支的本机生成：

```bash
git status --short
git log -1 --format='%H %P' integration/pr10-official-retest
git bundle create /tmp/fi-official-retest.bundle integration/pr10-official-retest
git bundle verify /tmp/fi-official-retest.bundle
```

bundle需保留4d4b91a及其所有祖先，含f5f66a8、f9eafb1和PR10两笔提交；另附三份脱敏交接材料。bundle不包含未提交工作：有未提交修复时分别导出暂存/未暂存补丁，先检查秘密与原始研究内容。不要上传数据库密码、API key、全库备份或未获准原文。

## 3. 分页修复（独立于未见的本地请求/迁移改动）

四类列表原来硬截前200条，没有后续页。新接口保留JSON数组响应；显式 `page_size=1..200` 启用keyset分页，通过 `X-Has-More` 与 `X-Next-Cursor` 获取后续页。实验/结果按唯一UUID升序，批次/评分按时间与UUID共同倒序，避免同时间记录的次序不稳定。cursor绑定资源、实验筛选及页大小，不是权限凭据；每次继续使用原有数据库角色。

```
GET /api/v1/experiments?page_size=100
GET /api/v1/experiments?page_size=100&cursor=<上次响应头的游标>
GET /api/v1/experiments?page_size=1&experiment_id=<精确实验UUID>
```

旧无分页参数调用仍返回一页，并以 `X-Pagination: legacy-first-page-use-page_size` 明示。`DeliveryRepository.overview(experiment_id=...)`精确读取指定实验；原运行标签测试保留断言，改为精确读取，不再假定新实验一定落在前200个UUID中。页面具有前后页按钮，只称“本页条数”，令牌变化清除旧数据，慢响应不覆盖已切换页面。

分页是实时列表，不是事务一致性或完整审计快照；并发新增需刷新。正式审计仍用既有完整批次导出，不用列表页作为评分分母。

## 4. 迁移：只读定位，禁止强行修复

新增 `scripts/audit_migrations.py` 只读取包内SQL哈希与数据库迁移表，事务设置READ ONLY。它不创建表、不执行迁移、不改写校验和、不修改文件。输出仅迁移名称、哈希、记录顺序、待应用和差异；连接错误只输出脱敏状态。

```bash
# DSN通过已有安全环境注入，禁止放在命令参数或报告中。
python scripts/audit_migrations.py --dsn-env FI_LIVE_READONLY_DSN
python scripts/audit_migrations.py --dsn-env FI_LIVE2_READONLY_DSN
```

角色需要已有迁移元数据只读权限；不自动扩大生产账户权限。代码目录可显式用 `--migrations-dir` 指向待核对分支SQL目录，报告不输出私有路径。两份0014以完整文件名为身份，不自动重命名或一概判错；待应用文件排在已应用记录之前会单列为顺序复核。

旧fi_live恢复顺序：保留原库与只读副本/备份 → 导出已应用名称/sha256/时间 → 从原提交、备份或构建产物找出与旧sha256一致的历史字节 → 对照实际表/约束/函数定义定位差异 → 在副本中评审、验证新的前向协调迁移及数据/记录指纹。找不到原始字节时如实标记来源未恢复，不得从现库反推后伪称等同原迁移。不能通过删除迁移行、更新sha256、修改已应用文件或清库来“修好”。

本轮没有读取fi_live/fi_live2，也没有看见本地0015，所以不新增0016、不判断0015正确性。CI可验证的升级仅限远端13→PR10第14迁移，不冒充本地两份0014与0015的升级验收。

## 5. 请求冻结：模型不换，参数支持与实际执行分别记录

2026-11-17核对官方文档：
- https://developers.openai.com/api/docs/models/gpt-6.1-terra
- https://developers.openai.com/api/docs/guides/latest-model
- https://developers.openai.com/api/reference/resources/responses/methods/create

Terra支持 reasoning.effort 的 none/low/medium/high/xhigh/max，文档默认medium；家族指南指出reasoning非none时不应传temperature/top_p/top_logprobs。Responses不能因Chat Completions或Evals支持seed，就假定这里也支持seed。

后续新实验候选方案是固定reasoning.effort=medium，并明确记录temperature/top_p在该模式下不支持、未发送；seed未确认支持、未发送。若决定使用none并设置temperature=0，则是另一份新配置，必须新建DIAGNOSTIC验证，不能悄悄替换中等推理配置。固定参数也不是位级确定性保证。

收到本地代码后须同时修改请求生成、数据库精确白名单/校验及冻结配置，保存精确无认证请求字节、模型/端点、提示词hash、证据hash和schemahash。不能只在Python上补参数导致数据库拒绝，也不能仅在文档宣称已固定。当前这项仅完成文档核查，未改请求、未跑新诊断。

任何新调用都走原账本，累计预算上限1,000,000微美元；未知费用仍挂账，不能填0解锁。失效凭证、预算不足或计费无法约束时停止。不读取聊天中曾贴出的临时密钥，不把它们复制到代码、日志或新配置。

## 6. 网络与TSA

在拥有本地环境的执行端修复DNS/代理的正常出网配置，或使用获准的正常联网运行器；保持域名白名单、公共地址检查、TLS主机验证，不增加fake-IP CIDR例外，不硬编码绕过地址、不关闭证书校验。当前会话无权修改用户宿主DNS。

TSA需要实际待审核CA/中间链/签名证书和独立信任来源、策略OID、原始回包、nonce、时间精度依据，以及当前撤销与长期验证政策。供应商网站公布指纹不是独立审核完成；签名验证成功也不是全部正式资格。不能凭人为missing_accuracy_us或NOT_CHECKED撤销状态偷偷降低要求。未取得上述材料，因此本轮TSA仍未审核、未申请新的证明。

## 7. CPI日历与首次值

已核对BLS官方：https://www.bls.gov/schedule/2026/12_sched_list.htm 和 https://www.bls.gov/schedule/news_release/cpi.htm 。11月2026 CPI计划于2026-12-10 08:30 America/New_York发布，即13:30 UTC、21:30 Asia/Shanghai、22:30 Asia/Tokyo。该日历可更新，FORMAL冻结前须从真实运行环境重新留存原始页面/哈希，不把本次网页检索当作已封存证据。

未读取实际question_spec，所以不能推断目标是总体/核心、CPI-U/CPI-W、季调/未季调、同比/环比或指数点位。必须按既定题目明确序列、单位、目标期间、比较阈值、分箱边界、首份官方发布的具体表格/列和舍入规则。FIRST_RELEASE使用首发原始快照，不用之后API返回的最新修订值替代；官方季调值存在修订机制，见 https://www.bls.gov/cpi/questions-and-answers.htm 。

当前没有创建FORMAL，也没有安排结算自动任务。仅在全新FORMAL成立后，才以其实际ID/冻结规则安排公布后的核验结算；发布前保持PENDING，不事后补造事前证明。
