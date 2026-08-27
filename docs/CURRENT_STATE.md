# AmazonESBestseller — Current State

Last updated: 2026-08-27

This document describes the current verified state of the project.

## 1. Current phase

The project has moved beyond pure reconnaissance.

Current phase:

> Pipeline Production Hardening — Round 1 (200-SKU stable baseline freeze).

The crawler has already produced real Amazon.es product data and working Excel outputs.

## 2. Verified capabilities

The following have been demonstrated with real Amazon.es data: Best Sellers page collection, ASIN extraction, ranking extraction, product-detail access, title extraction, product URL, image URL, current price, rating, review count, brand, Parent ASIN for many products, technical detail extraction, Detail BSR, specification normalization, first-available date where present, Spanish Excel, Chinese Excel, embedded images, category planning and offline data auditing.

The project should not be described as a non-working prototype.

## 3. Current sample scale

Existing working datasets contain approximately 193–200 Amazon.es product records depending on output version. A stricter cleaned subset of 100 SKUs has already been used for direct internal selection/review.

**Full-major-category validation (2026-08-26):** Hogar y cocina bestsellers were collected
end-to-end at scale — 19 ranking pages (kitchen top + 18 subcategories), 570 ranking records
→ 496 unique ASINs → 496 detail records → enrich → QA → export. QA result: **0 P0 / 0 P1**
(456 PASS / 40 WARN, all WARN = P2 `BRAND_MISSING`, no SOURCE_CONFLICT). The bilingual
workbook (`选品清单_496.xlsx`) satisfies the 3-sheet / 26-column contract with ASIN-aligned
Spanish/Chinese sheets. Field coverage: current_price 467/496, brand 456/496, image_url
475/496, leaf_category 466/496, browse_node_id 466/496, spec_v2 395/496 (80%),
product_details_zh 469/496, feature_bullets_zh 475/496.

**Repeat-run validation (2026-08-26, same day):** a 2nd run over the same 19 ranking pages
re-collected 570 rankings / re-collected 31 incremental ASINs (incomplete ones) and resumed
the other 465 from saved HTML. QA stayed identical — **0 P0 / 0 P1**, same per-field coverage
— and ASIN set matched 496/496 with **0 field drift** on `bestseller_rank`, `current_price`,
`brand`, `image_url`, `leaf_category`, `browse_node_id`. The re-exported workbook
(`选品清单_496_v2.xlsx`) keeps the 3-sheet / 26-column / bilingual-aligned contract.

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
`section / label_raw / value_raw / position / source` plus feature bullets) is implemented.
`parse_detail_page` collects all visible Key/Value attributes (`product_overview` /
`technical_details` / `additional_information`) plus `feature_bullets_raw` /
`product_description_raw` / `detail_bullets_raw`. `pipeline.normalize_product` renders them
offline into `product_details_es/zh` and `feature_bullets_es/zh` (spanish = evidence layer,
chinese = derived layer via dictionary translation, unknown labels/words stay Spanish —
never fabricated). The export columns `完整商品详情（中文）` (col 20) and `商品卖点（中文）`
(col 21) on the Chinese sheet and `完整商品详情（西语原文）` (col 19) / `商品卖点（西语原文）`
(col 20) on the Spanish sheet are filled from these rendered fields. Missing raw detail still
renders empty — missing is not fabricated and is not an automatic QA failure (QA_RULES §29).
Verified on 2 real products (B008YETL18, B078C6QR1C). Remaining limits: see §26.

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

Monthly-bought raw extraction and lower-bound normalization are implemented for visible
"comprados el mes pasado" evidence. Coverage still depends on whether Amazon exposes the
message for a given SKU; do not infer it from rank, rating or reviews.

## 13. Price state

Current-price extraction is relatively mature. Original/list-price extraction now requires
explicit struck-price evidence (or equivalent currency-only legacy evidence); unit prices are
excluded. Discount rate is calculated only when original price is greater than current price.

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

Offline regression coverage is established for product-type translation, specification parsing,
brand false positives, ranking semantics, category mapping, bilingual row mapping, image
association, access-stop behavior and the default Excel schema. Live tests remain opt-in via
RUN_LIVE=1.

**Hardening update (2026-08-27):** CLI smoke/vertical tests now cover all nine commands,
including fake-browser `collect --rankings-only`, offline `reparse-details` across multiple
directories with duplicate-ASIN precedence, quota uniqueness/shortfall, translation failure
isolation and separate partial counts, the select-quota→enrich→translation→enrich→QA→closure→export
path, and export-gate classifications. The suite does not require Amazon/DeepSeek access or
credentials. GitHub Actions offline CI is defined for Python 3.11 and 3.12. `run_manifest.py`
supplies JSON-only workflow metadata helpers; no `amazon-es run` orchestrator exists yet.

## 23. Export contract testing

Automated tests verify exact sheet names/order, the frozen 26 Chinese columns, Spanish/Chinese
ASIN order, notes preservation and ASIN-based image mapping; the live collection tests remain
opt-in only.

## 24. Current code organization

The repository still contains a mixture of working scripts, experiments, audit scripts, workbook builders, translation scripts, historical data and reports. The actual working runtime path should continue to be documented and gradually clarified.

