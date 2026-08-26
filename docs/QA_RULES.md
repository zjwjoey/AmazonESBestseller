# AmazonESBestseller — QA Rules

Last updated: 2026-08-26

This document defines the mandatory quality-assurance rules for the `AmazonESBestseller` project.

The goal is not maximum field completion.

The goal is:

> correct, traceable and decision-useful data.

A field being non-empty does not mean it is valid.

A blank value is preferable to a confident-looking wrong value.

---

# 1. QA priority

Quality priority is:

```text
correctness
>
identity consistency
>
source traceability
>
field completeness
>
presentation
```

Do not sacrifice correctness to improve fill rate.

---

# 2. QA severity levels

Use the following severity levels.

## P0 — Critical

Data is misleading or changes the identity/meaning of the product.

Examples:

* wrong product type;
* wrong ASIN mapping;
* wrong image for product;
* Bestseller rank mixed with Detail BSR;
* wrong price assigned to another product;
* source conflict hidden by generated values.

P0 data must not be exported as clean data.

---

## P1 — High

Important field is clearly wrong.

Examples:

* 9L parsed as 25.4L;
* reusable product translated as disposable;
* false brand;
* wrong package quantity;
* wrong selected variation.

P1 data should not enter the strict clean export until fixed.

---

## P2 — Medium

Data is incomplete but not necessarily wrong.

Examples:

* missing brand;
* missing specification;
* missing first-available date;
* missing leaf category.

P2 data may be usable depending on export criteria.

---

## P3 — Low

Presentation or consistency issue.

Examples:

* inconsistent capitalization;
* unnecessary foreign-language marketing phrase;
* unit formatting inconsistency;
* overly long Chinese title.

P3 does not normally block raw data use.

---

# 3. Product identity QA

Every accepted product must have a valid ASIN.

Expected format:

```text
[A-Z0-9]{10}
```

Example:

```text
B078C6QR1C
```

Invalid or missing ASIN:

> FAIL

---

# 4. Product URL QA

Canonical product URL should contain the same ASIN.

Example:

```text
ASIN:
B078C6QR1C

URL:
https://www.amazon.es/dp/B078C6QR1C
```

If URL points to a different ASIN:

> P0 FAIL

---

# 5. Spanish/Chinese bilingual matching QA

For bilingual exports:

Spanish row and Chinese row must match by:

```text
ASIN
```

Required consistency:

* same ASIN;
* same product URL;
* same image URL;
* same product identity;
* same ranking context where applicable.

Do not rely only on row number.

---

# 6. Product table uniqueness

In the product table:

```text
one ASIN = one product row
```

Duplicate ASIN rows require investigation.

Possible causes:

* accidental duplicate;
* multiple ranking contexts incorrectly flattened;
* variant confusion.

Do not silently delete duplicates before understanding them.

---

# 7. Ranking-record multiplicity

In ranking records:

```text
same ASIN may appear multiple times
```

This is valid when the product appears in multiple rankings.

Do NOT flag ASIN repetition alone as duplicate error.

---

# 8. Bestseller rank QA

`bestseller_rank` must represent:

> actual rank on a specific Amazon Best Sellers page.

Valid examples:

```text
1
12
49
100
```

Must be tied to:

* ranking source;
* category context;
* collection time.

---

# 9. Detail BSR QA

Detail-page BSR is separate from Best Sellers page rank.

Values such as:

```text
233
126287
180285
```

may be valid Detail BSR values.

They must not populate:

```text
bestseller_rank
```

Mixing these fields:

> P0 FAIL

---

# 10. Rank source QA

A ranking value without context is incomplete.

Preferred evidence:

```text
asin
bestseller_rank
ranking_source_url
category
browse_node_id
collected_at
```

If rank exists but source context is unknown:

> WARN

Do not invent source context afterward.

---

# 11. Index QA

`index` is not an Amazon rank unless proven.

Do not infer:

```text
row 1 = Amazon #1
row 2 = Amazon #2
```

without evidence.

If DOM order is used only as local order:

store as `index`.

---

# 12. Category hierarchy QA

Category levels must come from actual Amazon evidence.

Do not infer from:

* product title;
* product image;
* Chinese translation;
* internal merchandising logic.

---

# 13. Category duplication QA

