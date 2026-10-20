# Main Consolidation V1 — 整合顺序与验收记录

## 目标

以 `origin/main` (`9d669b3`) 为正式基线，保留已经验证的 collection、identity、quality、translation、export 合同；只将有清晰依赖边界且能在离线测试中验收的功能逐步纳入。禁止在生产工作目录操作，禁止运行采集和真实翻译 provider。

## 处置原则

| 功能 | 处置 | 理由 / 依赖 | 进入主线的门槛 |
|---|---|---|---|
| V4 ranking snapshot 与 identity snapshot | **正式保留；不重复搬运** | `monitoring/snapshot.py`、`monitoring/ranking_identity/snapshot.py` 和相关 parser 已与 main 相同 | 当前基线相关 22 个离线测试通过；若后续 V4 分支有独有差异，逐文件复核 |
| Ranking V2/identity completeness 和 stable collector quality gate | **正式保留** | `collection/ranking_v2.py` → identity audit → quality authority；stable gate 已在 baseline | 以现有 tests/CI 为约束，不引入第二权威状态 |
| 标准详情采集 | **正式保留一个入口** | `detail-plan` → `detail-run` → `collection/detail_executor.py`；使用现有 checkpoint、serial scheduler、Access Gate | 定向详情/访问停止/恢复测试通过；保持现有 CLI 与状态兼容 |
| Detail5500 多窗口/多 lane 运行器 | **实验保留** | 需要独立 `detail_bridge5500`、quick selection、highspeed V3/V4 和 multisource store；任务授权范围不同 | 不启用双/三/五路；只在未来专门任务中审查候选身份、安全和恢复修复 |
| Source Audit / translation preparation | **有条件候选；当前兼容性 BLOCKED** | 远端分支独有 `source_audit/*`，并依赖主线缺少的 detail bridge、V3 URL、quick selection、response alias 和 multisource sealed storage。分支测试可以通过，但不代表可接入当前 checkpoint contract | 定义不改变原始数据的 adapter，将标准 `detail-run`/saved HTML 转为 audit 的输入合同；列出所需模块后做离线 fixture 测试，再解除 BLOCKED |
| Qwen Translation V2 production adapter | **已接入整合分支，等待独立 PR/CI** | 来源 `feature/translation-p0-reconciliation-v1`；provider、preclean、QA、cache 复用 main；新增 hash-bound input/state/shard/release contract、独立预算账本与阶段 CLI | adapter+budget 21 tests passed；跨模块翻译回归 111 tests passed；命令默认 fake；真实 Qwen 阶段需要显式 provider mode、有效当前价格卡、凭据和 `--yes`；CI/PR 尚未运行 |
| commands/orchestration 大型重构 | **不整包 cherry-pick；拆成后续 PR** | 候选分支对 CLI/task/runtime/checkpoint 等改动规模大，影响既有入口和冻结状态 | 每个薄 facade 都须维持旧 CLI；分别跑旧、新命令回归；数据合同无冲突后再合入 |
| normalization/quality/export | **main 现有实现为准** | 是翻译/采集共用合同并受 Field Closure 和 3-sheet/26-column 冻结 | 只允许必要的小修；不得用 production adapter 或 exporter 重新推断 source facts |

## 分阶段执行与记录

### 阶段 0：分支与模块对账

- [x] 核实独立整合树与分支：`refactor/main-consolidation-v1`，干净起点 `9d669b3`。
- [x] 对照 `git ls-remote --heads origin` 的 47 个远端分支；完整表见 `BRANCH_INVENTORY.md`。
- [x] 对照当前主线包结构和核心依赖；见 `MODULE_INVENTORY.md`。
- [x] 初步识别 V4 核心 snapshot 已在 main；不重复复制。
- [ ] 对 Source Audit sidecar adapter 依赖闭环，完成/解除兼容 BLOCKED。

### 阶段 1：已合并能力复用核验

- V4 rank snapshot、identity snapshot、snapshot CLI/planner 的基线离线测试：**22 passed**。
- 结论：V4 核心 snapshot 已进入基线；本阶段无需新增代码或 PR。V4 highspeed collector/runner 与快照分离，继续实验保留。

