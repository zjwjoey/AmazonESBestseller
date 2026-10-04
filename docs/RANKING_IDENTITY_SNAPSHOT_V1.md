# Ranking Identity Snapshot V1

本模块只处理已保存 Amazon.es 榜单证据中的身份信息：ASIN、商品链接、排名和榜单来源上下文。它不解析标题、价格、品牌或商品详情。

离线入口：

```text
amazon-es --offline ranking-identity-extract \
  --evidence-dir <saved-evidence> --out-dir <extract-output>

amazon-es --offline ranking-identity-snapshot \
  --evidence-dir <saved-evidence> --out-dir <snapshot-root>
```

解析器版本为 `ranking_identity_v1`，正式记录保留 `product_url_raw`，并在有效 ASIN 缺少 href 时确定性生成 `https://www.amazon.es/dp/{ASIN}`。`IDENTITY_CONFLICT` 不会用一个证据覆盖另一个证据。

支持的榜单卡布局包括 `#gridItemRoot`、`.zg-grid-general-faceout`、`[id^='p13n-asin-index-']` 和受限榜单容器内的 `[data-asin]`。`data-client-recs-list`、独立 client-recs JSON 和 ACP JSON 只作为补充身份证据，始终离线解析。

快照目录为 append-only：

```text
<root>/YYYY-MM-DD/identity_snapshot_<timestamp>/
  manifest.json
  identity.json
  identity.csv
  audit.json
  evidence/
```

`IDENTITY_READY` 与 `IDENTITY_COMPLETE` 分开审计。只有唯一有效 ASIN 均有可执行商品链接、没有身份冲突且存在至少一个身份时才 ready；若提供 `expected_count`，只有唯一 ASIN 数量相等时才 complete。未知 expected count 会输出 `IDENTITY_COMPLETENESS_UNKNOWN`，绝不会伪装成 complete；审计同时保存 `expected_count_source`。

初始 HTML、渲染 HTML、client-recs 和 ACP 可以共同提供身份证据，但同一页面通过 `page_instance_id` 聚合，完整性数量只计算一次。补充证据只接受明确 ASIN 字段或 `/dp/`、`/gp/product/`、`/gp/aw/d/` 商品 URL，不会扫描任意十位字符串。

采集器即使保留榜单 HTML 和 `rankings.json`，也会把身份提取结果写入 `identity_status.json`；解析异常明确标记为 `IDENTITY_FAILED`，下游不能把它当作完成的 Identity Snapshot。

Detail Planner 优先使用 identity snapshot 的 `product_url`，旧的 `ranking_product_url_raw`、`ranking_product_url_normalized` 字段仍保留用于兼容历史数据。