Do not simulate hierarchy by copying values.

Invalid:

```text
L2 = 咖啡机配件
L3 = 咖啡机配件
leaf = 咖啡机配件
```

If only one level is confirmed:

store only that level.

---

# 14. Multi-category QA

Do not combine multiple categories into one field using:

```text
/
|
,
```

Example to avoid:

```text
家居与厨房 / DIY及工具
```

Correct representation:

multiple ranking records.

---

# 15. Browse Node QA

If `browse_node_id` exists:

* preserve exact value;
* associate with source URL;
* associate with category context.

If unknown:

leave null.

Do not guess.

---

# 16. Current-price QA

`current_price` must come from a directly displayed current Amazon price.

Validation:

* numeric value > 0;
* correct currency;
* same product/ASIN;
* not per-unit price unless that is explicitly the business definition.

---

# 17. Original-price QA

`original_price` must come from an explicitly displayed struck-through/list price.

Do not reconstruct from:

* promotion percent;
* historical observations;
* external sites;
* estimated discount.

If absent:

null.

---

# 18. Discount QA

Only calculate discount when:

```text
current_price != null
original_price != null
original_price > current_price
```

Formula:

```text
(original_price - current_price) / original_price
```

If original price is absent:

discount must remain null.

---

# 19. Coupon and Prime QA

Coupon or Prime text must not silently modify `current_price`.

Examples:

```text
Cupón del 10%
Precio Prime
Oferta
```

may be stored separately.

Do not merge them into price semantics without a deliberate model change.

---

# 20. Rating QA

Valid rating range:

```text
0 <= rating <= 5
```

If text parsing produces:

```text
45
4,5 de 5 estrellas (3000)
```

normalize carefully.

Do not infer rating from review count.

---

# 21. Review-count QA

Review count must be numeric after locale normalization.

Examples:

```text
3.873 → 3873
12.455 → 12455
```

Be careful with Spanish thousands separators.

Do not interpret them as decimal values.

---

# 22. Monthly-bought QA

If Amazon displays:

```text
100+ comprados el mes pasado
1 mil+ comprados el mes pasado
```

store:

```text
monthly_bought_raw
monthly_bought_min
```

Examples:

```text
100+ → 100
1 mil+ → 1000
```

Do not infer monthly sales from:

* review count;
* BSR;
* rating;
* rank.

---

# 23. Brand QA

Brand must come from reliable evidence.

Accepted sources include:

* Amazon byline;
* brand field;
* structured details;
* manufacturer/brand field when unambiguous.

---

# 24. Forbidden brand fallback

Do not automatically use:

```text
first title word
```

as brand.

This has historically produced false brands from ordinary Spanish nouns.

Known bad-style examples include values equivalent to:

* cleaner;
* barrier;
* arm;
* chimney cap.

A missing brand is better than a false brand.

---

# 25. Brand cleaning QA

Remove display prefixes where safe:

```text
Marca:
Visita la tienda de
```

Also remove:

* zero-width characters;
* invisible Unicode noise;
* leading/trailing spaces.

Do not alter real brand identity.

---

# 26. Brand canonicalization QA

Where practical, normalize case:

```text
Bissell
BISSELL
bissell
```

to one canonical representation.

However, do not change stylized brand spelling without confidence.

---

# 27. Chinese title QA — core requirement

Chinese product name must correctly answer:

> What product is this?

Product-type correctness is more important than linguistic elegance.

---

# 28. Known P0/P1 title regression cases

The following historical error classes must never regress:

```text
thermal lunch bag
≠ lunch box
```

```text
reusable containers
≠ disposable containers
```

```text
portafilter
≠ coffee tamper
```

```text
coffee-machine cleaning tablets
≠ portafilter
```

```text
mini chainsaw
≠ chain lubricant
```

```text
trimmer line
≠ cordless trimmer machine
```

---

# 29. Chinese title source consistency QA

Preferred consistency check:

```text
Spanish title
+
image
+
technical details
→ Chinese product type
```

If these sources contradict each other:

mark:

```text
SOURCE_CONFLICT
```

Do not guess.

---

# 30. Chinese title brand duplication QA

If the product brand is already stored separately:

Chinese title should normally not repeat it.

Example:

Brand:

```text
Tatay
```

Preferred:

```text
冷切肉保鲜盒 3件套
```

Not preferred:

```text
Tatay Fresh 冷切肉保鲜盒 3件套
```

Exceptions exist for compatibility references.

---

# 31. Compatibility-name QA

Keep external brands/models when they identify compatible equipment.

Valid example:

```text
适用于 Dyson V15 的吸尘器支架
```

Here Dyson is not the product brand.

It is compatibility information.

---

# 32. Foreign-language residue QA

Chinese titles should not retain unnecessary foreign-language marketing phrases.

Examples to translate/remove when not essential:

```text
Wash & Protect
Spot & Stain
FreshStart
Adventure
EasySpray
```

---

# 33. Allowed Latin text in Chinese titles

Do not flag these automatically:

* model numbers;
* interfaces;
* standards;
* compatible model names;
* recognized ecosystems.

Examples:

```text
USB-C
E27
SDS Plus
HEPA
HSS
PTFE
ABS
BPA
Dyson V15
Dedica EC685
Nespresso Original
Dolce Gusto
```

---

# 34. Chinese title length QA

Preferred title should remain concise.

General target:

> approximately 15–35 Chinese characters

Longer is acceptable when model/compatibility data is essential.

Amazon-style full marketing titles should be avoided.

---

# 35. Chinese title separator QA

Avoid unnecessary:

```text
|
||
—
```

used to concatenate Amazon marketing sections.

If the title still contains several marketing segments:

WARN.

---

# 36. Specification purpose QA

Specification should answer:

> What version/specification is being purchased?

It should not become a full technical-detail dump.

---

# 37. Specification precedence QA

When several values conflict:

```text
selected variation
>
exact title
>
explicit package description
>
reliable details
>
generic technical field
```

Use higher-confidence evidence.

---

# 38. Quantity regression QA

Known historical failure:

```text
title says 4-piece set
technical quantity says 1
→ output incorrectly became 1 piece
```

This must be prevented.

Generic Amazon quantity fields often mean:

> package reference quantity

not actual set contents.

---

# 39. Quantity interpretation QA

Different concepts must remain distinct:

* number of containers;
* number of lids;
* number of sets;
* number of pieces;
* item package quantity.

Example:

```text
7 containers + 7 lids
```

may reasonably be presented as:

```text
7盒+7盖
```

or:

```text
14件
```

depending on business rules.

Do not blindly label every quantity as product count.

---

# 40. Dimension QA

Dimension units:

```text
mm
cm
m
```

must map to dimension fields.

Example:

```text
10×15cm
```

must not become:

```text
10×10mm
```

Historical regression:

```text
10×15cm → 10×10mm
```

must be covered by tests.

---

# 41. Capacity QA

Capacity must use volume units.

Allowed examples:

```text
ml
L
fl oz
```

Invalid:

```text
capacity = 30cm
capacity = 992g
```

Such mappings:

> FAIL

---

# 42. Weight QA

Weight must use mass units.

Allowed:

```text
g
kg
lb
```

Do not map weight into capacity.

---

# 43. Known capacity regression cases

Historical errors include:

```text
9L → 25.4L
```

and:

```text
30L → 20L
```

These must become permanent regression tests.

---

# 44. Variation QA

When a product page lists multiple options:

```text
20L
30L
40L
```

the parser must identify the currently selected variation.

Do not default to the first option.

---

# 45. Placeholder specification QA

Values such as:

```text
1×1×1cm
```

may be placeholders or invalid technical data.

If inconsistent with title/image/product type:

do not display them as reliable business specifications.

---

# 46. Specification completeness QA

A specification can be valid even if partial.

Example:

```text
500毫升
```

is acceptable.

Do not force unnecessary material/color values into the specification.

Those belong in details.

---

# 47. Details-summary QA

Details summary may include:

* material;
* function;
* compatibility;
* washable;
* waterproof;
* microwave safe;
* dishwasher safe;
* freezer safe;
* certification;
* country of origin;
* key structural feature.

All statements must come from source evidence.

---

# 48. No invented marketing claims

Do not create Chinese claims such as:

```text
高品质
专业级
超耐用
最佳选择
```

unless the business layer intentionally stores quoted marketing copy.

For factual summary:

avoid subjective claims.

---

# 49. First-available-date QA

