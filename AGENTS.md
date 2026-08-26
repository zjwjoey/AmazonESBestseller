# AmazonESBestseller — Agent Development Rules

## 1. Purpose of this file

This file defines the permanent development rules for AI coding agents working on the `AmazonESBestseller` repository.

Applicable agents include, but are not limited to:

* Codex
* Claude Code
* DeepSeek
* Cursor agents
* other automated coding agents

Read this file before modifying code.

This repository already contains a **working Amazon.es bestseller collection workflow and real collected output**.

Do not treat the project as a greenfield prototype.

The primary development principle is:

> Preserve verified working behavior first, then improve it incrementally.

---

# 2. Project purpose

`AmazonESBestseller` collects publicly visible bestseller and product information from:

`https://www.amazon.es`

The long-term goal is to build an internal Amazon Spain bestseller research dataset covering major physical-product categories.

Expected scale:

* several thousand ranking records
* approximately 6,000–10,000 unique product ASINs in later stages

The data is intended for:

* Spanish-market product research
* category research
* price-band research
* bestseller analysis
* specification analysis
* assortment / product selection
* future comparison with other European retailers
* future AI-assisted assortment analysis

This is not intended to become a general-purpose Amazon crawler.

---

# 3. Current project status

The project has already successfully produced real Amazon.es product data.

Verified capabilities include:

* Amazon.es Best Sellers data collection
* ASIN extraction
* bestseller rank collection
* product title extraction
* product URL extraction
* image URL extraction
* current price extraction
* rating and review extraction
* product-detail-page enrichment
* brand extraction
* Parent ASIN extraction for many products
* technical detail extraction
* Amazon BSR extraction
* specification normalization
* first-available-date extraction where present
* Spanish product table generation
* Chinese product table generation
* category planning table generation
* Excel image embedding
* Chinese translation / normalization experiments
* offline data auditing

Existing real outputs must be treated as evidence of working behavior.

Do not assume a feature is absent merely because it is implemented through scripts rather than a polished package structure.

---

# 4. Current development phase

The project has passed the pure reconnaissance stage.

Current phase:

> Stabilize and engineer the already-working collection pipeline.

The next priority is NOT immediately expanding to all Amazon categories.

Priority order:

1. stabilize current collection behavior;
2. freeze business field definitions;
3. improve data-quality validation;
4. convert one-off scripts into reusable modules where justified;
5. add regression tests;
6. validate complete collection of one major category;
7. validate a second major category;
8. expand to additional physical-product categories;
9. only then increase scale toward 6,000–10,000 unique ASINs.

---

# 5. Core development principle

## Never rewrite a working path without evidence that rewriting is necessary.

Agents must prefer:

> small, reversible, testable changes

over:

> large architecture rewrites.

Do not refactor solely because the current implementation is not aesthetically ideal.

A stable script that produces correct data is more valuable than a cleaner architecture that changes behavior.

---

# 6. Mandatory workflow before code changes

Before modifying business logic:

1. read the relevant existing code;
2. identify the current data flow;
3. inspect existing real output if relevant;
4. identify the smallest necessary change;
5. identify possible regression risks;
6. add or update tests where practical;
7. only then modify implementation.

For high-risk changes involving:

* ranking
* ASIN identity
* prices
* category hierarchy
* specifications
* translation
* image association
* Excel export

the agent must explicitly compare old and new behavior.

---

# 7. Do not modify unrelated code

Every task should have a bounded scope.

Do not:

* rewrite unrelated modules;
* rename large portions of the repository unnecessarily;
* introduce a new framework without a requirement;
* replace working libraries merely for style;
* add databases unless explicitly requested;
* add web UI unless explicitly requested;
* introduce distributed workers unless explicitly required;
* add proxy systems;
* add anti-detection systems;
* add CAPTCHA solving;
* add browser fingerprint bypass systems.

Keep the project focused.

---

# 8. Amazon access safety rules

Amazon access must remain conservative.

Allowed principles:

* serial access;
* low frequency;
* one controlled browser session where appropriate;
* explicit delays;
* stop on access restrictions;
* save evidence for abnormal responses.

If any page shows:

* HTTP 403
* HTTP 429
* Robot Check
* CAPTCHA
* access denied
* forced login caused by access restrictions
* other obvious challenge pages

