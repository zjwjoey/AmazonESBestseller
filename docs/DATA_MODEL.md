# AmazonESBestseller — Data Model

Last updated: 2026-08-26

This document defines the canonical data model and the default Excel export contract for `AmazonESBestseller`.

Field semantics in this document should not be changed silently.

## 1. Core model

The project contains at least three distinct concepts:

```text
Product
Ranking Record
Product Attribute
```

Product = one logical record per ASIN.

Ranking Record = one ASIN appearing in one Amazon ranking context.

Product Attribute = one visible product-detail Key/Value fact associated with an ASIN.

This third model is essential for full dynamic product-detail extraction.

## 2. Primary identity

### `asin`

Exact purchasable Amazon product/variation identity. ASIN is the canonical product key.

### `parent_asin`

Confirmed Amazon variation-family parent. Child ASIN remains the canonical SKU identity.

## 3. Product record

Recommended conceptual fields:

```text
asin
parent_asin

title_es_raw
title_zh

brand_raw
brand

current_price
original_price
currency
discount_rate

rating
review_count

monthly_bought_raw
monthly_bought_min

selected_variation_raw
selected_variation_zh
specification_es

image_url
product_url

date_first_available_raw
date_first_available

seller_raw
seller

detail_bsr_raw
detail_bsr_segments
detail_category_trail

first_seen
last_seen
```

Full dynamic details should not be flattened into a permanently fixed list of product columns.

## 4. Product Attribute model

Canonical conceptual shape:

```text
asin
section
label_raw
value_raw
position
source

normalized_key
normalized_value

label_zh
value_zh
```

Unknown/new Amazon fields must still be preservable through `label_raw` + `value_raw`.

## 5. Detail sections

`section` may include values conceptually equivalent to product_overview, technical_details, additional_information, selected_variation, feature_bullets, product_description, a_plus and other_visible_details.

Exact implementation names may vary, but source sections should remain distinguishable.

## 6. Raw detail preservation

The data layer should preserve full publicly visible Key/Value evidence where practical.

Do not require a field to match a predefined list such as material, capacity, dimensions, weight, power or voltage before storing it.

The normalization layer may recognize only some attributes. The raw layer should preserve more.

## 7. Full product details

Conceptual fields:

```text
product_details_raw
product_details_normalized
product_details_zh
```

`product_details_raw` is a lossless or near-lossless representation of all collected structured detail attributes.

`product_details_normalized` is an optional structured normalized representation.

`product_details_zh` is a human-readable Chinese rendering of the collected details. It is a display/derived field, not the raw source of truth.

`detail_category_trail` preserves the visible Amazon detail-page breadcrumb from
root to leaf. When the ranking page exposes only a top-level category, the
normalizer may use this explicit trail only to fill missing product category
depth; existing ranking-page category values remain authoritative.

## 8. Feature bullets

Canonical concepts:

```text
feature_bullets_raw
feature_bullets_zh
```

`feature_bullets_raw` is Amazon About this item / `Acerca de este producto` bullet text.

`feature_bullets_zh` is Chinese business translation.

Feature bullets must remain separate from structured product details.

## 9. Selected variation

Canonical concepts:

```text
selected_variation_raw
selected_variation_zh
```

This is the currently selected SKU option/variation shown by Amazon, for example `30 L`, `Rosa / 900 ml`, `Pack de 2`.

This is a high-priority evidence source for specification resolution.

## 10. Core specification

Canonical concepts:

```text
specification
specification_zh
```

Definition: compact purchasing-specification summary derived from full source evidence.

It is NOT the complete detail record.

Evidence priority: selected variation > exact title > explicit package description > reliable detail attributes > generic technical fields.

## 11. Ranking record model

Recommended fields:

```text
index
asin
category_l1
category_l2
category_l3
leaf_category
browse_node_id
bestseller_rank
monthly_bought_raw
monthly_bought_min
ranking_source_url
collected_at
```

Same ASIN may appear in multiple ranking records.

## 12. Bestseller rank vs Detail BSR

`bestseller_rank` comes from Amazon Best Sellers page.

`detail_bsr_raw` comes from Amazon product detail page.

Never merge these fields.

## 13. Category hierarchy

Canonical concepts:

```text
category_l1
category_l2
category_l3
leaf_category
browse_node_id
```

All category levels must come from actual Amazon evidence. Unknown deeper levels remain null.

## 14. Price fields

`current_price` = Amazon currently displayed purchase price.

`original_price` = explicit struck-through/list price.

`currency` default for Amazon.es = EUR.

`discount_rate` only valid when both price fields exist and are valid.

## 15. Rating and reviews

`rating` is normalized numeric rating.

`review_count` is normalized review count.

Do not infer monthly sales from review count.

## 16. Monthly bought

Preserve `monthly_bought_raw` and `monthly_bought_min`. The parsed value is a lower bound, not exact sales volume.

## 17. Date fields