### 阶段 2：Translation Production Adapter

- [x] 选取 `feature/translation-p0-reconciliation-v1` 已提交代码作为来源，不从有未提交改动的工作树复制。
- [x] 加入 `production_contract`、`production`、独立 `budget.py` 账本和 `translation-production` 阶段 CLI；预算账本 writer 局部实现，避免依赖尚未进入 main 的 `runtime_state` 子系统。
- [x] CSV loader 保留完整 source row，再补 canonical alias，避免 Parent ASIN、备注、价格和链接等原始列丢失。
- [x] 新增默认 `fake` 的 production config example；Qwen 配置缺少有效价格卡时 fail closed。
- [x] 跑 production adapter/budget 定向测试和现有 translation 回归；最后完整离线套件 **883 passed, 6 skipped**。
- [x] 明确 `--offline` 阻断 Qwen translate stage；摘要与 shard 保存实际 provider/execution mode。
- [x] 独立 PR #6：<https://github.com/zjwjoey/AmazonESBestseller/pull/6>；Python 3.11/3.12 CI 通过，无 P0 阻塞，已 squash 合入 `main` (`6cc36a1179d21d8bbd9461085232903939a59095`)。

状态：已开发、已提交、完整离线测试通过、PR/CI 已通过并合入 main；尚未进行真实生产验收。

### 阶段 2.1：共享字段合同与评分值域（当前整合树）

- 来源：`codex/5000-translation-v2-integration`、`codex/rating-validation`。
- Production Translation 和 Pre-Clean 复用 `PRODUCTION_FIELD_ALIASES`，避免两个阶段各自维护别名表。
- 单条 Qwen content-safety/data-inspection 请求失败作为 request-level 状态处理，不禁用健康 provider；使用离线 fake provider 测试。
- `rating` 限制在合法 `0..5` 范围，保留 `5.05` 回归用例；独立 PR #7：<https://github.com/zjwjoey/AmazonESBestseller/pull/7> 已通过 Python 3.11/3.12 CI 并 squash 合入 main (`dd24b9b50181eadfa7bcd22ab9a269469f6bc4de`)。
- 未整合 provider-pool admission 并发修改，等待专门边界测试；未整合 Qwen prompt/domain/terms 变化，当前仅有 mock transport 证据，暂保留候选。
- 定向测试已通过；当前整合树完整离线套件 886 passed、6 skipped。字段合同/provider 修正待独立 PR；rating guard 已合入 main。

### 阶段 3：Source Audit 与统一命令边界

- [ ] 为主线 saved HTML / detail checkpoint 定义只读输入 adapter。
- [ ] 判断是否必须迁入 DetailBridge5500、V3 URL 和 multisource storage；如果必须且无法维持独立契约，则保留 BLOCKED，不强行耦合。
- [ ] 将 audit/prep 输出作为 translation 的 hash-bound candidate，不自动写回 master。
- [ ] 根据已有 `detail-plan` / `detail-run` 和 `quality-audit` 语义，评估薄 commands facade；不整包搬入 239-file production closure 分支。
- [ ] 按模块定向测试、PR、CI 门槛执行。

### 阶段 4：最终回归

- [ ] 完整离线测试套件。
- [ ] CLI smoke 和离线端到端流程（fixture/合成输入）。
- [ ] 数据契约检查：ASIN、raw Spanish、rank/Detail BSR、checkpoint、cache hash/schema、三表和备注。
- [ ] 更新 README、AGENTS.md、架构图和最终整合报告。
- [ ] 明确逐项标记开发、整合、离线测试、main 合并、生产运行验收状态。

## 后续专项优化（本次不实施）

- V4 快照/页面规模下的速度与 CPU/IO 成本。
- 网络异常触发的停机策略、可恢复状态和人工续跑体验。
- Source Audit 大样本耗时与资源使用。
- Detail5500 多执行器的安全/可观测性/实测证据。

以上专项不得通过降低 authority、身份、访问停止或 QA 门槛来解决。
