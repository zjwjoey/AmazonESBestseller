# 分支功能盘点 — Main Consolidation V1

初始基线：`origin/main` / `9d669b3`。本表覆盖本次 `git ls-remote --heads origin` 返回的全部远端分支头；文件差异列以当时分支当前 tree 与初始基线 tree 的直接比较为准。功能归类依据分支名、HEAD 摘要、提交图、唯一 tree diff、调用关系和测试。PR #6 合入后当前 `origin/main` 为 `6cc36a1`；下方“复核决定”基于更新后的主线。

远端分支头数量：47（含 `main`）。

| 远端分支 | HEAD | HEAD 摘要 | 相对基线变更路径数 | 主要路径 | 初步归类 | 初步处置 |
|---|---|---|---:|---|---|---|
| `chore/v2-canary-readiness` | `8c302c5` | fix: accept documented Amazon bestseller URLs | 23 | canary.py, cli.py, configs, docs, quality, tests | 翻译生产闭环 | 候选整合；按数据契约、缓存和生产门分层验证 |
| `codex/5000-translation-v2-integration` | `4698957` | test(translation): lock production namespace isolation | 115 | .gitattributes, THIRD_PARTY_NOTICES.md, categories, cli.py, collection, configs, docs, identity.py | 翻译生产闭环 | production adapter 由该 V2 家族候选对账后择取并经 PR #6 合入；字段别名统一与 request-level provider 状态已在整合树测试、待独立 PR；提示词和其它改动暂留参考 |
| `codex/detail5500-browser-network-timing` | `03cc764` | Preserve passive browser network timing and separate callback metrics | 441 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-cdp-start-fix` | `b04b077` | Acknowledge real reserved CDP root starts across missing Playwright redirect events | 444 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-dual-1000` | `8530dba` | Optimize isolated dual sustained dispatch with equivalent access scan and FIFO IO | 455 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-dual-stage-resume` | `c3d6405` | Resume bounded dual stages only from preserved original remainder | 460 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-dual-staged` | `865b439` | Bound dual collector pilots to cumulative 100 500 1000 stages | 458 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-family-identity` | `7e59f31` | Bound one reported-restored transport recovery and preserve manual refresh budget | 436 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-five-combined` | `6574a4a` | Add explicit three-plus-two lanes under one shared collector and budget | 459 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-five-continue-errors` | `568eec8` | Preserve failed collector and visible pages even when disk diagnostics also fail | 462 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-four-timeout-rechecks` | `251385a` | Bind four explicitly approved timeout rechecks before releasing shared gates | 465 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-keep-challenge-visible` | `d55334a` | Keep visible restriction pages open while paused for manual handoff | 427 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-local-lanes` | `588ff30` | Preserve distinct recovery proofs and default Detail5500 to visible browser | 424 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-start-event-fix` | `812806d` | Record scoped browser document starts across CDP windows | 438 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-three-visible-windows` | `a14a496` | Add reviewed three visible windows with one serial detail dispatcher | 426 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-timeout-recovery` | `213e0c3` | Bind one explicit timeout page verification without reusing recovery approval | 464 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/detail5500-true-parallel-three` | `d4ff16e` | Preserve measured full QA reserve before parallel request dispatch | 431 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/field-closure-audit` | `2c70377` | fix: clean translated display artifacts and close detail gaps | 241 | .gitattributes, .github, AGENTS.md, README.md, THIRD_PARTY_NOTICES.md, access, categories, cli.py | 来源审计 / 字段闭环 | 候选整合；审计能力按层验证 |
| `codex/highspeed-multisource-v4` | `4c168e6` | feat(v4): prepare bounded offline supplementary capacity plans | 313 | .gitattributes, .github, README.md, cli.py, configs, docs, evidence, highspeed | V4 / 高速 / 快照实验 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/highspeed-snapshot-v3` | `7ad7737` | test(highspeed): preserve ACP fixture parent capture binding | 232 | .gitattributes, .github, README.md, cli.py, configs, docs, evidence, highspeed | V4 / 高速 / 快照实验 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/multisource-highspeed-v2` | `8a7fcd8` | Rebase rolling throughput after offline resume validation | 120 | .github, README.md, cli.py, configs, docs, evidence, highspeed, multisource | V4 / 高速 / 快照实验 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/multisource-snapshot-test-v1-1` | `3db1503` | Isolate metered ACP requests from identical native retries | 56 | .github, README.md, cli.py, configs, docs, evidence, multisource, pyproject.toml | V4 / 高速 / 快照实验 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/quick-selection-detail-5500-v0` | `d0b62fd` | Accept owned media child identity with explicit canonical parent | 421 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | Detail5500 实验/修复 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/rating-validation` | `49234ca` | fix(translation): stop pool admission at provider health boundary | 105 | .gitattributes, THIRD_PARTY_NOTICES.md, categories, cli.py, collection, configs, docs, identity.py | 标准化 / provider 调度 | 评分范围修复经 PR #7 / Python 3.11+3.12 CI 合入；pool admission 修改尚未整合，待增加专门并发边界回归 |
| `codex/v4-global-30000` | `2ef8a6f` | Avoid redundant V4 validation when no pacing wait is needed | 369 | .gitattributes, .github, README.md, cli.py, configs, docs, evidence, highspeed | V4 / 高速 / 快照实验 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/v4-serial-read-optimization` | `94e0561` | fix: expose exact ranking grid boundary during native discovery | 374 | .gitattributes, .github, README.md, cli.py, configs, docs, evidence, highspeed | V4 / 高速 / 快照实验 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/v4-three-lane-evaluation` | `a88a64b` | Reject owner-thread proxy calls instead of blocking the browser dispatcher | 374 | .gitattributes, .github, README.md, cli.py, configs, docs, evidence, highspeed | V4 / 高速 / 快照实验 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `codex/v4-three-source-5001` | `e8b1f01` | fix: bound native grid observation for late widget startup | 343 | .gitattributes, .github, README.md, cli.py, configs, docs, evidence, highspeed | V4 / 高速 / 快照实验 | 实验保留；逐项审查安全、身份与恢复补丁；执行器不启用 |
| `feat/1000-sku-validation` | `5f7dc1d` | fix(scale): use discovered bestseller category nodes | 237 | .gitattributes, AGENTS.md, README.md, THIRD_PARTY_NOTICES.md, access, categories, cli.py, collection | 采集 / 身份 / 生产流程 | 候选保留或归档；核对调用链与测试后定 |
| `feat/4500-product-master` | `6dcec94` | fix: make 5000 plan reproducible in CI | 151 | .gitattributes, THIRD_PARTY_NOTICES.md, access, categories, cli.py, collection, configs, docs | 采集 / 身份 / 生产流程 | 候选保留或归档；核对调用链与测试后定 |
| `feat/pipeline-production-hardening` | `d66481b` | test(config): align Amazon.es host assertion | 237 | .gitattributes, AGENTS.md, README.md, THIRD_PARTY_NOTICES.md, access, categories, cli.py, collection | 采集 / 身份 / 生产流程 | 候选保留或归档；核对调用链与测试后定 |
| `feature/amazon-crawler-v2-reference-integration` | `48b520b` | fix: stop category crawl and preserve collector fallback | 61 | categories, cli.py, collection, configs, docs, monitoring, pyproject.toml, quality | 采集 / 身份 / 生产流程 | 候选保留或归档；核对调用链与测试后定 |
| `feature/amazon-es-production-v1-complete` | `1c570e2` | fix(collection): bind detail migration to runtime fingerprint | 115 | .github, AGENTS.md, README.md, access, cli.py, collection, commands, configs | 采集 / 身份 / 生产流程 | 候选保留或归档；核对调用链与测试后定 |
| `feature/amazon-es-reconnaissance` | `410cb0f` | feat: add bounded catalog completion workflow | 349 | "historical, .gitattributes, .github, .gitignore, AGENTS.md, AmazonESBestseller, README.md, THIRD_PARTY_NOTICES.md | 其他 / 稳定基线 | 候选保留或归档；核对调用链与测试后定 |
| `feature/ranking-identity-snapshot-v1` | `015d0e5` | fix(ranking-identity): close cross-evidence and legacy replay gaps | 71 | .gitattributes, THIRD_PARTY_NOTICES.md, categories, cli.py, collection, configs, docs, identity.py | 采集 / 身份 / 生产流程 | 候选保留或归档；核对调用链与测试后定 |
| `feature/ranking-snapshot-detail-planner-v1` | `0e22714` | fix(monitoring): close snapshot and identity review findings | 103 | .gitattributes, THIRD_PARTY_NOTICES.md, categories, cli.py, collection, configs, docs, identity.py | 采集 / 身份 / 生产流程 | 候选保留或归档；核对调用链与测试后定 |
| `feature/source-audit-translation-prep-v1` | `a74c659` | Fix source audit review gaps and close derived repair reaudit loop | 406 | .gitattributes, .github, README.md, access, cli.py, collection, configs, docs | 来源审计 / 字段闭环 | 候选整合；审计能力按层验证 |
| `feature/stable-collector-quality-gate` | `9d669b3` | feat: add stable collector quality gate | 0 | 与基线树相同 | 其他 / 稳定基线 | 已进入基线；保留，不重复合并 |
| `feature/translation-p0-reconciliation-v1` | `e0ec790` | Harden translation cache persistence and reduce redundant I/O | 181 | .github, AGENTS.md, README.md, access, cli.py, collection, commands, configs | 翻译生产闭环 | 候选整合；按数据契约、缓存和生产门分层验证 |
| `feature/translation-production-closure-v1` | `c5eb4ee` | Merge remote-tracking branch 'origin/feature/translation-production-closure-v1' into feature/translation-production-closure-v1 | 239 | .github, AGENTS.md, README.md, access, cli.py, collection, commands, configs | 翻译生产闭环 | 候选整合；按数据契约、缓存和生产门分层验证 |
| `feature/translation-v2-qwen-mt` | `94cf933` | fix(translation): scope structured detail memory by attribute label | 162 | .gitattributes, AGENTS.md, THIRD_PARTY_NOTICES.md, access, categories, cli.py, collection, configs | 翻译生产闭环 | 候选整合；按数据契约、缓存和生产门分层验证 |
| `fix/1000-scale-blockers` | `303db9a` | docs: point review bundle to validated outputs | 232 | .gitattributes, AGENTS.md, README.md, THIRD_PARTY_NOTICES.md, access, categories, cli.py, collection | 采集 / 身份 / 生产流程 | 候选保留或归档；核对调用链与测试后定 |
| `fix/challenge-recovery-status-consistency` | `3fe4e87` | fix(access): preserve recovered challenge page state | 201 | .gitattributes, AGENTS.md, THIRD_PARTY_NOTICES.md, access, categories, cli.py, collection, configs | 访问安全 / 监控 | 候选保留或归档；核对调用链与测试后定 |
| `fix/final-4500-cooldown-consistency` | `46d0acf` | docs: update category-scale collection policy | 184 | .gitattributes, AGENTS.md, THIRD_PARTY_NOTICES.md, access, categories, cli.py, collection, configs | 采集 / 身份 / 生产流程 | 候选保留或归档；核对调用链与测试后定 |
| `fix/monitoring-canary-hardening-v1` | `36ab0c5` | fix(detail): validate identity during offline reparse | 93 | .gitattributes, THIRD_PARTY_NOTICES.md, categories, cli.py, collection, configs, docs, identity.py | 访问安全 / 监控 | 候选保留或归档；核对调用链与测试后定 |
| `fix/post-merge-integration-hardening-v1` | `3660eeb` | fix(monitoring): unify authority manifest state | 19 | cli.py, configs, docs, quality, tests | 其他 / 稳定基线 | 候选保留或归档；核对调用链与测试后定 |
| `main` | `9d669b3` | feat: add stable collector quality gate | 0 | 与基线树相同 | 其他 / 稳定基线 | 本次盘点的初始正式基线；PR #6 后已推进至 `6cc36a1` |

