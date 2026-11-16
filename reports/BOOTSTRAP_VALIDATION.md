# 初始化验证报告（发布前历史记录）

日期：2026-11-16。Python 3.13.5，Linux。本报告只覆盖初始化包。当前发布准备及复测见 `GITHUB_BOOTSTRAP.md`。

## 实际执行结果

| 检查 | 结果 |
|---|---|
| `python -m unittest discover -s tests -v` | **30 passed, 0 failed** |
| `python -m compileall -q future_intelligence tests` | 通过 |
| `python -m future_intelligence` | 离线模拟成功，内容证明校验通过 |
| 演示外部时间状态 | **NOT_REQUESTED**，不是可信时间证明 |
| `bash -n scripts/publish_github.sh` | Shell 语法通过 |
| 无 gh 环境执行发布脚本 | 正确退出码 2，拒绝继续，没有创建本地 .git 或远端 |
| 三份原始材料 SHA-256 | 3/3 与归档清单一致 |

## 没有执行或没有完成

GitHub 远端创建、推送、权限配置、Issues 创建、真实三家 API、TSA/OTS、PostgreSQL、API/界面、故障恢复集成、统计功效/聚类 Bootstrap、真实前瞻预测及外部基准。

发布脚本已做语法与缺少 CLI 的失败路径检查，但**没有真实认证/创建/推送测试**。模拟测试不能替代联网验收。

单元测试通过只说明当前函数在列出的测试样例下表现符合预期，不代表整个 MVP 或生产安全通过。

## 测试原始输出

```text
test_bad_hash_input (test_core.CommitmentTests.test_bad_hash_input) ... ok
test_bad_index (test_core.CommitmentTests.test_bad_index) ... ok
test_canonical_key_order (test_core.CommitmentTests.test_canonical_key_order) ... ok
test_empty_tree (test_core.CommitmentTests.test_empty_tree) ... ok
test_even_odd_and_single_trees (test_core.CommitmentTests.test_even_odd_and_single_trees) ... ok
test_fixed_fixture_nonce (test_core.CommitmentTests.test_fixed_fixture_nonce) ... ok
test_float_and_nonjson_rejected (test_core.CommitmentTests.test_float_and_nonjson_rejected) ... ok
test_hash_stable_and_sensitive (test_core.CommitmentTests.test_hash_stable_and_sensitive) ... ok
test_invalid_nonce (test_core.CommitmentTests.test_invalid_nonce) ... ok
test_proof_length (test_core.CommitmentTests.test_proof_length) ... ok
test_random_nonce_and_isolation (test_core.CommitmentTests.test_random_nonce_and_isolation) ... ok
test_tampered_root (test_core.CommitmentTests.test_tampered_root) ... ok
test_unicode (test_core.CommitmentTests.test_unicode) ... ok
test_wrong_direction (test_core.CommitmentTests.test_wrong_direction) ... ok
test_budget_and_limit (test_core.ProviderContractTests.test_budget_and_limit) ... ok
test_interface_cannot_fake_working_provider (test_core.ProviderContractTests.test_interface_cannot_fake_working_provider) ... ok
test_invalid_response (test_core.ProviderContractTests.test_invalid_response) ... ok
test_timezone_required (test_core.ProviderContractTests.test_timezone_required) ... ok
test_unknown_cost_preserved (test_core.ProviderContractTests.test_unknown_cost_preserved) ... ok
test_brier_extremes (test_core.ScoringTests.test_brier_extremes) ... ok
test_brier_hand_example (test_core.ScoringTests.test_brier_hand_example) ... ok
test_brier_symmetry (test_core.ScoringTests.test_brier_symmetry) ... ok
test_invalid_outcome (test_core.ScoringTests.test_invalid_outcome) ... ok
test_invalid_probabilities (test_core.ScoringTests.test_invalid_probabilities) ... ok
test_invalid_rps (test_core.ScoringTests.test_invalid_rps) ... ok
test_log_loss_does_not_hide_wrong_certainty (test_core.ScoringTests.test_log_loss_does_not_hide_wrong_certainty) ... ok
test_log_loss_hand_example (test_core.ScoringTests.test_log_loss_hand_example) ... ok
test_rps_binary_equals_brier (test_core.ScoringTests.test_rps_binary_equals_brier) ... ok
test_rps_deterministic (test_core.ScoringTests.test_rps_deterministic) ... ok
test_rps_hand_example (test_core.ScoringTests.test_rps_hand_example) ... ok

----------------------------------------------------------------------
Ran 30 tests in 0.006s

OK
```
