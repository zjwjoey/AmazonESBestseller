# 分支功能盘点 — Main Consolidation V1

基线：`origin/main` / `9d669b3`。本表覆盖本次 `git ls-remote --heads origin` 返回的全部远端分支头；文件差异以分支当前 tree 与基线 tree 的直接比较为准。功能归类依据分支名、HEAD 摘要和差异路径。它是审查清单，不代表功能已合并或已通过本项目验收。

远端分支头数量：47（含 `main`）。

| 远端分支 | HEAD | HEAD 摘要 | 相对基线变更路径数 | 主要路径 | 初步归类 | 初步处置 |
|---|---|---|---:|---|---|---|
| `chore/v2-canary-readiness` | `8c302c5` | fix: accept documented Amazon bestseller URLs | 23 | canary.py, cli.py, configs, docs, quality, tests | 翻译生产闭环 | 候选整合；按数据契约、缓存和生产门分层验证 |
| `codex/5000-translation-v2-integration` | `4698957` | test(translation): lock production namespace isolation | 115 | .gitattributes, THIRD_PARTY_NOTICES.md, categories, cli.py, collection, configs, docs, identity.py | 翻译生产闭环 | 候选整合；按数据契约、缓存和生产门分层验证 |
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
| `codex/rating-validation` | `49234ca` | fix(translation): stop pool admission at provider health boundary | 105 | .gitattributes, THIRD_PARTY_NOTICES.md, categories, cli.py, collection, configs, docs, identity.py | 翻译生产闭环 | 候选整合；按数据契约、缓存和生产门分层验证 |
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
| `main` | `9d669b3` | feat: add stable collector quality gate | 0 | 与基线树相同 | 其他 / 稳定基线 | 已进入基线；保留，不重复合并 |

## 判定说明

- `feature/stable-collector-quality-gate` 与 `main` 同指 `9d669b3`，其功能已在基线。
- 路径差异只用于定位候选；后续分支可能已经包含先前功能，必须继续按实际实现、调用关系、测试和提交图判断，不能依据分支名或提交数重复合并。
- Detail5500、highspeed、multisource、V4 实验分支不整体合并；只考虑已经验证且符合访问门禁、身份和证据规则的最小补丁。
