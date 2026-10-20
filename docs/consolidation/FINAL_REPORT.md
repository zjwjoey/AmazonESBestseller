# Main Consolidation V1 — 当前验收报告

## Git 基线与保护范围

- 整合工作树：`C:\Users\Administrator\.codex\worktrees\main-consolidation-v1\AmazonESBestseller`
- 整合分支：`refactor/main-consolidation-v1`
- 初始整合基线 `origin/main`：`9d669b3c7a01ccd0090acbc320d19b471a0b02a1`；本次 PR 合并后当前 `origin/main`：`6cc36a1179d21d8bbd9461085232903939a59095`。
- 生产目录的本地 `main` 仍为 `2f6292e486fb6865ac55db9d2c5da11107c7fdcd`；只读复核显示它比当前 `origin/main` 落后 39 个提交（初始基线下的历史记录为 38，PR 合并推进 main 后增为39）。生产目录未被切换、覆盖或更新。
- 生产目录 `F:\AmazonESBestseller` 未在本轮写入；未运行采集、真实翻译 API、数据库/任务状态清理或磁盘操作。
- 分支盘点覆盖 47 个远端 heads（含 `main`）；提交和路径差异只用于定位，不代替代码、依赖及测试核查。

## 功能验收状态

| 功能 | 已开发 | 当前树已整合 | 离线测试 | 已合并 main | 生产运行验收 |
|---|---:|---:|---:|---:|---:|
| V4 ranking/identity snapshots | 不适用 | 是，已在正式基线 | 是，22 项定向测试 | 是，基线已有 | 未执行本任务生产验收 |
| Translation V2 production adapter / budget | 是 | 是 | 是，完整套件 883 passed、6 skipped；GitHub Python 3.11/3.12 CI 通过 | 是，PR #6 squash commit `6cc36a1` | 否，未调用真实 provider |
| Source Audit / translation preparation | 是，来源分支具备 | 否；与主线 saved-HTML/checkpoint 合同兼容性 BLOCKED | 来源分支测试通过；主线 adapter 未测试 | 否 | 否 |
| Detail5500 / highspeed 多执行器 | 是，来源分支具备 | 否；实验保留 | 不作为本次主线验收 | 否 | 否 |
| Normalization / quality / export | 是 | 复用现有 main 实现 | 包含在完整离线回归中 | 是，基线已有 | 本任务未执行生产导出 |

## 已完成的翻译生产整合

来源为 `feature/translation-p0-reconciliation-v1`。新增 hash-bound input/state/shard/release contract、独立预算账本和现有 `amazon-es` CLI 下的 `translation-production` 阶段命令。主线 CSV 输入保留原始列，再补充 canonical alias。默认 provider 为离线 fake；Qwen 阶段要求显式 provider 配置、可用凭据、有效价格卡、预算准入和 `--yes`；`--offline` 明确禁止实际 Qwen 请求。翻译结果不写回 collection Master，正式 export 仍受原有 QA / Field Closure 和 Excel 合同控制。

完整离线测试：`python -m pytest` → **883 passed, 6 skipped**。测试未调用 Amazon 或翻译网络 API。`git diff --check` 通过。

实现提交 `811fbf7` 和状态文档提交 `2ed3878` 已通过 PR #6：<https://github.com/zjwjoey/AmazonESBestseller/pull/6> 合并到 `main`，squash commit 为 `6cc36a1179d21d8bbd9461085232903939a59095`。GitHub Python 3.11/3.12 CI 均通过。未调用真实 provider，因此生产验收仍未发生。

## 阻塞与后续

- Source Audit 当前 loader 绑定 Detail5500 sealed snapshot、`multisource.storage` 和候选任务产物。主线正式入口为 `detail-plan` / `detail-run` 与既有 checkpoint/saved HTML。需先实现只读 evidence adapter 并用离线 fixture 验证，才可解除 BLOCKED。
- `codex/detail5500-*` 双/三/五路执行器与 highspeed runner 继续实验保留；本任务没有启用。
- 快照速度、网络异常停机策略、来源审计耗时作为后续专项优化，不在本轮实施。
- 翻译生产模块：独立 PR、GitHub CI、P0 审查仍待执行；CI 通过且无 P0 后才能按用户要求考虑合并。
