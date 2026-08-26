# AmazonESBestseller — Data Model

Last updated: 2026-08-26

This document defines the canonical business data model for the `AmazonESBestseller` project.

The purpose is to ensure that:

* collection;
* enrichment;
* normalization;
* translation;
* QA;
* Excel export;
* future database migration

all use the same field semantics.

Field meanings in this document should not be changed casually.

If a field definition must change, update this document together with the code.

---

# 1. Core modeling principle

The project contains two fundamentally different types of data:

## A. Ranking data

Represents:

> one product appearing in one Amazon ranking context.

## B. Product data

Represents:

> one ASIN and its relatively stable product information.

These must remain separate.

---

# 2. Primary product identity

## `asin`

Type:

`string`

Example:

```text
B078C6QR1C
```

Definition:

> Amazon Standard Identification Number for the exact purchasable product/variation.

Rules:

* required whenever a product is accepted;
* canonical product key;
* exactly one product record per ASIN;
* may appear in multiple ranking records;
* never replace with title, URL, row index or image URL.

---

# 3. Parent ASIN

## `parent_asin`

Type:

`string | null`

Definition:

> Amazon parent variation-family identifier when reliably available.

Use cases:

* color families;
* size families;
* package-size variants;
* capacity variants;
* model variants.

Rules:

* backend field;
* preserve when confirmed;
* do not infer;
* null is valid;
* child ASIN remains the real product identity.

---

# 4. Ranking record identity

A ranking record represents:

```text
ASIN
+
ranking context
+
collection time
```

Recommended logical identity:

```text
asin
+ browse_node_id / ranking_source_url
+ bestseller_rank
+ collected_at
```

Do not deduplicate ranking records by ASIN alone.

---

# 5. Product record identity

A product record represents:

```text
one ASIN
```

Recommended identity:

```text
asin
```

Product data can be enriched over time.

---

# 6. Ranking record model

Recommended canonical fields:

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

Optional future fields may be added only when evidence justifies them.

---

# 7. `index`

Type:

`integer | null`

Definition:

> internal display or local ordering number.

This is NOT automatically an Amazon ranking.

Example:

```text
1
2
3
```

Rules:

* may reset within a ranking page/category;
* used for spreadsheet readability;
* must not substitute for `bestseller_rank`.

---

# 8. `bestseller_rank`

Type:

`integer | null`

Definition:

> explicit position of the product on the specific Amazon Best Sellers ranking page being collected.

Source:

Amazon Best Sellers page.

Example:

```text
1
7
38
100
```

Rules:

* must be tied to ranking context;
* must not come from product-detail BSR;
* must not be invented from DOM order unless the page explicitly confirms that order as rank;
* null if not reliable.

---

# 9. `detail_bsr`

Type:

`integer | string | structured object | null`

Definition:

> Amazon Best Sellers Rank shown on the product detail page.

This is different from `bestseller_rank`.

Example:

```text
233
180285
```

or raw:

```text
n.º 233 en Hogar y cocina
```

Recommended storage:

```text
detail_bsr_raw
detail_bsr_segments
```

Do not expose `detail_bsr` as the main human-facing ranking unless explicitly requested.

---

# 10. `detail_bsr_raw`

Type:

`string | null`

Definition:

> full Amazon detail-page BSR text as captured.

Example:

```text
n.º 233 en Hogar y cocina (Ver el Top 100...)
n.º 4 en Juegos de recipientes
```

Preserve raw text whenever available.

---

# 11. `detail_bsr_segments`

Type:

`array | json | null`

Definition:

Structured interpretation of detail BSR.

Example:

```json
[
  {
    "category": "Hogar y cocina",
    "rank": 233
  },
  {
    "category": "Juegos de recipientes",
    "rank": 4
  }
]
```

This is useful for analysis.

Do not automatically treat these categories as the product's canonical category hierarchy.

---

# 12. Category hierarchy

Canonical fields:

```text
category_l1
category_l2
category_l3
leaf_category
browse_node_id
```

All should represent actual Amazon category evidence.

---

# 13. `category_l1`

Type:

`string | null`

Definition:

> first-level Amazon category relevant to the ranking context.

Example:

```text
Hogar y cocina
```

Chinese business layer may store a translated equivalent separately if needed.

