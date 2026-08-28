# AmazonESBestseller — QA Rules

Last updated: 2026-08-27

This document defines quality-assurance rules for collection, normalization, translation and Excel export.

Primary rule: a non-empty field is not automatically a correct field.

Priority:

```text
correctness > identity consistency > source traceability > completeness > presentation
```

## 1. QA severity

P0 Critical: wrong ASIN mapping, wrong image, wrong product type, Bestseller rank mixed with Detail BSR, wrong price assigned to another SKU, severe source conflict hidden by derived data.

P1 High: wrong specification, false brand, wrong selected variation, reusable → disposable, wrong package quantity.

P2 Medium: missing brand, missing first-available date, missing leaf category.

P3 Low: formatting, capitalization, non-critical translation residue.

## 2. ASIN QA

Accepted product records require a valid ASIN. Product URL should correspond to the same ASIN. ASIN/URL mismatch = P0 FAIL.

## 3. Bilingual mapping QA

Spanish and Chinese rows must match by ASIN. Required consistency: same ASIN, corresponding product URL, corresponding image URL, same product identity and same ranking context where applicable.

## 4. Product uniqueness vs ranking multiplicity

Product table: one ASIN = one product row.

Ranking table: same ASIN may repeat across different ranking contexts.

## 5. Rank QA

`bestseller_rank` must come from Best Sellers ranking context. Detail BSR must remain separate. Mixing these = P0 FAIL. Internal `index` is not automatically Amazon rank.

## 6. Category QA

Category hierarchy must come from Amazon evidence. Do not infer from title/image. Do not duplicate one category into L2/L3/leaf merely to improve fill rate.

## 7. Price QA

`current_price` must be a directly displayed current Amazon price. `original_price` must be explicitly displayed. `discount_rate` only exists when both are valid. Coupons and Prime text must not silently modify canonical price fields.

## 8. Rating / review QA

Rating must normalize to valid 0–5 range. Review counts must handle Spanish thousands separators correctly. Do not interpret review count as monthly sales.

## 9. Monthly-bought QA

Preserve raw text and parse only a lower bound. Do not infer monthly purchases from rating, reviews, rank or BSR.

## 10. Brand QA

Accepted brand sources include Amazon byline, explicit brand field, structured detail and unambiguous manufacturer/brand source.

Forbidden generic fallback: first title word.

Missing brand is better than false brand.

## 11. Product-type translation QA

Historical regression classes that must be protected:

```text
thermal lunch bag ≠ lunch box
reusable container ≠ disposable container
portafilter ≠ tamper
cleaning tablets ≠ portafilter
mini chainsaw ≠ chain lubricant
trimmer line ≠ trimmer machine
```

## 12. Specification QA

Evidence priority: selected variation > exact title > package description > reliable detail attributes > generic technical fields.

Known regressions: `9L → 25.4L`, `30L → 20L`, `10×15cm → 10×10mm`.

These must be permanent tests.

## 13. Unit-type QA

Dimensions: `mm / cm / m`.

Capacity: `ml / L`.

Weight: `g / kg`.

Invalid examples such as `capacity = 30cm` or `capacity = 992g` must fail or be rejected from normalized specification.

## 14. Full-detail extraction QA

The new full-detail collector must be evaluated differently from the older specification-only logic.

If a visible detail Key/Value is successfully collected into the raw detail layer, downstream processing must not silently discard it from the raw store.

Example source fields include Material, Color, Capacity, Weight, Dimensions, Country of Origin and Model.

## 15. Dynamic field QA

The collector must NOT reject a visible Amazon detail field simply because its label is unknown to the normalization code.

Unknown fields should still be preservable as:

```text
section
label_raw
value_raw
```

Normalization may leave `normalized_key = null`, but raw evidence remains.

## 16. Raw detail preservation QA

For every collected raw attribute, verify when practical: ASIN association exists, source section exists, raw label is preserved, raw value is preserved.

Normalization errors must not destroy raw evidence.

## 17. Complete-detail display QA

`完整商品详情（中文）` is derived from the dynamic raw detail set.

QA should check that no obvious high-value raw attributes are silently omitted without rule, translation preserves numbers/units, label/value relationships remain correct, duplicate fields are handled without losing distinct source evidence, and unknown/untranslated fields are represented safely rather than fabricated.

The display field does not need to reproduce every low-value technical artifact verbatim if the raw data remains preserved, but business-relevant detail loss must be explainable.

## 18. Feature-bullet QA

`商品卖点` comes from Amazon About this item / feature bullets.

Do not merge feature bullets into structured product Key/Value details.

Preserve `feature_bullets_raw → feature_bullets_zh`.

## 19. Selected-variation QA

If Amazon shows multiple variants, the current selected variant must be identified correctly. Do not default to the first available option.

