# Ranking Snapshot → Detail Planner → Incremental Detail Executor V1

## 目标与边界

V1 把榜单观察和详情生命周期分开：榜单每次运行生成一个不可变快照；planner 只读取快照、已有详情证据、状态、checkpoint 和 schema；executor 只执行 planner 已经批准的网络动作。

V1 不实现 daily-run，也不改变 Translation V2、历史生产数据或现有详情解析器。所有离线命令和测试都不访问 Amazon 或翻译服务。

## 快照生命周期

现有 `collection.ranking.parse_bestsellers_page` 负责解析 HTML。每条记录保留：

- `ranking_asin`（榜单身份，等同兼容字段 `asin`）；
- `ranking_product_url_raw`（榜单 HTML 中的原始 href，不改写）；
- `ranking_product_url_normalized`（去掉追踪参数的安全 canonical URL）；
- `ranking_link_asin` 与 `ranking_link_identity_status`；
- 排名、来源页、页码、类目、`observed_at` 和 `snapshot_id`。

`monitoring.snapshot.build_ranking_snapshot` 写入：

```text
<output-root>/YYYY-MM-DD/<snapshot_id>/
  manifest.json
  rankings.json
  rankings.csv
  audit.json
  html/                 # 如调用方提供 HTML 证据
<output-root>/latest_authoritative_snapshot.json
```

快照目录已存在时直接失败，不覆盖历史证据。只有所有计划页面都完成访问和解析、没有 `PARSE_EMPTY`、每页 `parsed_record_count > 0`，且总记录和唯一 ASIN 都大于 0 时，manifest 才为 `AUTHORITATIVE`，并更新 latest 指针。manifest 同时记录 `expected_page_count/completed_page_count/parsed_page_count/failed_page_count/empty_page_count` 和 page-level 状态。Challenge、403、429、Blocked、Network Error、Unknown、空解析页等都会产生 `INCOMPLETE` 快照而不更新指针；失败快照仍保留已完成页的 structured records 和全部证据。CLI 生产模式对 `INCOMPLETE` 返回非 0；`--allow-incomplete-debug` 仅供人工分析。

`--rankings-file` 没有原始 source/page manifest 时只生成受限的 `OFFLINE_FROZEN` 证据，不冒充生产 `AUTHORITATIVE`。正式离线冻结必须同时提供 `--source-manifest`，其中包含 planned pages 与 page-level success evidence。

## Detail Planner

`monitoring.detail_planner.build_detail_plan` 是纯离线函数。它只接受带有
`snapshot_status=AUTHORITATIVE` 的快照映射，按 canonical ASIN 去重榜单记录，并输出：

```text
detail_plan.json        # 不可变 object artifact
detail_plan.csv
detail_plan_summary.json
```

动作含义：

| 动作 | 语义 |
| --- | --- |
| `REUSE_VALID_CACHE` | 有效商品页、身份合法、schema 兼容；不请求网络 |
| `FETCH_NEW` | 榜单出现但没有有效详情证据 |
| `REFETCH_INVALID_CACHE` | 空页、错误页或 schema 过期且没有可重解析 HTML |
| `RETRY_TRANSIENT_FAILURE` | timeout、network error 等瞬时失败 |
| `VERIFY_IDENTITY` | 详情 ASIN 与榜单 ASIN 需要复核 |
| `REPARSE_SAVED_HTML` | schema 过期但保存 HTML 足以离线重解析 |
| `BLOCK_ACCESS_STATE` | 历史证据处于 Challenge、403、429 或 Blocked |
| `BLOCK_LINK_IDENTITY` | 榜单链接 ASIN 与强榜单 ASIN 明确冲突 |
| `BLOCK_CODE_FIX` | parser/program/schema 等确定性错误，不能自动重试 |

V1 的 `DetailRefreshPolicy` 默认永不按时间刷新有效缓存。排名改变、tracking 参数改变、掉榜后重新上榜都不会强制重抓。父 ASIN 和明确 variation-family 证据可以进入 `PARENT_ASIN_MATCH` 或 `VARIATION_RELATED`；无关 ASIN 只进入身份复核，不覆盖榜单身份。

详情请求 URL 与 raw href 分离。优先使用最新榜单的规范 URL，其次使用已验证历史 URL，最后才使用 `https://www.amazon.es/dp/{ranking_asin}`；每条计划保留 `preferred_request_url_source`。

`amazon_es_bestseller.identity.resolve_identity()` 是 Planner 与 Collector 共用的 Identity Resolver，输入 ranking/requested/final/canonical/embedded/detail/parent/family 证据，输出身份状态和可审计 evidence。缺少商品链接或 URL 中没有 ASIN 时，只要榜单 ASIN 有独立强证据就走 canonical fallback；只有明确 `LINK_ASIN_MISMATCH` 才阻断。

## Detail Executor 与恢复

`collection.detail_executor.execute_detail_plan` 只传递以下动作给已有串行 `collect_details`：

```text
FETCH_NEW
REFETCH_INVALID_CACHE
RETRY_TRANSIENT_FAILURE
```

它不会为 `REUSE`、`REPARSE`、`VERIFY` 或 `BLOCK` 发请求。`REPARSE_SAVED_HTML` 调用现有 `reparse_saved_details()` 并更新 state；`VERIFY_IDENTITY` 生成 JSON/CSV review queue。详情 collector 在增量 executor 模式只生成 `details_delta.json`，不直接覆盖全量 `details.json`。

`DetailState` 是当前详情权威状态；executor 合并成功 delta、最新成功 checkpoint 和已有 state 后原子保存 state，再从 `state.records()` 原子重建 `details.json`。旧的 `details.json` 只在没有 state 时作为一次性 legacy seed。每次执行保存到 `detail_runs/<run_id>/manifest.json`，根目录的 manifest 只是最新指针。

每个执行 checkpoint 会保留 `snapshot_id`、榜单 ASIN、原始榜单 URL、requested/resolved ASIN、requested URL、action、attempt、access state、final URL、状态和错误。成功 checkpoint 在同一 snapshot/action 下再次执行会被跳过；瞬时失败会在下一次 planner 中进入重试；AccessStop 会立即停止并保留当前证据。

## CLI

```bash
# 离线冻结已有榜单 JSON
amazon-es --offline ranking-snapshot --rankings-file rankings.json --out-dir runtime/ranking_snapshots

# 纯离线生成详情计划
amazon-es --offline detail-plan --snapshot <snapshot-directory> \
  --details outputs/details.json --state outputs/state/details_state.json \
  --checkpoints outputs/checkpoints \
  --html-dir outputs/html --out-dir outputs/detail_plan

# 按计划执行；只有三类 network action 会请求 Amazon
amazon-es detail-run --plan outputs/detail_plan/detail_plan.json --out-dir outputs
```

checkpoint 的正式形态是 `checkpoints/<ASIN>.json` 目录；CLI 仍兼容旧 JSON 文件。网络成功 checkpoint 在 state 尚未保存时可恢复，避免重复请求。

`detail-run --offline` 在计划仍有待执行网络动作时会拒绝运行，避免误把 dry-run 当作采集。

## 兼容性与证据原则

旧 ranking/detail JSON 仍可读取；新字段为增量字段。`bestseller_rank` 与详情 BSR 始终分离。任何无效或 challenge 证据都进入现有 quarantine 流程，历史 HTML、快照和 checkpoint 不删除。V1 不做数据库迁移，不改 Excel 契约，也不执行真实大规模采集。