Do not combine multiple categories in one field.

Bad:

```text
家居与厨房 / DIY及工具
```

Instead create multiple ranking records.

---

# 14. `category_l2`

Type:

`string | null`

Definition:

> confirmed second-level category.

Leave null when unavailable.

Do not copy `category_l1` merely to fill the field.

---

# 15. `category_l3`

Type:

`string | null`

Definition:

> confirmed third-level category.

Leave null when unavailable.

---

# 16. `leaf_category`

Type:

`string | null`

Definition:

> confirmed lowest category / Best Sellers node used for the ranking context.

This field is especially important for selection research.

Rules:

* must come from Amazon evidence;
* do not infer from title;
* do not copy L3 just to fill it;
* null is valid.

---

# 17. `browse_node_id`

Type:

`string | null`

Definition:

> Amazon Browse Node identifier for the category/ranking context.

Rules:

* preserve exact value;
* do not guess;
* always associate it with category and ranking source;
* potentially critical for repeatable category crawling.

---

# 18. `ranking_source_url`

Type:

`string | null`

Definition:

> URL of the ranking page that produced this ranking record.

This is mandatory evidence for reliable ranking interpretation.

Example:

```text
https://www.amazon.es/gp/bestsellers/...
```

Prefer normalized URLs when practical.

---

# 19. `collected_at`

Type:

`datetime`

Recommended format:

```text
YYYY-MM-DDTHH:MM:SS
```

Definition:

> time at which the ranking/product data was collected.

Ranking records should always retain collection time.

---

# 20. Monthly bought

Canonical fields:

```text
monthly_bought_raw
monthly_bought_min
```

---

# 21. `monthly_bought_raw`

Type:

`string | null`

Definition:

> exact publicly displayed Amazon monthly-purchase text.

Example:

```text
100+ comprados el mes pasado
1 mil+ comprados el mes pasado
```

Do not translate or alter the raw source field.

---

# 22. `monthly_bought_min`

Type:

`integer | null`

Definition:

> parsed lower bound from `monthly_bought_raw`.

Examples:

```text
100+ → 100
500+ → 500
1 mil+ → 1000
```

This is not exact sales volume.

Never label it as exact monthly sales.

---

# 23. Product data model

Recommended canonical product fields:

```text
asin
parent_asin

title_es_raw
title_zh

brand
brand_raw

current_price
original_price
currency
discount_rate

rating
review_count

image_url
product_url

details_json
details_raw
details_summary_zh

specification
specification_zh

date_first_available
date_first_available_raw

detail_bsr_raw
detail_bsr_segments

first_seen
last_seen
```

Additional technical fields may exist in backend storage.

---

# 24. Spanish title

## `title_es_raw`

Type:

`string`

Definition:

> original Spanish Amazon product title as collected.

Rules:

* preserve source wording;
* do not overwrite with cleaned title;
* considered evidence layer;
* changes across future runs may be tracked.

---

# 25. Chinese title

## `title_zh`

Type:

`string | null`

Definition:

> concise Chinese internal-selection title derived from source evidence.

This is a business-layer field.

It is NOT a literal full translation.

Preferred format:

```text
核心商品类型 + 关键规格/数量 + 必要兼容型号
```

Examples:

```text
儿童3格便当盒
玻璃保鲜盒 12件套
咖啡机除垢液 2×250毫升
SDS Plus混凝土钻头 14×160毫米
Dedica EC680/EC685兼容滤杯手柄
```

---

# 26. Chinese title rules

`title_zh` should:

* clearly identify product type;
* remain concise;
* avoid unnecessary brand duplication;
* preserve essential compatibility/model information;
* translate ordinary foreign-language marketing words;
* retain necessary standards/model codes.

Do not store advertising claims inside the title.

---

# 27. Brand fields

Canonical fields:

```text
brand_raw
brand
```

---

# 28. `brand_raw`

Type:

`string | null`

Definition:

> brand field exactly as collected.

Examples:

```text
Marca: Tatay
Visita la tienda de Bissell
```

Preserve source for traceability.

---

# 29. `brand`

Type:

`string | null`

Definition:

> cleaned canonical brand.

Examples:

```text
Tatay
BISSELL
KRUPS
```

Clean only when evidence is reliable.