the program should stop or mark the run as restricted according to the current access policy.

Do not implement:

* CAPTCHA bypass
* proxy rotation
* cookie rotation
* account rotation
* browser fingerprint spoofing
* stealth plugins intended to bypass access controls
* automatic CAPTCHA clicking
* aggressive retries

Do not weaken existing access-safety behavior without explicit approval.

---

# 9. ASIN is the primary product identity

The canonical Amazon product identity is:

`ASIN`

Example:

`B078C6QR1C`

ASIN must be preserved throughout:

* ranking collection
* detail enrichment
* deduplication
* translation
* image handling
* Excel export
* historical tracking

Never use:

* product title
* row number
* image URL
* rank

as a replacement for ASIN identity.

---

# 10. Parent ASIN

`parent_asin` identifies variant families where available.

Examples:

* color variants
* size variants
* capacity variants
* package variants

Parent ASIN is useful for backend analysis.

Do not use Parent ASIN as a replacement for child ASIN.

Each purchasable child ASIN must retain its own identity.

If Parent ASIN cannot be confirmed:

> store null / empty.

Do not guess it.

---

# 11. Ranking records and product records are different entities

This distinction is fundamental.

## Ranking record

Represents:

> one ASIN appearing in one Amazon ranking context.

Example:

```text
ASIN BXXXXXXXXX
Home & Kitchen #35
```

and:

```text
ASIN BXXXXXXXXX
Food Storage #8
```

are two valid ranking records.

They are not duplicates.

Therefore:

> Do not deduplicate ranking records solely by ASIN.

---

## Product record

Represents:

> one ASIN and its relatively stable product information.

Examples:

* title
* brand
* product URL
* image URL
* price
* details
* specification
* first-available date

Product records may be deduplicated by ASIN.

---

# 12. Bestseller rank and Detail BSR must never be mixed

There are at least two different ranking concepts.

## `bestseller_rank`

Source:

> Amazon Best Sellers ranking page.

Meaning:

> position on the specific ranking page currently being collected.

Usually values such as:

```text
1
2
3
...
100
```

depending on page depth.

---

## `detail_bsr`

Source:

> Amazon product detail page.

Meaning:

> Amazon Best Sellers Rank displayed in product details.

It may contain values such as:

```text
233
5000
180285
```

These are not the same as ranking-page position.

Never populate:

`bestseller_rank`

using:

`detail_bsr`.

Never label Detail BSR as the ranking-page position.

---

# 13. Internal index is not Amazon rank

An internal row/index number is only:

`index`

It exists for display or local ordering.

Example:

```text
1
2
3
...
```

It must not be treated as an Amazon rank unless the source explicitly confirms that ranking position.

DOM order alone is not sufficient evidence when Amazon does not expose an explicit ranking value.

---

# 14. Category hierarchy rules

Amazon categories should come from actual Amazon evidence such as:

* Best Sellers category navigation
* breadcrumb
* Browse Node
* ranking-page source
* structured page data

Potential hierarchy:

```text
category_l1
category_l2
category_l3
leaf_category
browse_node_id
```

Do not invent category levels.

Do not create a fake leaf category simply because the product title suggests one.

Example:

A coffee-machine cleaning tablet may logically belong to:

`Coffee Machine Cleaning Tablets`

but if Amazon only confirms:

`Coffee Machine Accessories`

then the stored Amazon category must remain:

`Coffee Machine Accessories`

until a real leaf node is discovered.

---

# 15. Never duplicate category values to simulate depth

Do not create:

```text
L2 = Coffee Accessories
L3 = Coffee Accessories
leaf = Coffee Accessories
```

just to fill empty fields.

If a deeper category is unknown:

> leave it empty.

Missing data is preferable to fabricated hierarchy.

---

# 16. Multi-category products

The same ASIN may appear in multiple Amazon ranking categories.

Do NOT store this as:

```text
Home & Kitchen / DIY & Tools
```

inside one category field.

Instead create multiple ranking records:

```text
ASIN × Home & Kitchen ranking
ASIN × DIY & Tools ranking
```

The product itself remains one ASIN.

---

# 17. Browse Node ID

Where available, preserve:

`browse_node_id`

along with:

* category name
* ranking source URL
* ranking position
* collection timestamp

Do not guess Browse Node IDs.

---

# 18. Price definitions are frozen

