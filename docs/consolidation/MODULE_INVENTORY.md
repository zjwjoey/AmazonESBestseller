# 模块功能与依赖盘点 — Main Consolidation V1

检查基线：`origin/main` / `9d669b3`。本文件记录可在基线中直接定位的正式模块、分支候选模块和依赖边界。它不把实验分支代码认作正式功能，也不把文件存在等同于通过生产验收。

## 当前主线模块

| 模块 | 当前入口与职责 | 主要依赖 / 交接 | 当前状态 | 整理决定 |
|---|---|---|---|---|
| CLI | `pyproject.toml` 的 `amazon-es = amazon_es_bestseller.cli:main`；`src/amazon_es_bestseller/cli.py` 定义 `collect`、`ranking-snapshot`、`detail-plan`、`detail-run`、`task-collect`、`translate`、`translation-production`、`preclean`、`quality-audit`、`export` 等命令 | argparse → 领域模块；`--offline` 区分本地流程与联网动作；Production Translation handler 位于 `commands/translation_production.py` | 已用统一命令入口；新生产 handler 通过薄适配接入；CLI 文件仍保留既有主流程 | 保留命令名和现有语义；逐个把独立 handler 移至 command modules，不整体替换 CLI |
| Access / Transport | `access/`、`transport/` | 浏览器、状态检测、访问停止、证据保存；被 ranking/detail/task collection 调用 | 现有主线访问门禁 | 作为唯一正式网络边界；Detail5500 执行器不可绕开门禁 |
| Categories | `categories/` | category graph、crawler、provenance、validation | 当前 main 已有类目证据模型 | 保留并由 collection 引用 |
| Collection | `collection/ranking.py`、`ranking_v2.py`、`detail.py`、`detail_v2.py`、`detail_executor.py`、`planning.py`、`checkpoints.py`、`task.py` | `models.py`、access/transport、保存 HTML、checkpoint、`DetailState` | 正式采集和离线重放主链；详情计划由 `monitoring/detail_planner.py` 校验 | `detail_executor.py` 是正式计划执行适配层；不要新增第二套常驻 5500 执行器 |
| Monitoring / Identity | `monitoring/snapshot.py`、`monitoring/ranking_identity/*`、`monitoring/detail_planner.py` | ranking parser、identity resolver、authority/completeness gate | V4 核心 ranking/identity 快照文件与当前基线相同 | 复用 main，不重复复制。新增候选只在 API 或语义确有差异时评估 |
| Normalization | `normalization/`、`pipeline.py` | raw/source fields → typed canonical values；评分限于 0–5 | 已被 pipeline、quality 与 translation 使用；`codex/rating-validation` 的 `5.05` 回归定向测试通过 | 保留 ASIN、价格、数量、规格、品牌证据语义；限制评分值域；集中复用而非在 exporter 修补 |
| Translation | `translation/service.py`、`pool.py`、`providers/qwen_mt.py`、`preclean.py`、`cache.py`、`dictionary_service.py`、`validators.py`、`production.py`、`production_contract.py`、`budget.py` | 原始西语字段 → hash-bound 生产输入 → 预清理 → dry-run/显式 Qwen provider → shard/cache → QA/晋级候选 | Production adapter/预算/阶段 CLI 已由 PR #6 合入；共享字段别名/raw-first precedence 与内容安全 request-level 状态由 PR #8 提交，CI 待完成；无真实 provider 请求 | 保留 raw Spanish 和 ASIN+source hash+schema；候选只写独立 run-dir，不写 collection master；固定 0–5 星评分范围 |
| Quality / QA | `quality/*`、`qa/` | canonical products、rank/detail identity、category provenance、field closure | stable collector quality gate 已是基线；是 export 前置门 | 质量门不得因整合放宽或绕过 |
| Export | `export/excel.py` | 经 QA 的中西语产品记录和 image-by-ASIN | 当前三表/中文 26 列合同已在文档及测试约束 | 不改表名、顺序、核心列或人类备注语义 |
| Pipeline / Models | `pipeline.py`、`models.py`、`identity.py` | canonical records 与上述模块交接 | 现有数据层/展示层基础 | 新模块以显式数据合同衔接，不再引入平行 ASIN/rank 主键 |

