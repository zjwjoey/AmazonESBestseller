# AmazonESBestseller — Current State

Last updated: 2026-08-26

This document describes the current verified state of the project.

## 1. Current phase

The project has moved beyond pure reconnaissance.

Current phase:

> Working Amazon.es collection pipeline + data-quality stabilization + detail/export model upgrade.

The crawler has already produced real Amazon.es product data and working Excel outputs.

## 2. Verified capabilities

The following have been demonstrated with real Amazon.es data: Best Sellers page collection, ASIN extraction, ranking extraction, product-detail access, title extraction, product URL, image URL, current price, rating, review count, brand, Parent ASIN for many products, technical detail extraction, Detail BSR, specification normalization, first-available date where present, Spanish Excel, Chinese Excel, embedded images, category planning and offline data auditing.

The project should not be described as a non-working prototype.

## 3. Current sample scale

Existing working datasets contain approximately 193–200 Amazon.es product records depending on output version. A stricter cleaned subset of 100 SKUs has already been used for direct internal selection/review.

## 4. Newly frozen product-detail direction

The project no longer treats a small predefined `specification` field as equivalent to the complete product detail.

The new direction is:

```text
Amazon detail page
    ↓
dynamic full-detail extraction
    ↓
raw detail preservation
    ↓
normalization / translation
    ↓
display fields
```

The collector should preserve visible Key/Value product attributes dynamically. A newly encountered Amazon detail field should not be discarded simply because it is not in a predefined schema.

## 5. Data Layer vs Display Layer

This distinction is now a frozen design principle.

### Data Layer

Purpose: preserve full evidence and support reprocessing. It may contain dynamic product attributes, raw sections and technical fields that are not shown as individual Excel columns.

### Display Layer

Purpose: let the user understand one SKU quickly. Default product sheets remain horizontal: one SKU = one row. The display layer uses a fixed business schema.

## 6. Complete product-detail sources

The detail collector should eventually preserve, where publicly visible and technically available: Product Overview, Technical Details, Additional Information, selected variation, feature bullets / About this item, Product Description, A+ text where collected, BSR, Date First Available and other visible Key/Value attributes.

Not every product will expose every section. Missing source data is not automatically a parser failure.

**Current status (2026-08-26):** full-detail extraction (Product Attribute model:
`section / label_raw / value_raw / position / source` plus feature bullets) is the designated
next-round scope and is NOT yet implemented. This round the export columns `完整商品详情（中文）`
(col 20) and `商品卖点（中文）` (col 21) are left empty — missing is not fabricated and is not an
automatic QA failure (QA_RULES §29). See §26.

## 7. Specification state

`核心规格` is now formally treated as a compact display summary derived from the complete product detail. It is NOT the source of truth.

Known historical specification errors include `9L → 25.4L`, `30L → 20L`, `10×15cm → 10×10mm`. These require regression protection.

## 8. Known product-title issues

Historical real-output errors include thermal lunch bag → lunch box, reusable container → disposable container, cleaning tablets → portafilter, mini chainsaw → chain lubricant, trimmer line → trimmer machine and portafilter → tamper.

Product-type correctness remains a high-priority QA target.

## 9. Brand state

Brand extraction works but false positives have occurred when ordinary Spanish words were interpreted as brands. Missing brand is better than false brand. Title-first-word fallback should not be used generically.

## 10. Category state

Category data is still incomplete. The project must continue improving L1/L2/L3, leaf category, Browse Node and ranking-source traceability. Unknown category depth should remain null rather than duplicated.

## 11. Ranking semantics

Best Sellers rank and Detail BSR must remain separate permanently.

## 12. Monthly bought state

Current sample data has little or no reliable monthly-bought coverage. This remains a valuable future field. Do not infer it from rank, rating or reviews.

## 13. Price state

Current-price extraction is relatively mature. Original/list-price coverage remains weaker. Discount rate must only be calculated when both prices exist.

## 14. Parent ASIN state

Parent ASIN has been recovered for many products but not all. Absence can mean no visible parent, source does not expose it or parser failed. Do not guess.

## 15. Current Excel strategy

The preferred workbook remains:

1. `类目规划`
2. `西班牙语选品清单`
3. `中文选品清单`

The product sheets remain horizontal: one SKU per row.

The main difference from earlier versions is that the display schema now separates selected variation, core specification, complete product details and feature bullets.

## 16. Default Chinese export schema

The frozen 26-column Chinese product sheet is:

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

Removed from the default display schema: `配送方式`, separate `选品状态`, separate `研究备注`.

The human field is now only `备注`.

## 17. Human notes

`备注` is user-owned business data. Future exports must preserve it by ASIN. Automated collection, translation, QA and regeneration must not overwrite it.

## 18. Full-detail display rule

`完整商品详情（中文）` should represent the translated/organized view of the dynamic data-layer attributes. Different SKUs may contain different attribute sets. The display column is fixed; its content is dynamic.

## 19. Feature-bullet display rule

`商品卖点（中文）` should be derived from Amazon feature bullets / About this item. Do not mix marketing bullets into structured product-detail Key/Value storage.

## 20. Spanish/Chinese relationship

The Spanish and Chinese product sheets should use the same ASIN set, deterministic row mapping, corresponding product URLs and corresponding image URLs. Chinese text is derived data and must not alter Spanish/raw evidence.

## 21. Image state

Embedded Chinese-sheet images have been successfully produced. Current expectation: one product image per Chinese row, Spanish sheet does not require embedded images, original image quality preserved where requested, ASIN-based mapping preferred.

## 22. Test state

Formal regression testing remains one of the weakest engineering areas. High-priority test groups: product-type translation, specification parsing, brand false positives, ranking semantics, category mapping, bilingual row mapping, image association and default Excel schema.

## 23. Export contract testing

Future automated tests should verify exact sheet names/order, Chinese sheet frozen 26 columns, no accidental rename/reorder, Spanish and Chinese ASIN order matches, notes survive regeneration, and image count/mapping is valid.

## 24. Current code organization

The repository still contains a mixture of working scripts, experiments, audit scripts, workbook builders, translation scripts, historical data and reports. The actual working runtime path should continue to be documented and gradually clarified.

## 25. Scale status

The project has proven real collection and transformation. It has not yet proven reliable production of 6,000–10,000 unique ASINs.

The next meaningful milestones remain: protect known regressions, stabilize full-detail extraction, stabilize ranking/category evidence, validate one full major category, repeat-run validation, validate a second major category, then controlled expansion.

## 26. Next-round scope and known limitations

Scope explicitly deferred from the 2026-08-26 data-quality round (Phase A + B complete and pushed):

1. **Full-detail extraction (Product Attribute model)** — `section / label_raw / value_raw /
   position / source` plus feature bullets, rendering `完整商品详情（中文）` (col 20) and
   `商品卖点（中文）` (col 21). NOT yet implemented; until it lands these columns are empty, not
   fabricated (QA_RULES §29).
2. **Monthly-bought / original-price coverage** — known data-source limitation. Partial support
   exists (`monthly_bought_min` and `discount_rate` computed when raw present); full coverage
   deferred.
3. **Historical snapshots / database** — not started by design (ROADMAP §39-41).

## 27. Current summary

AmazonESBestseller is already a working Amazon.es bestseller research system, and is now being upgraded so the data layer preserves complete dynamic product-detail evidence while the Excel display layer remains a fixed, readable one-SKU-per-row business view.