## 20. Source-conflict QA

If title, image and details strongly disagree, mark `SOURCE_CONFLICT`. Do not guess.

## 21. Image QA

Image association must be by ASIN/product identity where possible.

Chinese workbook requirement: one product image per row, no shifted image mapping, no unintended recompression where original preservation is required.

Spanish sheet does not require embedded images by default.

## 22. Human notes QA

The only default human field is `备注`.

It must survive regenerated exports by ASIN. Automated processes must not clear it, overwrite it or replace it with QA status.

## 23. Default Excel schema QA

When using the default export contract, automated QA should verify workbook sheets exactly:

1. `类目规划`
2. `西班牙语选品清单`
3. `中文选品清单`

unless the user explicitly requested another workbook.

The Chinese product sheet must use the frozen 26-column order:

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

There is no default `配送方式` column and there are no separate default `选品状态` and `研究备注` columns.

## 24. Spanish/Chinese export alignment

For each product row, `cn.asin == es.asin`. The product sets and ordering should match unless explicitly designed otherwise. Links and image URLs should correspond to the same ASIN.

## 25. Exporter QA

The exporter should not repair upstream business logic silently. If upstream data is invalid, flag it, preserve evidence and fail/warn according to severity.

Do not make Excel formatting code guess brand, category, rank, product type or specification.

## 26. Regression-test policy

Whenever a real production error is discovered: create a minimal fixture, write a failing test, fix implementation, verify test passes, keep fixture permanently.

Prefer offline fixtures.

## 27. Full-detail fixture expectation

Include at least one fixture where Amazon exposes attributes that the normalizer does not recognize.

Expected behavior: raw attribute preserved anyway.

This protects the dynamic full-detail extraction requirement from future regression.

## 28. QA statuses

Recommended: `PASS`, `WARN`, `FAIL`, `SOURCE_CONFLICT`.

## 29. Missing data

Missing is not automatically a failure. Differentiate source does not contain field from source contains field but collector/parser failed.

Do not fake values to improve completion.

## 30. Final QA principle

When choosing between complete but uncertain and incomplete but correct, choose incomplete but correct.

When the normalizer does not recognize a new detail field, preserve the raw field.

When a real bug is discovered, convert it into a permanent regression test.

## 31. Export QA gate

Export runs the full QA pipeline before writing the workbook. Any P0/P1 issue blocks the default export: the workbook is not written and the blocking issues are reported with ASIN + code + message. `--force` bypasses the gate explicitly; the exporter never silently repairs upstream data (§25).

Missing data is still not an automatic failure (§29): the gate only stops on real P0/P1 defects, not on empty-but-missing fields.

### The gate must never fail open

`export --details` and `--rankings` default to `outputs/details.json` and
`outputs/rankings.json`, matching `enrich`/`qa`, so the field-closure half of
the gate runs in the default invocation. Before this was fixed the flags
defaulted to empty and the closure audit was skipped whenever they were
omitted — the same product table exported cleanly without flags and was
blocked with 908 P0/P1 findings when the optional flags were supplied.

When no evidence is reachable the gate degrades **explicitly**: export prints
that the field-closure gate did not run and that only the QA gate was applied.
A missing path that was supplied explicitly remains a hard error, so a typo is
never read as "no evidence available".

## 32. Field Closure QA

The offline `audit-fields` command checks each automatic field through Source → Raw
→ Canonical → Derived → Display. It distinguishes `SOURCE_MISSING`,
`PARSER_MISSED`, `MAPPING_MISSED` and `DERIVED_MISSING`; source-missing fields are
normally informational and do not block export. The audit is read-only and excludes
human-owned `备注`.

`ORIGINAL_PRICE_INVALID` is emitted when an apparent list price is absent,
unparseable, or `original_price <= current_price`; it is not displayed as a valid
struck-through original and no discount is calculated. Unknown raw attributes remain
preserved, and Detail BSR never supplies `bestseller_rank`.

Export also blocks `TRANSLATION_INCOMPLETE` when a source Spanish display field
exists but its Chinese target is empty or still a Spanish sentence. `--force`
is the explicit override and is always reported.

## 33. CLI and pipeline integrity

Each CLI command must have command-specific parsing, deterministic input/output
paths and controlled errors. `repair-cache` reports its cache-repair counters;
`reparse-details` reports only schema-reparse counters and must not depend on
repair-cache locals. Smoke tests may use fake browser/DeepSeek transports, but
the default test suite and CI must never access Amazon, DeepSeek, credentials or
local browser profiles. Multiple reparse directories must deduplicate by ASIN with
documented directory-order precedence, and translation summaries must distinguish
`success`, `partial` and `failed`. `SOURCE_MISSING` remains valid and does not become
a blanket completeness failure.
