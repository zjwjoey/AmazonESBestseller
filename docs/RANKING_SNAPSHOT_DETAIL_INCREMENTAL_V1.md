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

快照目录已存在时直接失败，不覆盖历史证据。只有所有计划来源状态都是成功状态时，manifest 才为 `AUTHORITATIVE`，并更新 latest 指针；Challenge、403、429、Blocked、Network Error、Unknown 等都会产生 `INCOMPLETE` 快照而不更新指针。失败快照仍保留用于审计。

## Detail Planner

`monitoring.detail_planner.build_detail_plan` 是纯离线函数。它只接受带有
`snapshot_status=AUTHORITATIVE` 的快照映射，按 canonical ASIN 去重榜单记录，并输出：

```text
detail_plan.json
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
| `BLOCK_LINK_IDENTITY` | 榜单 href 缺失、无法解析 ASIN 或与榜单 ASIN 不一致 |

V1 的 `DetailRefreshPolicy` 默认永不按时间刷新有效缓存。排名改变、tracking 参数改变、掉榜后重新上榜都不会强制重抓。父 ASIN 和明确 variation-family 证据可以进入 `PARENT_ASIN_MATCH` 或 `VARIATION_RELATED`；无关 ASIN 只进入身份复核，不覆盖榜单身份。

详情请求 URL 与 raw href 分离。优先使用最新榜单的规范 URL，其次使用已验证历史 URL，最后才使用 `https://www.amazon.es/dp/{ranking_asin}`；每条计划保留 `preferred_request_url_source`。

## Detail Executor 与恢复

`collection.detail_executor.execute_detail_plan` 只传递以下动作给已有串行 `collect_details`：

```text
FETCH_NEW
REFETCH_INVALID_CACHE
RETRY_TRANSIENT_FAILURE
```

它不会为 `REUSE`、`REPARSE` 或 `BLOCK` 自行发请求。详情 collector 仍复用现有 Spain Delivery Gate、Access Gate、challenge 检测、HTML 落盘、cache audit、quarantine 和 parser。新增的 `request_urls` 与 `execution_context` 参数只是附加请求/证据元数据，不改变旧调用方式。

每个执行 checkpoint 会保留 `snapshot_id`、榜单 ASIN、原始榜单 URL、requested/resolved ASIN、requested URL、action、attempt、access state、final URL、状态和错误。成功 checkpoint 在同一 snapshot/action 下再次执行会被跳过；瞬时失败会在下一次 planner 中进入重试；AccessStop 会立即停止并保留当前证据。

## CLI

```bash
# 离线冻结已有榜单 JSON
amazon-es --offline ranking-snapshot --rankings-file rankings.json --out-dir runtime/ranking_snapshots

# 纯离线生成详情计划
amazon-es --offline detail-plan --snapshot <snapshot-directory> \
  --details outputs/details.json --state outputs/state/details_state.json \
  --checkpoints outputs/checkpoints.json \
  --html-dir outputs/html --out-dir outputs/detail_plan

# 按计划执行；只有三类 network action 会请求 Amazon
amazon-es detail-run --plan outputs/detail_plan/detail_plan.json --out-dir outputs
```

`detail-run --offline` 在计划仍有待执行网络动作时会拒绝运行，避免误把 dry-run 当作采集。

## 兼容性与证据原则

旧 ranking/detail JSON 仍可读取；新字段为增量字段。`bestseller_rank` 与详情 BSR 始终分离。任何无效或 challenge 证据都进入现有 quarantine 流程，历史 HTML、快照和 checkpoint 不删除。V1 不做数据库迁移，不改 Excel 契约，也不执行真实大规模采集。