Do not use first title word as generic fallback.

---

# 30. Manufacturer

Optional fields:

```text
manufacturer_raw
manufacturer
```

Manufacturer and brand are not always the same.

Do not merge automatically.

---

# 31. Current price

## `current_price`

Type:

`decimal | null`

Definition:

> Amazon's currently displayed purchase price.

Example:

```text
9.99
```

Store numeric value separately from presentation.

---

# 32. Price raw

Optional:

```text
current_price_raw
```

Example:

```text
9,99 €
```

Useful for traceability.

---

# 33. Original price

## `original_price`

Type:

`decimal | null`

Definition:

> explicitly displayed struck-through/list price.

Only capture when directly shown.

Do not reconstruct.

---

# 34. Currency

## `currency`

Type:

`string`

Expected value for Amazon.es:

```text
EUR
```

Do not store currency symbol inside numeric price fields.

---

# 35. Discount rate

## `discount_rate`

Type:

`decimal | null`

Formula:

```text
(original_price - current_price) / original_price
```

Requirements:

```text
original_price > 0
current_price != null
```

Otherwise null.

---

# 36. Coupons and promotions

Potential backend fields:

```text
coupon_raw
prime_price_raw
promotion_raw
deal_raw
```

These must not alter `current_price` unless future business definitions explicitly change.

---

# 37. Rating

## `rating`

Type:

`decimal | null`

Example:

```text
4.6
```

Rating is a secondary research signal.

Do not use it to infer sales.

---

# 38. Review count

## `review_count`

Type:

`integer | null`

Example:

```text
156032
```

Do not infer:

* monthly sales;
* bestseller rank;
* sales volume

from review count.

---

# 39. Product URL

## `product_url`

Type:

`string`

Canonical preferred format:

```text
https://www.amazon.es/dp/{ASIN}
```

Example:

```text
https://www.amazon.es/dp/B078C6QR1C
```

Remove nonessential tracking parameters when normalizing.

---

# 40. Image URL

## `image_url`

Type:

`string | null`

Definition:

> product image source URL associated with the ASIN.

Rules:

* preserve even if workbook also embeds image;
* image is presentation;
* image URL is data.

---

# 41. Embedded image

Embedded images are export/presentation artifacts.

They are not primary product data fields.

Association rule:

```text
image
↔ ASIN
```

Do not rely only on row position.

---

# 42. Raw details

## `details_json`

Type:

`json | null`

Definition:

> structured raw/near-raw technical details collected from Amazon detail page.

This may contain many heterogeneous fields.

Example categories:

* material;
* dimensions;
* capacity;
* model;
* package count;
* special features;
* country;
* certifications;
* power;
* voltage.

Preserve as backend evidence.

---

# 43. `details_raw`

Type:

`string | json | null`

Definition:

> unnormalized source detail content.

Use where needed for audit or parser improvement.

---

# 44. Details summary

## `details_summary_zh`

Type:

`string | null`

Definition:

> concise Chinese business summary based only on factual source data.

Useful information may include:

* material;
* key functions;
* package structure;
* washable;
* waterproof;
* microwave safe;
* freezer safe;
* certifications;
* use case;
* country of origin.

Do not invent marketing claims.

---

# 45. Specification

Canonical source/normalized fields:

```text
specification
specification_zh
```

---

# 46. `specification`

Type:

`string | structured object | null`

Definition:

> normalized purchasing specification.

Purpose:

> identify which version/variant the buyer is actually purchasing.

It should NOT be a complete dump of all technical data.

---

# 47. `specification_zh`

Type:

`string | null`

Definition:

> Chinese presentation form of the normalized specification.

Examples:

```text
90×190×40厘米
500毫升
2×250毫升
18V 4.0Ah / 2块
8件套 / 320–1200毫升
```

---

# 48. Specification source priority

When conflicting values exist:

1. selected variation;
2. exact product title;
3. explicit package description;
4. reliable detail specification;
5. generic technical data.

Do not let low-confidence generic fields override explicit title/variation data.

---

# 49. Dimension fields

If structured storage is added later, recommended:

```text
length
width
height
dimension_unit
```

Do not confuse with:

* capacity;
* weight;
* volume.

---

# 50. Capacity fields

Recommended:

```text
capacity_value
capacity_unit
```

Accepted examples:

```text
500 ml
1 L
```

Do not accept length or mass units.

---

# 51. Weight fields

Recommended:

```text
weight_value
weight_unit
```

Accepted examples:

```text
320 g
2.5 kg
```

---

# 52. Package count

Potential fields:

```text
package_count
container_count
piece_count
set_count
```

Do not force all Amazon quantity fields into one concept.

Examples:

```text
7 containers + 7 lids
```

may be:

```text
14 pieces
```

but not necessarily:

```text
14 products
```

Preserve semantics.

---

# 53. Date first available

Canonical fields:

```text
date_first_available_raw
date_first_available
```

---

# 54. `date_first_available_raw`

Type:

`string | null`

Example:

```text
28 octubre 2023
```

Preserve exact source.

---

# 55. `date_first_available`

Type:

`date | null`

Format:

```text
YYYY-MM-DD
```

Example:

```text
2023-10-28
```

Do not replace missing Amazon listing date with crawler first-seen date.

---

# 56. First seen / last seen

Canonical fields:

```text
first_seen
last_seen
```

Definition:

> when this crawler first/most recently observed the ASIN.

These are crawler lifecycle timestamps.

They are NOT Amazon listing date.

---

# 57. Seller fields

Potential backend fields:

```text
seller
sold_by_amazon
fulfilled_by_amazon
```

These are useful future research fields but not mandatory main-table fields.

---

# 58. Availability

Potential field:

```text
availability_raw
```

Example:

```text
En stock
Agotado temporalmente
```

Do not interpret temporary unavailability as permanent delisting without explicit lifecycle logic.

---

# 59. Chinese business table

Recommended human-facing Chinese fields:

```text
图片
序号
ASIN
商品名称
品牌
当前售价
划线原价
折扣率
评分
月购买量
一级类目
二级类目
三级类目
细分类目
畅销榜排名
规格
商品详情摘要
首次上架日期
商品链接
图片链接
选品状态
研究备注
```

Not every field is required to be non-null.

Correctness is more important than fill rate.

---

# 60. Spanish business table

Recommended Spanish-facing/evidence-oriented fields:

```text
序号
ASIN
商品名称（西语原文）
品牌
当前售价
划线原价
折扣率
评分
月购买量
一级类目
二级类目
三级类目
细分类目
畅销榜排名
规格（西语/原始）
首次上架日期
商品链接
图片链接
```

No embedded image is required unless explicitly requested.

---

# 61. Category planning table

Category planning is not product data.

Recommended fields may include:

```text
category_name_zh
category_name_es
priority
recommendation
notes
```

It is a planning layer.

Do not mix category planning rows into the product table.

---

# 62. Manual fields

Canonical manual fields:

```text
selection_status
research_notes
```

Chinese workbook labels:

```text
选品状态
研究备注
```

---

# 63. `selection_status`

Recommended allowed values:

```text
待评估
重点关注
暂不考虑
已研究
```

Future values may be added deliberately.

---

# 64. `research_notes`

Free-text human field.

Must be preserved across regenerated exports using ASIN matching.

---

# 65. Raw vs normalized rule

For important transformations, prefer:

```text
RAW
↓
NORMALIZED
↓
BUSINESS PRESENTATION
```

Example:

```text
title_es_raw
↓
normalized product type
↓
title_zh
```

Another example:

```text
date_first_available_raw
↓
date_first_available
```

Do not collapse all three layers when traceability matters.

---

# 66. Missing values

Use:

```text
null / empty
```

when evidence is absent.

Never fill missing values with:

```text
待补充
未知
N/A
0
```

inside canonical machine data unless a presentation layer explicitly requires it.

A real numeric zero is different from missing.

---

# 67. Data confidence

Future versions may optionally add:

```text
field_confidence
source_type
qa_status
```

but only if they materially improve quality control.

Do not add dozens of confidence columns to the human-facing workbook.

---

# 68. Recommended QA status

Potential backend field:

```text
qa_status
```

Possible values:

```text
PASS
WARN
FAIL
SOURCE_CONFLICT
```

Meaning:

* `PASS`: usable
* `WARN`: incomplete but not clearly incorrect
* `FAIL`: known invalid derived data
* `SOURCE_CONFLICT`: source fields contradict each other