`date_first_available` must come from Amazon listing information.

Do not use crawler:

```text
first_seen
```

as replacement.

If raw date cannot be parsed reliably:

store raw value and leave normalized field null.

---

# 50. Date normalization QA

Spanish dates such as:

```text
28 octubre 2023
```

should normalize to:

```text
2023-10-28
```

Invalid calendar dates:

> FAIL parsing, preserve raw.

---

# 51. Image-to-ASIN QA

Image must belong to the same ASIN/product record.

Validation sources may include:

* image URL from same source record;
* product-page image extraction;
* product identity mapping.

Do not map images merely by positional order if ASIN mapping exists.

---

# 52. Excel image QA

For Chinese workbook exports requiring images:

* one product row = one product image;
* no shifted image rows;
* no missing anchor mapping;
* no montage unless explicitly requested;
* preserve original image quality where required.

---

# 53. Image duplication QA

Same image URL across multiple ASINs is not automatically wrong.

Possible causes:

* variants;
* same parent listing;
* shared product photo.

Flag for review only if product identities differ materially.

---

# 54. Spanish raw-data preservation QA

Spanish source title/details must not be overwritten by Chinese translations.

Any derived translation error must be fixable without losing source evidence.

---

# 55. Raw vs normalized QA

For important fields, retain both when needed:

```text
raw
+
normalized
```

Examples:

```text
brand_raw / brand
date_first_available_raw / date_first_available
monthly_bought_raw / monthly_bought_min
detail_bsr_raw / detail_bsr_segments
```

---

# 56. Missing-data QA

Missing is not automatically an error.

Examples:

* no list price;
* no monthly-bought text;
* no Parent ASIN;
* no first-available date.

Only label parser failure when the source contained the field and parsing failed.

---

# 57. Fill-rate reporting

Each run should report useful fill rates.

Recommended fields:

* ASIN;
* current price;
* original price;
* brand;
* image URL;
* monthly bought;
* specification;
* first available date;
* category levels;
* Browse Node;
* bestseller rank.

Do not use fill rate alone as quality score.

---

# 58. Source-conflict QA

Use:

```text
SOURCE_CONFLICT
```

when source evidence contradicts itself.

Examples:

* title describes coffee maker;
* image shows descaler;
* details describe cleaning chemical.

Do not automatically choose one source.

---

# 59. QA status model

Recommended values:

```text
PASS
WARN
FAIL
SOURCE_CONFLICT
```

---

# 60. PASS

Use when:

* identity is consistent;
* no known high-severity issue;
* business fields are credible.

Not every optional field needs to be present.

---

# 61. WARN

Use for incomplete but usable records.

Examples:

* missing brand;
* missing specification;
* missing first-available date;
* missing leaf category.

---

# 62. FAIL

Use for known wrong derived data.

Examples:

* false product type;
* invalid spec unit mapping;
* mismatched ASIN/URL;
* false brand;
* wrong price association.

---

# 63. SOURCE_CONFLICT

Use when raw sources disagree enough that automatic resolution is unsafe.

Such records must not enter strict clean export without review.

---

# 64. Strict clean-export eligibility

A strict direct-export SKU should normally have:

* valid ASIN;
* valid product URL;
* correct product type;
* credible brand;
* current price where expected;
* reliable specification;
* category context;
* valid bestseller rank;
* image URL;
* no P0/P1 issue.

Optional fields such as:

* first-available date;
* monthly bought;
* original price

do not have to be present unless required by the specific export.

---

# 65. Relaxed research-export eligibility

A relaxed research SKU may be allowed when:

* identity is correct;
* product name is correct;
* image is correct;
* core ranking context is correct;

even if some secondary fields are missing.

Such records should be marked:

```text
WARN
```

rather than falsely completed.

---

# 66. Human-field preservation QA

Before export regeneration:

match existing human fields by ASIN.

Preserve:

```text
selection_status
research_notes
```

A regenerated workbook must not reset valid human entries.

---

# 67. Regression-test policy

Whenever a real bug is discovered:

1. create a minimal reproducible fixture;
2. write a failing test;
3. fix code;
4. verify the test passes;
5. keep the fixture permanently.

Do not only patch the current output file.

---

# 68. Preferred test source

Use offline fixtures whenever possible.