Price semantics must remain strict.

## `current_price`

Only the current price explicitly displayed on Amazon.

Do not adjust it using:

* coupons
* Prime discounts
* promotional text
* cashback
* voucher values

unless future requirements explicitly change this definition.

---

## `original_price`

Only a clearly displayed struck-through/list price.

Do not manufacture original price from:

* discount percentages
* historical data
* external sites
* assumptions

---

## `discount_rate`

Only calculate when both values exist:

```text
(current_price != null)
AND
(original_price != null)
```

Formula:

```text
(original_price - current_price) / original_price
```

Otherwise:

> null.

---

# 19. Monthly bought field

Amazon may show text such as:

```text
100+ comprados el mes pasado
500+ comprados el mes pasado
1 mil+ comprados el mes pasado
```

Store two fields when available:

`monthly_bought_raw`

Original displayed text.

Example:

```text
1 mil+ comprados el mes pasado
```

and:

`monthly_bought_min`

Parsed lower bound.

Example:

```text
1000
```

This is a lower bound, not exact sales.

Never infer monthly purchases from:

* review count
* rating
* rank
* BSR

---

# 20. Preserve raw evidence

Raw values must be retained wherever practical.

For important transformed fields, prefer:

```text
raw field
+
normalized field
```

Examples:

```text
title_es_raw
title_zh

date_first_available_raw
date_first_available

monthly_bought_raw
monthly_bought_min

details_json
details_summary_zh
```

Do not destroy raw evidence simply because a normalized version exists.

---

# 21. Spanish data is the evidence layer

The Spanish-language source data represents the closest available business evidence from Amazon.es.

Chinese data is a derived business layer.

Therefore:

> Chinese translation or normalization must never overwrite the original Spanish source values.

If Chinese and Spanish values conflict:

1. preserve Spanish raw data;
2. mark the derived field as suspect;
3. investigate;
4. do not silently rewrite the raw record.

---

# 22. Chinese product-name rules

Chinese product names are intended for internal assortment research.

They are NOT literal full-title translations.

Preferred format:

```text
核心商品类型 + 关键规格/数量 + 必要兼容型号
```

Examples:

```text
玻璃保鲜盒 8件套
床垫保护套 90×190×40厘米
咖啡机除垢液 2×250毫升
儿童3格便当盒
Dedica EC680/EC685兼容滤杯手柄
```

---

# 23. Brand and Chinese product name have separate responsibilities

If a reliable brand already exists in the:

`brand`

field,

do not unnecessarily repeat it in:

`title_zh`.

Example:

Brand:

```text
Tatay
```

Preferred Chinese title:

```text
冷切肉保鲜盒 3件套
```

instead of:

```text
Tatay Fresh 冷切肉保鲜盒 3件套
```

---

# 24. Exception: compatibility brands/models must remain

Do not remove a brand/model when it describes compatibility.

Example:

```text
适用于 Dyson V15 的吸尘器支架
```

Here `Dyson V15` is not the product's own brand.

It is compatibility information and must remain.

Likewise:

```text
De'Longhi Dedica EC680/EC685兼容滤杯手柄
```

---

# 25. Chinese titles must not contain unnecessary foreign-language marketing text

Ordinary marketing or descriptive phrases should be translated or removed.

Examples that should normally not remain untranslated:

```text
Wash & Protect
Spot & Stain
FreshStart
Adventure
EasySpray
```

unless they are verified official product series and materially useful for identification.

---

# 26. Allowed Latin text in Chinese titles

Do not blindly remove all Latin characters.

Allowed examples include:

## Model numbers

```text
EC685
G807
MS622718
```

## Interfaces / standards

```text
USB-C
E27
SDS Plus
HSS
HEPA
LED
ABS
PTFE
BPA
TÜV
```

## Compatibility models

```text
Dyson V15
Dreame X20 Pro
```

## Recognizable product ecosystems

```text
Nespresso Original
Dolce Gusto
```

---

# 27. Product-type correctness has higher priority than translation fluency

The most serious translation error is:

> identifying the wrong type of product.

Known historical errors must be treated as regression cases.

Examples previously observed include:

* thermal lunch bag translated as lunch box;
* portafilter translated as coffee tamper;
* mini chainsaw translated as chainsaw lubricant;
* trimmer line translated as cordless grass trimmer;
* cleaning tablets translated as portafilter;
* reusable containers translated as disposable containers.