Preserve `date_first_available_raw` and `date_first_available`.

Do not substitute crawler `first_seen` for Amazon listing date.

## 18. Seller

Preserve `seller_raw` and `seller`.

Seller is included in the default human display.

`配送方式` is not part of the default Excel display contract.

## 19. Human notes

Canonical field: `notes`.

Chinese label: `备注`.

This replaces the older separate concepts `selection_status` and `research_notes`.

Notes are human-owned data and must survive regeneration by ASIN.

# 20. Default Excel Export Contract

When the user requests Excel export without specifying another schema, the exporter MUST use this contract.

Default workbook:

1. `类目规划`
2. `西班牙语选品清单`
3. `中文选品清单`

Product sheets use one SKU = one row.

The full dynamic detail data remains in the data layer and is rendered into fixed display columns rather than expanded into hundreds of columns.

## 21. Chinese product sheet — frozen 26 columns

Exact default order:

```text
01 图片
02 序号
03 ASIN
04 Parent ASIN
05 商品名称（中文）
06 品牌
07 当前售价
08 划线原价
09 折扣率
10 评分
11 评论数
12 月购买量
13 一级类目
14 二级类目
15 三级类目
16 细分类目
17 畅销榜排名
18 当前选中规格 / 变体
19 核心规格（中文）
20 完整商品详情（中文）
21 商品卖点（中文）
22 首次上架日期
23 卖家
24 商品链接
25 图片链接
26 备注
```

This schema is frozen until the user explicitly requests a change.

## 22. Chinese column mapping

| Excel column | Canonical source |
|---|---|
| 图片 | image associated with ASIN |
| 序号 | display index |
| ASIN | asin |
| Parent ASIN | parent_asin |
| 商品名称（中文） | title_zh |
| 品牌 | brand |
| 当前售价 | current_price |
| 划线原价 | original_price |
| 折扣率 | discount_rate |
| 评分 | rating |
| 评论数 | review_count |
| 月购买量 | monthly_bought display |
| 一级类目 | category_l1 |
| 二级类目 | category_l2 |
| 三级类目 | category_l3 |
| 细分类目 | leaf_category |
| 畅销榜排名 | bestseller_rank |
| 当前选中规格 / 变体 | selected_variation_zh/raw |
| 核心规格（中文） | specification_zh |
| 完整商品详情（中文） | product_details_zh |
| 商品卖点（中文） | feature_bullets_zh |
| 首次上架日期 | date_first_available |
| 卖家 | seller |
| 商品链接 | product_url |
| 图片链接 | image_url |
| 备注 | notes |

## 23. Spanish product sheet

The Spanish sheet should be business-equivalent and ASIN-aligned with the Chinese sheet.

Recommended corresponding content:

```text
序号
ASIN
Parent ASIN
商品名称（西语）
品牌
当前售价
划线原价
折扣率
评分
评论数
月购买量
一级类目
二级类目
三级类目
细分类目
畅销榜排名
当前选中规格 / 变体（西语）
核心规格（西语）
完整商品详情（西语原文）
商品卖点（西语原文）
首次上架日期
卖家
商品链接
图片链接
备注
```

The Spanish sheet does not require embedded images by default.

Chinese and Spanish product sheets must use the same ASIN set and deterministic ordering.

## 24. Complete product-detail display

`完整商品详情（中文）` is a rendered human-readable field built from the dynamic detail attributes.

Different SKUs may contain different attribute sets. The column is fixed; its content is dynamic.

## 25. Raw vs display rule

Example:

```text
label_raw = Material
value_raw = Polipropileno
↓
完整商品详情（中文） = 材质：聚丙烯
```

If translation/normalization is wrong, the raw source must remain available for repair.

## 26. Excel is not the canonical raw database

Excel is a business display artifact.

Preferred direction:

```text
structured raw/normalized data → Excel
```

## 27. Missing values

Canonical machine data should use null/empty values when evidence is missing.

Do not fabricate rank, category, brand, original price, monthly bought or details.

## 28. Data contract change policy

Any change to the 26 default Chinese columns, sheet names/order, notes field, rank semantics, category semantics, specification semantics or full-detail semantics requires explicit user approval, documentation update, code/schema update and regression validation.

## Field Closure Result

`audit-fields` returns a read-only result for each automatic display field with
source/raw/canonical/derived/display statuses and evidence. The layers are
`Amazon Source → Raw → Canonical → Derived → Display`; missing values are classified
as `SOURCE_MISSING` (no reliable source), `PARSER_MISSED` (source exists but raw is
empty), `MAPPING_MISSED` (raw exists but canonical is empty), or `DERIVED_MISSING`
(raw/canonical exists but translation, calculation or display is empty). `备注` is
human-owned and preserved by ASIN, not audited as an automatic field.

## 29. Final principle

The data layer should be more complete than the display layer.

The display layer should be more readable than the data layer.

Both must remain traceable by ASIN.