## 全量复核后的功能处置

| 处置类别 | 功能/来源 | 复核依据与决定 |
|---|---|---|
| **正式保留** | 当前 `main` 的 collection、normalization、identity、quality/Field Closure、Qwen provider、cache 和三表/26列 export | 作为唯一正式合同；沿用现有 CLI 与 checkpoint，不复制平行状态机 |
| **整合** | Translation V2 hash-bound production adapter（`feature/translation-p0-reconciliation-v1` / `codex/5000-translation-v2-integration`） | adapter 已由 PR #6 合入 main；`codex/5000` 家族的共享别名和 request-level provider failure 修正正在本整合树单独测试/PR |
| **整合** | rating 0–5 范围校验（`codex/rating-validation`） | pipeline 的 4 行校验和 `5.05` 回归测试已在本整合树通过定向测试；不搬入该分支无专门回归的 provider pool admission 调度修改 |
| **替换** | V4 ranking/identity snapshot、ranking authority 和正式 detail 执行入口 | 分支副本分别由 main 的 `monitoring/snapshot.py`、`monitoring/ranking_identity/*`、`detail-plan` → `detail-run` 实现替代；V4 snapshot 22 项离线测试通过，不再保留第二个正式实现 |
| **实验保留** | 全部 `codex/detail5500-*`、`codex/quick-selection-detail-5500-v0` | 双/三/四/五 lane 和 browser recovery 仍绑定专用 bridge/多源状态；只作为实验依据，不启用、不替换正式 detail-run |
| **实验保留** | `codex/highspeed-*`、`codex/multisource-*`、V4 lane/runner 分支 | V4 快照核心复用 main；高速采集与 native carousel runner 仍是独立实验，不进入正式入口 |
| **BLOCKED** | `feature/source-audit-translation-prep-v1` | source audit imports `detail_bridge5500` 和 `multisource.storage`，消费 sealed candidate/task snapshot；主线 detail checkpoint/saved HTML 无该合同。需要只读 adapter 与离线 fixture 验收，未验证前不复制 package |
| **归档参考** | `codex/field-closure-audit`、`feat/1000-sku-validation`、`feat/4500-product-master`、`feature/amazon-crawler-v2-reference-integration`、`feature/ranking-identity-snapshot-v1`、`feature/ranking-snapshot-detail-planner-v1`、`feature/stable-collector-quality-gate`、`fix/1000-scale-blockers`、`fix/challenge-recovery-status-consistency`、`fix/final-4500-cooldown-consistency`、`fix/monitoring-canary-hardening-v1`、`fix/post-merge-integration-hardening-v1` | 当前远端 branch tip 均为当前 `origin/main` 的祖先，且 triple-dot tree diff 为 0；代码已在主线，保留历史作为参考，不重复合并。此类别不删除远端 Git 分支 |
| **归档参考** | `feature/amazon-es-reconnaissance` | 43 个 branch-only path 位于独立嵌套 `AmazonESBestseller/` 子项目，不属于正式仓库 package；不合并进当前 package |
| **保留参考，不整包移入** | `feature/amazon-es-production-v1-complete`、`feature/translation-production-closure-v1`、`feature/translation-v2-qwen-mt` | 大型分支有 46–239 个净差异路径，包含不同 scheduler/runtime/checkpoint 与 translation provider 变化；已逐项提取可复用 production adapter，余下内容缺少与现有主线合同相同的独立验收，不整包合并 |
| **归档参考** | `chore/v2-canary-readiness` | 独有代码主要是额外 canary CLI/config 与文档；不属于本次主线模块整理，也不执行真实 canary |

## 判定说明

- `feature/stable-collector-quality-gate` 与 `main` 同指 `9d669b3`，其功能已在基线。
- 路径差异只用于定位候选；后续分支可能已经包含先前功能，必须继续按实际实现、调用关系、测试和提交图判断，不能依据分支名或提交数重复合并。
- Detail5500、highspeed、multisource、V4 实验分支不整体合并；只考虑已经验证且符合访问门禁、身份和证据规则的最小补丁。
- “归档参考”是功能处置与不再提取的审计记录，不代表本次删除或改写远端分支。