Any future translation logic must prevent these classes of errors.

---

# 28. Chinese title must agree with image and Spanish title

Where image data is available, use the following consistency rule:

```text
Spanish title
      +
product image
      +
technical details
      ↓
Chinese product type
```

If:

```text
Spanish title ≠ image ≠ details
```

or there is another obvious contradiction:

do not guess.

Mark:

`SOURCE_CONFLICT`

or equivalent QA state.

---

# 29. Brand extraction rules

Do not use:

> first word of product title

as a generic brand fallback.

This previously creates false brands such as ordinary nouns.

Brand should come from reliable evidence such as:

* Amazon brand/byline field
* product details
* known structured field
* clearly identified manufacturer/brand data

If brand cannot be reliably identified:

> leave empty.

Missing brand is better than false brand.

---

# 30. Brand cleaning

Remove presentation labels such as:

```text
Marca:
Visita la tienda de
```

and invisible Unicode characters where safe.

Preserve canonical brand spelling.

Where practical, normalize case consistently.

Example:

```text
KRUPS
Krups
krups
```

should eventually map to a canonical form.

Do not alter brand identity based on assumptions.

---

# 31. Specification field purpose

`specification`

must answer:

> Which exact version/specification is the customer buying?

It is not a dump of every Amazon technical field.

Good examples:

```text
90×190×40厘米
500毫升
2×250毫升
65瓦 / USB-C
18V 4.0Ah / 2块
8件套 / 320–1200毫升
```

---

# 32. Specification precedence

When several Amazon fields conflict, use evidence priority.

Recommended order:

1. current selected variation;
2. exact product title;
3. explicit package description;
4. reliable product-detail specification;
5. generic technical-detail fields.

Do not let a generic field such as:

```text
quantity = 1
```

override an explicit product title such as:

```text
14-piece set
```

---

# 33. Specification type validation

Dimensions must use dimension units.

Examples:

```text
mm
cm
m
```

Capacity must use volume units.

Examples:

```text
ml
L
```

Weight must use mass units.

Examples:

```text
g
kg
```

Invalid mappings such as:

```text
capacity = 30 cm
capacity = 992 g
```

must fail validation.

Do not display them as valid specifications.

---

# 34. Known specification regression cases

Past output exposed errors such as:

```text
9L → 25.4L
30L → 20L
10×15cm → 10×10mm
```

Future parser changes must include regression tests for these patterns or equivalent fixtures.

---

# 35. Details and specification are different

`specification`

answers:

> What variant is this?

`details`

or:

`details_summary`

answers:

> What useful characteristics does this product have?

Useful details may include:

* material
* special features
* waterproof
* washable
* dishwasher safe
* microwave safe
* freezer safe
* certifications
* package structure
* use case
* country of origin where useful

Do not place every detail inside the product title.

---

# 36. First available date

If Amazon provides:

`Date First Available`

preserve:

```text
date_first_available_raw
```

and normalize to:

```text
YYYY-MM-DD
```

where parsing is reliable.

If absent:

> leave null.

Do not substitute first-seen crawler date for Amazon listing date.

---

# 37. Image rules

Image association must be based on ASIN/product record identity.

Do not rely solely on:

> image position = row position.

For exported product tables:

* preserve original `image_url`;
* ensure product URL and image URL belong to the same ASIN record;
* if images are embedded into Excel, maintain one product image per row.

Do not silently reuse a different product's image.

---

# 38. Image quality

When the requirement is to retain original image quality:

* do not recompress;
* do not resample the underlying file;
* adjusting Excel display size is allowed;
* preserve original image bytes where practical.

---

# 39. Excel business output principles

The human-facing workbook should remain readable.

Avoid turning the primary Chinese selection sheet into a 50–100 column database export.

The human workflow primarily needs:

```text
图片
序号
ASIN
商品名称
品牌
当前售价
原价
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

Technical fields belong in backend/raw data where needed.

---

# 40. Human fields must never be overwritten

Fields such as:

```text
选品状态
研究备注
```

belong to human users.

Future exports must preserve existing values by ASIN.

Never overwrite them with blank values during regeneration.

---

# 41. QA is part of the product

A run is not complete merely because an Excel file was generated.

Validation should include, where applicable:

* ASIN uniqueness in product table;
* ranking-record duplicates preserved correctly;
* product link contains expected ASIN;
* image URL association;
* price parsing;
* rank source validity;
* category source validity;
* brand validity;
* specification unit validation;
* Chinese product-type consistency;
* missing-field statistics.

---

# 42. Parser changes require regression tests

Whenever a parsing bug is fixed:

> add a test that reproduces the old failure.

Do not only patch the current data.

Examples:

If:

```text
30L
```

was previously parsed as:

```text
20L
```

create a fixture/test ensuring it cannot regress.

If:

```text
portafiltro
```

was translated as:

```text
压粉器
```

add a title-normalization QA case.

Real production errors are valuable test fixtures.

---

# 43. Tests should prefer offline fixtures

Do not repeatedly access Amazon to test parser logic.

Preferred workflow:

```text
real page captured once
       ↓
saved HTML / JSON fixture
       ↓
offline parser tests
```

Network access should verify integration behavior, not serve as the normal unit-test mechanism.

---

# 44. Do not silently fix missing data by guessing

The following are unacceptable:

```text
missing monthly_bought
→ infer from review count

missing leaf category
→ infer from product title

missing original price
→ estimate from discount

missing rank
→ use row number

missing brand
→ use first title word
```

Missing values should remain missing until real evidence exists.

---

# 45. Configuration over hard-coded local paths

Existing scripts may contain historical absolute paths.

When refactoring reusable functionality, prefer configuration or CLI arguments over new hard-coded paths.

Do not break existing working scripts solely to remove their path constants.

Migration should be incremental.

---

# 46. Historical scripts

Some scripts in the repository are historical experiment or transformation scripts.

Examples may include:

* one-off CSV builders
* workbook migrations
* translation-generation scripts
* audit scripts
* temporary data repair scripts

Do not assume every script belongs to the future production pipeline.

Before deleting or rewriting one:

1. identify what output it produced;
2. determine whether another component replaces it;
3. preserve useful behavior or historical evidence.

---

# 47. No premature architecture expansion

Do not add by default:

* PostgreSQL
* Redis
* Celery
* Kafka
* distributed workers
* microservices
* web dashboard
* cloud deployment
* authentication system

These may be appropriate later.

They are not current requirements unless explicitly requested.

---

# 48. Definition of a good change

A high-quality contribution should:

1. solve the stated problem;
2. preserve verified existing behavior;
3. minimize unrelated changes;
4. add validation for new logic;
5. keep raw evidence;
6. improve reproducibility;
7. reduce future ambiguity;
8. update documentation when behavior or data contracts change.

---

# 49. Definition of an unacceptable change

Do not submit a change that:

* rewrites large working areas without necessity;
* silently changes field semantics;
* mixes Bestseller rank and Detail BSR;
* invents categories;
* guesses missing values;
* destroys raw Spanish data;
* overwrites manual selection notes;
* introduces anti-bot bypass behavior;
* reduces data quality merely to improve fill rate;
* makes the program harder to reproduce;
* modifies unrelated files without reason.

---

# 50. Documentation synchronization

If a code change modifies any frozen business definition, update the relevant documentation.

Examples:

Ranking semantics change:

> update `docs/DATA_MODEL.md`.

QA rule changes:

> update `docs/QA_RULES.md`.

Architecture or workflow changes:

> update `docs/ARCHITECTURE.md`.

Current verified status changes:

> update `docs/CURRENT_STATE.md`.

Do not allow code and documentation to silently diverge.

---

# 51. Current priority for coding agents

Unless the user explicitly changes direction, current priorities are:

1. preserve the already-working crawler;
2. document the real current pipeline;
3. improve regression coverage;
4. stabilize product-name and specification QA;
5. formalize category/ranking evidence;
6. improve missing important fields such as monthly-bought data when technically available;
7. validate repeatable collection on one complete major category;
8. validate a second category;
9. expand only after stability is proven.

---

# 52. Final rule

When uncertain between:

> a clever transformation that fills more fields

and:

> leaving a field empty because evidence is insufficient,

choose:

> **evidence over completeness.**

When uncertain between:

> a large refactor

and:

> a small verified change,

choose:

> **small verified change.**

When uncertain between:

> improving appearance

and:

> preserving data correctness,

choose:

> **data correctness.**
