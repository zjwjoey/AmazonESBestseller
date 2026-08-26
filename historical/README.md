# historical/ —— 历史一代/二代脚本归档

> 这些是 AmazonESBestseller 第一代（V1）与第二代（V2）的根目录脚本，**已废弃**，
> 由统一主链取代（见 README / docs/ARCHITECTURE.md §59-60）。归档前零改动，
> 仅供审计与追溯；**不要**在新流程中引用或重写。

统一主链（`src/amazon_es_bestseller/`）入口：

```text
python -m amazon_es_bestseller.cli collect --urls <榜单URL...>
python -m amazon_es_bestseller.cli enrich --offline
python -m amazon_es_bestseller.cli qa --offline
python -m amazon_es_bestseller.cli export --offline
```

## 废弃脚本清单

| 脚本 | 职责 | 废弃原因 |
| --- | --- | --- |
| `extract_details.js` | Playwright 批量采集商品详情（标题/价格/评分/评论/库存/BSR/卖家/品牌/变体/是否自营）→ `product_details.json` | 逻辑迁入 `collection/detail.py` + `access/browser.py`（串行 + 显式延迟纪律不变） |
| `build_output.py` | 读 `amazon_es_home_kitchen_bestsellers.csv`（榜单数据）+ `product_details.json` → 合并 | **已知历史错误**：按 `Rank` 构造 `"n.º %s en Hogar y cocina"` 伪造 BSR。生产链永久淘汰（meta-test `test_rank_bsr_meta.py` 钉死）；其产物 `product_details.json` 的 BSR 列导入主链时丢弃 |
| `prep_selection_data.py` | V1 预处理：读源工作簿「提取信息」sheet → `_selected_data.json` | V1 一次性管线，被 V2/主链取代 |
| `make_translations.py` | 用模型能力生成 `_translations.json`（不调用第三方翻译服务） | 本轮主链 `enrich` 接受翻译表 JSON（ASIN → `{title_zh}`）；模型翻译流程待统一入口 |
| `build_selection_workbook.py` | V1 工作簿构建（读 `_selected_data.json` + `_translations.json`） | 被 `export/excel.py` 取代（本轮重写为新 3 表契约） |
| `prep_v2_selection.py` | V2 预处理 + 字段审计：读「选品优化版.xlsx」→ `_v2_selected.json` / `_v2_audit.json` | 归一化逻辑迁入 `normalization/*` + `pipeline.py`；审计迁入 QA 层 |
| `build_v2_workbook.py` | V2 工作簿构建 | 被 `export/excel.py` 取代 |
| `audit_current_v2.py` | 只读审计 round-1 输出工作簿 | 一次性审计脚本，归档留痕 |

## 旧运行顺序（历史，仅追溯）

```text
V1:  extract_details.js → build_output.py → prep_selection_data.py
      → make_translations.py → build_selection_workbook.py
V2:  prep_v2_selection.py → build_v2_workbook.py → audit_current_v2.py
```

## 数据产物

- `product_details.json`（30 条真实记录）：仍用于离线主链回归（`cli enrich --legacy product_details.json`）。
  注意：其中 `BSR` 列是 `build_output.py` 按 `Rank` 构造的历史伪造产物（30/30 为
  `"n.º {Rank} en Hogar y cocina"`），主链导入时**丢弃**，绝不作为 detail BSR 证据。
