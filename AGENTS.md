# AmazonESBestseller — Agent Development Rules

Last updated: 2026-08-26

## 1. Purpose

This file defines permanent development rules for AI coding agents working on `AmazonESBestseller`.

Read this file before modifying code. This repository already contains a working Amazon.es bestseller collection workflow and real collected output. Do not treat the project as greenfield.

Primary principle:

> Preserve verified working behavior first, then improve it incrementally.

## 2. Project goal

The project collects publicly visible Amazon.es Best Sellers and product-detail information for internal product-selection research.

Long-term target:

- major Amazon.es physical-product categories;
- approximately 6,000–10,000 unique ASINs;
- ranking records with source context;
- full product-detail evidence;
- Spanish/Chinese business output;
- repeatable QA.

## 3. Data Layer and Display Layer are different

This project MUST distinguish:

```text
Amazon source
    ↓
Data Layer
    ↓
Normalization / Translation / QA
    ↓
Display Layer
```

### Data Layer

Goal:

> Preserve as much publicly visible product-detail evidence as possible without losing fields.

The product-detail collector must NOT depend on a fixed specification whitelist. If Amazon exposes a new visible Key/Value attribute that is not yet recognized, preserve it as raw data.

Recommended raw attribute shape:

```text
section
label_raw
value_raw
position
source
```

The data layer may contain Product Overview, Technical Details, Additional Information, selected variation, feature bullets, product description, A+ text where collected, BSR, date first available and other visible Key/Value attributes.

### Display Layer

Goal:

> Let the user understand one SKU quickly.

The default Excel display layer uses a fixed business schema. Do not confuse display fields with the full raw data model.

## 4. Full product-detail extraction rule

The collector should preserve all useful publicly visible detail fields dynamically.

Do NOT design detail collection as only capacity, dimensions, weight, material, power and voltage.

Instead:

```text
Amazon detail page
    ↓
lossless detail collection
    ↓
raw detail store
    ↓
normalization
    ↓
translation
    ↓
display summaries
```

Unknown attributes should remain available in raw form.

## 5. Specification is a derived field

`核心规格` / `specification` is NOT the complete product detail. It is a compact summary derived from the full detail data.

Preferred evidence priority:

1. selected variation;
2. exact product title;
3. explicit package description;
4. reliable detail fields;
5. generic technical fields.

Do not allow generic values such as `quantity=1` to override explicit title/variant evidence.

## 6. Full product details and feature bullets are different

Do not merge these concepts.

Complete product details are structured facts such as material, dimensions, capacity, weight, model, color, package count, voltage, power, compatibility, country of origin, certifications and other visible Key/Value fields.

Feature bullets are Amazon `Acerca de este producto` / About this item bullet content.

## 7. ASIN is the primary product identity

Canonical product identity: `ASIN`.

ASIN must be preserved across ranking collection, detail enrichment, deduplication, translation, image handling, Excel export and future historical tracking.

Never use title, rank, row number or image URL as a replacement for ASIN identity.

## 8. Parent ASIN

`parent_asin` identifies a confirmed variation family where available. Preserve when confirmed, do not infer, child ASIN remains the product identity, and null is valid.

## 9. Ranking records and product records are different

A ranking record represents one ASIN appearing in one Amazon ranking context. Same ASIN may have multiple ranking records. Do not deduplicate ranking records by ASIN alone.

A product record represents one ASIN and its product information. Product table should normally have one row per ASIN.

## 10. Bestseller rank and Detail BSR must never be mixed

`bestseller_rank` comes from Amazon Best Sellers ranking pages.

`detail_bsr` comes from Amazon product detail pages.

Never populate bestseller rank from Detail BSR and never fabricate BSR from ranking-page position.

## 11. Category hierarchy

Use Amazon evidence such as Best Sellers category navigation, breadcrumb, Browse Node, ranking source URL and structured page data.

Canonical concepts:

```text
category_l1
category_l2
category_l3
leaf_category
browse_node_id
```

Do not invent hierarchy or duplicate one category into deeper levels merely to fill cells.

## 12. Price definitions

`current_price` is only the current price explicitly displayed by Amazon.

`original_price` is only a clearly displayed struck-through/list price.

`discount_rate` is calculated only when both current and original price are valid.

Do not reconstruct missing prices. Coupons, Prime discounts and promotional text must not silently change canonical current price.

## 13. Monthly bought

Preserve `monthly_bought_raw` and `monthly_bought_min`. The parsed value is a lower bound, not exact monthly sales.

Never infer monthly bought from ratings, reviews or rank.

## 14. Preserve raw evidence

Important transformations should preserve raw + normalized + business presentation.

Examples:

```text
title_es_raw → title_zh
date_first_available_raw → date_first_available
detail_attributes_raw → product_details_zh
```

Chinese output must never overwrite Spanish/raw evidence.