## 分支候选功能及边界

| 候选功能 | 主要来源分支 | 依赖关系 | 当前判断 |
|---|---|---|---|
| V4 immutable ranking snapshot、page/source completeness、authority pointer | `codex/v4-global-30000` 及 highspeed/V4 家族 | `monitoring/snapshot.py` → ranking V2 parser → identity audit/authority | 被审查的 ranking snapshot 和 identity snapshot 实现已与 main 相同；通过主线快照和身份测试，不重复合并 |
| 30000 multi-source / native carousel、continuation、V3/V4 browser runner | `codex/highspeed-multisource-v4`、`codex/highspeed-snapshot-v3`、`codex/multisource-highspeed-v2`、`codex/v4-*` | 新增 `highspeed/`、`multisource/`、浏览器/ACP 证据和运行状态 | 与标准 collection 不是同一入口；依赖众多授权、运行、snapshot 及浏览器模块；只作实验保留 |
| Detail5500 URL-keyed bridge、quick selection、run store、source evidence | `codex/detail5500-*`、`codex/quick-selection-detail-5500-v0` | `detail_bridge5500.py` → `quick_selection.py` → highspeed URL normalizer/response alias + multisource immutable storage | 可以作为 bounded task 的候选，不作为默认主线；主线已有 `detail-plan`/`detail-run`/`detail_executor.py`、checkpoint 和访问门禁 |
| Source Audit + translation preparation | `feature/source-audit-translation-prep-v1` | `source_audit/*` → `detail_bridge5500.py`、`multisource/storage.py`、V3 URL normalization、quick-selection/response-alias；消费 5500 candidate-bound snapshots | 分支测试通过，但依赖主线没有的采集/封存栈。先把它作为 sidecar 接口候选审查，不能只复制 package 就视为可集成 |
| Qwen production translation state / hash-bound input / shards / release gate | `feature/translation-p0-reconciliation-v1` (`e0ec790`), cross-checked against `codex/5000-translation-v2-integration` | main 已有 Qwen provider、preclean、cache、QA；新增 canonical field contract、production state 和预算 ledger | 已接入候选并通过整合树离线测试；`translation-production` stages 写入显式 run-dir；真实 provider 需要有效价格证据和 `--yes`；不写 collection master |
| commands / orchestration / production workflow | `feature/amazon-es-production-v1-complete` 及 translation closure | 大范围改造 CLI、task、checkpoint、runtime state、production、release 和 scheduler | 代码量大且改写多个既有交接点；未整体移入 main。分批抽取适配层，保持旧 CLI 可用 |
| Ranking identity snapshot/detail planner hardening | `feature/ranking-identity-snapshot-v1`、`feature/ranking-snapshot-detail-planner-v1` | `monitoring/ranking_identity/*`、`quality/*`、`collection/detail_executor.py` | 两个分支 head 当前均为 `origin/main` 祖先；对应功能已在主线，保留分支历史，不重复整合 |

## 当前整合树新增的兼容性收拢

- `translation.preclean.FIELD_ALIASES` 直接复用 `production_contract.PRODUCTION_FIELD_ALIASES`；新增测试锁定两阶段共享一份字段别名合同。
- Qwen `content_safety` / `data_inspection` 请求错误仅阻止该字段请求，不会将健康 provider 标记为不可用；离线 fake-provider 回归覆盖这一状态转换。
- `pipeline._to_rating_num` 丢弃超出 0–5 的评分；`5.05` fixture 回归通过。
- pool admission 的 active-endpoint 调度修改未整合；当前没有专门回归覆盖，不将 branch 上的改动当成已验收能力。

## 正式接口交接

```text
CLI (amazon-es)
  ├─ collection: ranking / detail plan / detail execute
  ├─ persisted raw evidence + checkpoints + ASIN identity
  ├─ normalization → translation (offline plan or explicitly invoked provider)
  ├─ quality / field-closure gates
  └─ Excel export (frozen Spanish + Chinese contract)
```

联网访问仍必须经主线 Access Gate。实验采集模块不得直接向标准 exporter 写结果，也不得替换 ASIN 身份、原始字段、rank 来源或备注所有权。