---

# 69. Product-table uniqueness

Product table:

```text
ASIN unique
```

Expected:

```text
one row per ASIN
```

If duplicates exist:

investigate before export.

---

# 70. Ranking-table multiplicity

Ranking table:

```text
ASIN may repeat
```

Expected:

```text
one row per ASIN × ranking context
```

This is correct behavior.

Do not treat it as duplication error.

---

# 71. Image duplication

Different ASINs may legitimately share:

* the same product image;
* the same parent product image;
* similar URLs.

Therefore:

same image URL ≠ automatic duplicate product.

It is only a QA signal.

---

# 72. Translation does not change identity

Translating or shortening a title must not affect:

* ASIN;
* product URL;
* image URL;
* rank;
* category;
* price.

Chinese text is a derived field only.

---

# 73. Historical compatibility

Legacy fields may currently exist, such as:

```text
price
best_rank
specification_legacy
```

When migrating:

* preserve historical values where useful;
* rename clearly;
* do not silently reinterpret old values.

Example:

```text
best_rank_legacy
```

is preferable to pretending it equals the new canonical `bestseller_rank`.

---

# 74. Future database mapping

This model should map naturally into at least:

## `products`

Primary key:

```text
asin
```

## `ranking_records`

Foreign key:

```text
asin
```

## `product_snapshots`

Optional future table for time-varying product fields.

## `manual_selection`

Optional future table for user-edited research state.

---

# 75. Recommended future product table

Conceptual example:

```text
products
--------
asin PK
parent_asin

title_es_raw
title_zh

brand
brand_raw

product_url
image_url

details_json
specification
specification_zh

date_first_available
date_first_available_raw
```

---

# 76. Recommended future ranking table

```text
ranking_records
---------------
id PK
asin FK

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

---

# 77. Time-varying product fields

Some product fields can change:

* current price;
* original price;
* rating;
* review count;
* seller;
* availability.

Long-term, consider storing these in snapshots rather than overwriting history.

Example future model:

```text
product_snapshots
-----------------
asin
collected_at
current_price
original_price
rating
review_count
seller
availability
```

This is a future design option, not an immediate requirement.

---

# 78. Static vs dynamic fields

Relatively stable:

* ASIN
* Parent ASIN
* product title
* brand
* main image
* first available date
* technical details

Dynamic:

* current price
* original price
* rating
* review count
* seller
* availability
* monthly bought
* ranking position

Do not design future update logic as if all fields were equally static.

---

# 79. Ranking history

Each ranking crawl should append new ranking observations.

Do not overwrite prior ranking records if historical analysis is desired.

Future analyses may include:

* ranking movement;
* new entrants;
* disappearing products;
* category persistence.

---

# 80. Product history

Future product snapshots may enable:

* price changes;
* review growth;
* rating movement;
* availability changes.

Do not implement this prematurely unless requested, but keep the model compatible.

---

# 81. Chinese selection-table goal

The Chinese table should optimize:

> human decision speed.

The main user should be able to understand:

```text
what is it?
what does it look like?
what price?
what category?
what rank?
what specification?
what are the useful features?
how old is the listing?
```

without reading the raw Spanish title.

---

# 82. Backend-data goal

Backend data should optimize:

> traceability and future reprocessing.

It may remain more verbose.

Important raw fields must not be removed merely because they are not currently displayed.

---

# 83. Do not optimize for fill rate alone

A field being non-null does not mean it is correct.

Priority:

```text
correct
>
traceable
>
complete
```

Example:

A blank brand is better than a false brand.

A blank leaf category is better than an invented leaf category.

---

# 84. Data contract change policy

Any change that affects the meaning of:

* `asin`
* `bestseller_rank`
* `detail_bsr`
* category fields
* price fields
* monthly bought
* specification
* Chinese product name
* manual fields

must:

1. be explicitly documented;
2. include migration considerations;
3. include regression validation;
4. avoid silent reinterpretation.

---

# 85. Final canonical principle

Every derived field should answer:

> What source evidence produced this value?

If that question cannot be answered reliably:

> the field should probably remain null or be marked for review.

The data model prioritizes:

> evidence, identity, traceability and correctness over superficial completeness.