## 15. Brand rules

Brand must come from reliable evidence such as Amazon byline, explicit brand field, structured detail or reliable manufacturer/brand data.

Forbidden generic fallback: first word of title.

Missing brand is better than false brand.

## 16. Chinese product-name rules

Preferred format:

```text
核心商品类型 + 关键规格/数量 + 必要兼容型号
```

Product-type correctness has higher priority than fluent wording.

Known historical error classes that must not regress include thermal lunch bag → lunch box, reusable container → disposable container, cleaning tablets → portafilter, mini chainsaw → chain lubricant, trimmer line → trimmer machine and portafilter → tamper.

## 17. Specification QA principles

Type-check units.

Dimensions: `mm / cm / m`

Capacity: `ml / L`

Weight: `g / kg`

Known regression cases include `9L → 25.4L`, `30L → 20L`, `10×15cm → 10×10mm`.

## 18. Image rules

Image association must be traceable by product identity.

Preferred flow:

```text
ASIN → image_url → local/original image → Excel row
```

When original quality is required, do not recompress. Display resizing is allowed.

## 19. Default Excel Export Contract

When the user requests Excel export and does not explicitly provide another schema, agents MUST use the frozen project export contract.

Default workbook sheet order:

1. `类目规划`
2. `西班牙语选品清单`
3. `中文选品清单`

Do not silently add, remove, rename or reorder core columns unless the user explicitly requests a schema change.

## 20. Default Chinese export fields

The canonical order is:

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

There is no default `配送方式` field.

The previous separate fields `选品状态` and `研究备注` are replaced by one human field: `备注`.

## 21. Human notes must never be overwritten

`备注` is human-owned business data. On re-collection, re-translation, QA or re-export, preserve existing notes by ASIN. Do not clear or replace them automatically.

## 22. Exporter responsibility

The Excel exporter should primarily format already processed data. It must not become the main place where the program guesses brand, guesses categories, invents rank, identifies product type or repairs ASIN mismatches.

## 23. Parser changes require regression tests

Whenever a real parser/translation bug is fixed: reproduce it with a fixture, write a failing test, fix the implementation, verify the test passes, and keep the fixture.

Prefer saved offline fixtures over repeated live Amazon access.

## 24. Amazon access safety

Keep access conservative. On 403, 429, Robot Check, CAPTCHA, access denied or challenge pages, record the condition and stop according to current access policy.

Do not add CAPTCHA bypass, proxy rotation, cookie rotation, account rotation or stealth bypass systems by default.

## 25. Final rules

When choosing between complete but uncertain and incomplete but correct, choose incomplete but correct.

When choosing between full raw detail preservation and dropping unfamiliar fields, preserve the raw detail.

When choosing between a large refactor and a small verified change, choose the small verified change.

## 26. Closure upgrade (2026-08-27)

- The 150 Hogar + 50 DIY quota is a global-unique-ASIN contract. Selection
  must fail with `QUOTA_UNIQUE_SHORTFALL` instead of emitting a short batch.
- Detail records carry the centralized `CURRENT_DETAIL_SCHEMA_VERSION` marker;
  old records are offline-reparsed from saved HTML before any new request.
- Translation caches are keyed by ASIN plus source hash and schema version.
  Field-level `partial` status is not equivalent to full success, and raw
  Spanish evidence is immutable.
- Export runs field-closure audit and blocks P0/P1 parser, mapping, derived or
  translation-incomplete findings unless `--force` is explicit.

## 26. Field Closure Audit

Field Closure Audit is a formal QA capability for automatic fields in the frozen
Chinese 26-column contract. Trace every field as `Amazon Source → RAW → Canonical →
Derived → Display / Excel`, and classify an empty value as `SOURCE_MISSING`,
`PARSER_MISSED`, `MAPPING_MISSED` or `DERIVED_MISSING`. An empty source is valid and
must not be filled by guessing. Preserve raw evidence, keep `备注` human-owned, and
never use Detail BSR as a fallback for `bestseller_rank`.

## 27. Current production-hardening phase (2026-08-27)

The current milestone is **Pipeline Production Hardening** for the frozen 200-SKU
baseline. Work in this phase is limited to CLI reliability, offline smoke/integration
tests, CI protection, documentation synchronization and the small Run Manifest
observability foundation. Do not treat this phase as a crawler rewrite or a new
collection milestone.

Agents must not start 1,000-SKU collection, full Bricolaje validation, a new
`amazon-es run` orchestrator, a database, concurrency/proxy/CAPTCHA systems or a new
Excel schema in this phase. Preserve the Access Gate, resume behavior, translation
cache hash semantics, QA/Field Closure export gate, raw evidence and the 3-sheet /
26-column contract. CI and default tests must remain offline and must not require
Amazon credentials, DeepSeek credentials or a local browser profile.