The active CLI entry is `src/amazon_es_bestseller/cli.py`: online commands are
`collect` and `translate-ds`; offline commands are `select-quota`, `enrich`,
`repair-cache`, `reparse-details`, `audit-detail-cache`, `qa`, `audit-fields` and `export`.

### 2026-08-27 1000-SKU blocker repair

The scale-validation repair is offline-first. `audit-detail-cache` classifies
saved HTML as `VALID_PRODUCT_PAGE`, `CHALLENGE`, or `INVALID_OR_EMPTY`; HTTP
200 validation pages are never treated as normal products. Reparse skips
challenge/invalid evidence, while quarantine copies preserve those files for
review. Product URLs are deterministically derived from valid ASINs, unit
prices cannot become list prices, and detail-bullet key/value rows supplement
table attributes. The 1000-SKU quota config rejects automotive records whose
source L1 is not `Coche y moto`.

Use `audit-detail-cache --quarantine-dir <dir> --state <state.json>` to make
the quarantine and state update reproducible; original evidence is never
deleted.

## 25. Scale status

The project has proven real collection and transformation. It has not yet proven reliable production of 6,000–10,000 unique ASINs.

**Milestone reached (2026-08-26): one full major category validated** — Hogar y cocina,
496 unique ASINs, 0 P0 / 0 P1. Real-scale validation drove three parser/normalizer fixes
(modern review-count `(8.819)` format; RANK_BSR_MIXED false-positive on legitimate same-value
ranks; spec_v2 empty because the modern `attributes` model was not wired into `build_spec_v2`)
plus collection resilience (resume + per-ASIN timeout isolation) and a `details.json`
full-state rebuild so resume never loses cached details.

Repeat-run validation is complete for the bounded Hogar y cocina run. The next meaningful
milestone is validating a second major category (`Bricolaje y herramientas`), followed by
controlled category expansion toward 6,000–10,000.

## 26. Next-round scope and known limitations

Scope explicitly deferred from the 2026-08-26 data-quality round (Phase A + B complete and pushed):

1. **Full-detail extraction (Product Attribute model)** — `section / label_raw / value_raw /
   position / source` plus feature bullets, rendering `完整商品详情（中文）` (col 20) and
   `商品卖点（中文）` (col 21). **Phase 1 (collect + render) is implemented** (2026-08-26):
   `parse_detail_page` collects overview / technical_details / additional_information
   attributes + feature bullets; `pipeline` renders `product_details_es/zh` and
   `feature_bullets_es/zh` offline; exporter fills the Chinese-sheet cols 20/21 and
   Spanish-sheet cols 19/20. **Remaining (deferred):** label/term dictionary coverage is
   incremental (unknown labels/words stay Spanish — never fabricated); `product_description`
   and A+ text are collected as raw evidence but not yet rendered into dedicated columns;
   large-scale validation across a full category is not yet done.
2. **Monthly-bought / original-price coverage** — known data-source limitation. Partial support
   exists (`monthly_bought_min` and `discount_rate` computed when raw present); full coverage
   deferred.
3. **Historical snapshots / database** — not started by design (ROADMAP §39-41).
4. **Access stop gate + QA export gate** — both were flagged as missing in the 2026-08-26
   assessment and are now implemented (2026-08-26): (a) `require_normal_access` raises
   `AccessStopError` on any non-NORMAL access state during `collect_rankings` /
   `collect_details`, preserving the restricted page HTML as evidence and never writing an
   incomplete `rankings.json` / `details.json`; the CLI reports and exits 2. (b) `export`
   runs the full QA pipeline first and refuses to write the workbook while any P0/P1 issue
   exists, unless explicitly `--force` (QA_RULES §31).
5. **Collection resilience (2026-08-26, real-scale failure driven)** — `collect_details` now
   (a) resumes: already-saved non-restricted `html/<ASIN>.html` is re-parsed offline instead
   of re-requested, so a re-run only fills gaps (CAPTCHA evidence on disk still raises
   `AccessStopError`); (b) isolates per-ASIN transient failures (e.g. `Page.goto` timeout):
   the ASIN is recorded as failed and collection continues — no retry, no bypass; and
   (c) `cmd_collect` rebuilds `details.json` from the full `DetailState` cache so an
   incremental resume never overwrites already-cached details.

## 27. Current summary

AmazonESBestseller is already a working Amazon.es bestseller research system, and is now being upgraded so the data layer preserves complete dynamic product-detail evidence while the Excel display layer remains a fixed, readable one-SKU-per-row business view.

## 28. Field Closure Audit (2026-08-26)

The formal offline field-closure audit is implemented. It traces automatic fields
through Source → Raw → Canonical → Derived → Excel and reports
`SOURCE_MISSING`, `PARSER_MISSED`, `MAPPING_MISSED` and `DERIVED_MISSING` with
field-level evidence. Raw full-detail extraction V1 is implemented; normalization,
Chinese rendering and display closure continue to improve. The next milestone is
real full-flow Hogar y cocina closure validation, not broad category expansion.

The 2026-08-27 closure pass adds versioned detail migration, hash-aware
translation caching, field-level translation QA and export-gate enforcement.