Examples:

```text
tests/fixtures/
  bestseller_page_sample.html
  product_detail_lunchbag.html
  product_detail_portafilter.html
  product_detail_30l_variant.html
```

This reduces repeated Amazon access.

---

# 69. Title-regression test examples

Suggested semantic assertions:

```text
"bolsa térmica"
→ title_zh contains "保温包"
```

and must not contain:

```text
便当盒
```

---

```text
"portafiltro"
→ contains "滤杯手柄" or equivalent
```

must not become:

```text
压粉器
```

---

```text
"reutilizable"
→ must not become "一次性"
```

---

# 70. Specification-regression test examples

Example:

```text
title/spec source contains 9L
```

Expected:

```text
9升
```

not:

```text
25.4升
```

---

Example:

```text
selected variant = 30L
other options = 20L, 40L
```

Expected:

```text
30升
```

---

Example:

```text
10×15 cm
```

Expected normalized:

```text
10×15厘米
```

---

# 71. Brand-regression test examples

Given title:

```text
Limpiador de...
```

do not infer:

```text
brand = Limpiador
```

---

Given reliable byline:

```text
Visita la tienda de BISSELL
```

expected:

```text
brand = BISSELL
```

---

# 72. Rank-regression tests

A Detail BSR value:

```text
180285
```

must never appear in:

```text
bestseller_rank
```

unless the ranking page itself explicitly says so.

---

# 73. Category-regression tests

Unknown leaf category:

expected:

```text
leaf_category = null
```

not:

```text
leaf_category = category_l3
```

just for completeness.

---

# 74. Price-regression tests

If page shows:

```text
current = 9.99
list = 12.99
```

expected:

```text
current_price = 9.99
original_price = 12.99
discount_rate ≈ 0.2309
```

If list price absent:

```text
original_price = null
discount_rate = null
```

---

# 75. Bilingual export regression tests

For each exported row:

```text
cn.asin == es.asin
```

and:

```text
cn.product_url contains asin
es.product_url contains asin
cn.image_url == es.image_url
```

where both use the same source record.

---

# 76. Excel export regression tests

Verify:

* expected sheet count;
* expected sheet names;
* expected row count;
* no accidental duplicate ASINs;
* image count where required;
* manual columns still exist;
* hyperlinks remain valid;
* formulas do not contain errors.

---

# 77. No silent repair

If QA detects a severe issue:

do not silently replace data using guesses.

Instead:

* mark FAIL;
* preserve raw data;
* log reason;
* allow later repair.

---

# 78. QA output

Each major run should ideally produce a QA summary containing:

```text
total_products
pass_count
warn_count
fail_count
source_conflict_count
```

and important field completeness.

---

# 79. Suggested issue categories

Useful structured issue codes may include:

```text
ASIN_INVALID
URL_ASIN_MISMATCH
IMAGE_ASIN_MISMATCH

TITLE_PRODUCT_TYPE_MISMATCH
TITLE_UNTRANSLATED_TEXT
TITLE_BRAND_DUPLICATION

BRAND_FALSE_POSITIVE
BRAND_MISSING

SPEC_UNIT_MISMATCH
SPEC_VARIANT_MISMATCH
SPEC_QUANTITY_CONFLICT
SPEC_SUSPICIOUS_VALUE

RANK_SOURCE_MISSING
RANK_BSR_MIXED

CATEGORY_DUPLICATED_LEVEL
CATEGORY_UNVERIFIED_LEAF

PRICE_INVALID
SOURCE_CONFLICT
```

Do not expose all issue codes in the human workbook unless useful.

---

# 80. QA and scale

Do not expand collection scale simply because the crawler can fetch more pages.

Before major scale increase:

* high-severity error rate should be low;
* known regression tests should pass;
* export consistency should pass;
* category/ranking semantics should be stable.

---

# 81. Definition of quality success

A high-quality SKU does not mean:

> every field is filled.

It means:

> the fields that are present are correct, traceable and useful.

---

# 82. Final QA principle

When choosing between:

```text
complete but uncertain
```

and:

```text
incomplete but correct
```

choose:

> **incomplete but correct.**

When a real production error is found:

> turn it into a permanent regression test.

When raw source and derived output disagree:

> trust evidence, not convenience.
