# AmazonESBestseller — Architecture

Last updated: 2026-08-27

This document describes the intended architecture of the `AmazonESBestseller` project based on the current working implementation and verified outputs.

The architecture should evolve incrementally from the existing working scripts.

It must not be used as justification for unnecessary large rewrites.

---

# 1. Architecture goal

The project should support the following end-to-end workflow:

```text
Amazon.es
   ↓
Best Sellers discovery
   ↓
Ranking collection
   ↓
Global unique selection
   ↓
Detail planning / collection
   ↓
Offline schema reparse
   ↓
Raw evidence storage
   ↓
Normalization
   ↓
Chinese business translation
   ↓
QA
   ↓
Business export
```

The architecture must prioritize:

* correctness;
* traceability;
* recoverability;
* low-risk Amazon access;
* offline reprocessing;
* future scalability.

---

# 2. Core architectural principle

The system should separate:

```text
ONLINE COLLECTION
```

from:

```text
OFFLINE PROCESSING
```

Amazon should only be accessed when actual new source data is required.

Translation, parsing improvements, specification fixes, QA and Excel rebuilding should operate offline whenever possible.

---

# 3. High-level architecture

```text
┌───────────────────────────────┐
│          Amazon.es            │
│                               │
│ Best Sellers + Product Detail │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│      Browser Access Layer     │
│                               │
│ access detection              │
│ navigation                    │
│ conservative rate control     │
│ evidence capture              │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│         Raw Evidence          │
│                               │
│ HTML / JSON / screenshots     │
│ raw product values            │
│ access events                 │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│       Ranking Collector       │
│                               │
│ category context              │
│ ASIN                          │
│ bestseller rank               │
│ source URL                    │
│ monthly bought                │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│       Product Enrichment      │
│                               │
│ product detail                │
│ brand                         │
│ parent ASIN                   │
│ price                         │
│ technical details             │
│ BSR                           │
│ first available               │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│       Normalization Layer     │
│                               │
│ prices                        │
│ brand                         │
│ dimensions                    │
│ capacity                      │
│ package count                 │
│ dates                         │
│ categories                    │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│     Chinese Business Layer    │
│                               │
│ title_zh                      │
│ specification_zh              │
│ details_summary_zh            │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│            QA                 │
│                               │
│ identity                      │
│ translation                   │
│ specification                 │
│ brand                         │
│ ranking                       │
│ category                      │
│ image                         │
└──────────────┬────────────────┘
               │
               ▼
┌───────────────────────────────┐
│          Export Layer         │
│                               │
│ 类目规划                      │
│ 西班牙语选品清单              │
│ 中文选品清单                  │
└───────────────────────────────┘
```

---

# 4. Layer 1 — Browser access

Only the browser-access layer should directly navigate Amazon pages.

Responsibilities:

* open Amazon.es;
* navigate Best Sellers pages;
* navigate selected detail pages;
* measure navigation result;
* detect abnormal access states;
* save raw evidence;
* stop safely when required.

---

# 5. Access states

Recommended canonical states:

```text
NORMAL
BLOCKED
RATE_LIMITED
CHALLENGE
NETWORK_ERROR
UNKNOWN
```

Typical mappings:

```text
HTTP 403
→ BLOCKED
```

```text
HTTP 429
→ RATE_LIMITED
```

```text
Robot Check / CAPTCHA
→ CHALLENGE
```

## HTTP 200 is not proof of a product page

Observed on 2026-08-27 during 1000-SKU scale validation: Amazon.es returned
**HTTP 200 with a challenge body** for 251 of 264 requested detail pages. Those
responses were ~3.5 KB, carried no `productTitle`, and showed the Spanish
continue-shopping challenge prompt, while the 13 genuine product pages were
~2 MB.

Consequences that the access layer must preserve:

* a status code alone never establishes `NORMAL` — the response body must be
  inspected before a page is accepted as product evidence;
* the challenge marker may sit deep in the document, so detection scans the
  whole saved response rather than a fixed prefix;
* challenge bodies must never be parsed into detail records, and cached
  challenge HTML must not be reused as evidence on a later run.

A false CHALLENGE stops collection, which is recoverable. A challenge body
accepted as a product page silently corrupts the dataset, which is not. The
detection therefore fails toward stopping.

---

# 6. Access behavior

The browser layer should remain conservative.

Expected behavior:

* serial navigation;
* low request frequency;
* explicit delays;
* controlled browser context;
* no aggressive retries.

On access restriction:

> preserve evidence and stop according to policy.

---

# 7. Forbidden access architecture

Do not introduce by default:

* CAPTCHA solving;
* proxy rotation;
* IP rotation;
* account rotation;
* cookie rotation;
* fingerprint spoofing;
* stealth bypass;
* automatic challenge solving;
* high concurrency.

The system is intended to operate safely, not to defeat Amazon access controls.

---

# 8. Raw evidence layer

Raw evidence exists so parser logic can improve without repeatedly visiting Amazon.

Recommended run structure:

```text
runs/
└── YYYYMMDD_HHMMSS/
    ├── html/
    ├── screenshots/
    ├── raw/
    ├── parsed/
    ├── failures/
    └── logs/
```

This structure already exists conceptually in the earlier reconnaissance design and remains a useful target.

---

# 9. Immutable run principle

Each collection run should ideally create a new run directory.

Do not overwrite the previous run.

Benefits:

* debugging;
* comparison;
* regression analysis;
* reproducibility;
* historical inspection.

---

# 10. Raw Amazon values

Raw values should be preserved before transformation.

Examples:

```text
title_es_raw
current_price_raw
original_price_raw
brand_raw
monthly_bought_raw
details_raw
details_json
date_first_available_raw
detail_bsr_raw
```

Normalization should create new fields rather than destroy source values.

---

# 11. Ranking collection layer

The ranking collector operates on Amazon Best Sellers pages.

Its purpose is not to build full product profiles.

Its primary job is:

> identify products and preserve ranking context.

---

# 12. Ranking collector output

Canonical output:

```text
ranking_records
```

Each row represents:

```text
1 ASIN
×
1 Amazon ranking context
```

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

---

# 13. Ranking-record rule

Do not deduplicate:

```text
same ASIN
```

across different ranking contexts.

Example:

```text
ASIN X
Home & Kitchen #35
```

and:

```text
ASIN X
Food Storage #8
```

must both remain.

---

# 14. Ranking collection should avoid unnecessary detail access

Best Sellers pages should discover most candidate ASINs first.

Recommended flow:

```text
Best Sellers pages
      ↓
ranking_records
      ↓
unique ASIN set
```

Only after that should selected ASINs enter detail enrichment.

---

# 15. Category discovery

Category discovery should use real Amazon category navigation.

Possible evidence:

* Best Sellers sidebar/navigation;
* breadcrumb;
* Browse Node;
* source URL;
* Amazon structured data.

Do not infer category hierarchy from product title.

---

# 16. Category tree

Conceptual category model:

```text
CategoryNode
------------
name_es
name_zh
parent
depth
browse_node_id
url
source
```

Not every level must exist.

---

# 17. Category planning vs Amazon category tree

These are different concepts.

## Amazon category tree

Source:

Amazon.

Represents actual marketplace structure.

## Category planning

Source:

internal research decisions.

Represents:

* which categories matter;
* priority;
* recommended collection order.

Do not merge them into one model.

---

# 18. Product-detail enrichment

Detail enrichment takes:

```text
unique ASINs
```

as input.

It should not require manually editing the crawler script for every batch in the final architecture.

Current hard-coded ASIN experiments should eventually be replaced by structured input.

---

# 19. Detail planner

A future reusable component may be:

```text
detail_planner
```

Responsibilities:

* receive unique ASINs;
* determine which need detail enrichment;
* avoid duplicate requests;
* support sampling or bounded batches;
* preserve enrichment status.

---

# 20. Detail collector

Conceptual component:

```text
detail_collector
```

Responsibilities:

* navigate canonical product URL;
* verify ASIN;
* detect access restriction;
* collect detail-page evidence;
* produce raw detail data.

---

# 21. Detail fields

Potential output includes:

```text
asin
parent_asin

title_es_raw

current_price_raw
original_price_raw

rating_raw
review_count_raw

brand_raw
seller_raw

availability_raw

selected_variation_raw

details_json

date_first_available_raw

detail_bsr_raw

image_url
product_url
```

Not every field is guaranteed on every page.

---

# 22. Product aggregation

Ranking data and detail data should meet through:

```text
ASIN
```

Conceptually:

```text
ranking_records
      │
      └── asin
            │
            ▼
         products
```

---

# 23. Product model

`products` should contain one logical row per ASIN.

Typical fields:

```text
asin
parent_asin

title_es_raw
brand

product_url
image_url

specification
details_json

date_first_available
```

Dynamic values may later move into snapshots.

---

# 24. Dynamic product fields

Fields such as:

* price;
* rating;
* reviews;
* seller;
* availability

can change over time.

Long-term architecture may separate them into:

```text
product_snapshots
```

but this is not required immediately.

---

# 25. Snapshot architecture — future option

Potential future model:

```text
products
ranking_records
product_snapshots
manual_selection
```

Where:

```text
products
```

stores relatively stable identity/details.

```text
product_snapshots
```

stores changing values.

Do not implement until needed.

---

# 26. Normalization layer

Normalization must be fully offline.

It receives raw collected values and produces canonical values.

Examples:

```text
"9,99 €"
→ current_price = 9.99
```

```text
"28 octubre 2023"
→ 2023-10-28
```

```text
"Acero inoxidable"
→ 不锈钢
```

---

# 27. Recommended normalization modules

Future code may be organized conceptually as:

```text
normalization/
├── price.py
├── brand.py
├── category.py
├── specification.py
├── date.py
├── monthly_bought.py
└── text.py
```

This is guidance, not a requirement for an immediate rewrite.

---

# 28. Brand normalization

Flow:

```text
brand_raw
↓
remove Amazon UI prefix
↓
Unicode cleanup
↓
validation
↓
brand
```

Do not use title-first-word fallback.

---

# 29. Price normalization

Flow:

```text
price raw text
↓
locale parsing
↓
numeric value
↓
currency
```

Current and original price remain separate.

---

# 30. Specification normalization

Specification should combine only useful purchase-variant evidence.

Conceptual evidence priority:

```text
selected variation
        ↓
exact title
        ↓
package description
        ↓
technical details
```

Result:

```text
specification
```

---

# 31. Specification normalization must be type-aware

Do not treat all numbers equally.

Separate concepts:

```text
dimensions
capacity
weight
quantity
power
voltage
compatibility
```

A value with `cm` cannot become capacity.

A value with `kg` cannot become volume.

---

# 32. Translation layer

Chinese translation belongs after raw data capture and normalization.

The translation layer should never change:

* ASIN;
* price;
* rank;
* source URL;
* category evidence;
* image URL.

---

# 33. Chinese business transformation

Conceptual flow:

```text
title_es_raw
+
brand
+
normalized specification
+
details
+
product image / QA evidence
        ↓
title_zh
```

---

# 34. Chinese title generation

Preferred output:

```text
核心商品类型
+
关键规格/数量
+
必要兼容型号
```

Do not copy full Amazon SEO title.

---

# 35. Translation dictionary

Reusable deterministic dictionaries are appropriate for:

* units;
* materials;
* colors;
* common Amazon attributes;
* technical terminology.

These should gradually replace unnecessary manual repetition.

---

# 36. ASIN-specific mappings

Current ASIN-specific translation maps are acceptable as:

> historical quality overrides / temporary exception data.

They should not become the primary long-term translation architecture.

---

# 37. Exception layer

A useful future pattern may be:

```text
automatic translation
        ↓
QA
        ↓
exception override
```

instead of:

```text
ASIN
→ fully hard-coded translation
```

This allows scalable automation while retaining manual corrections.

---

# 38. QA layer

QA must run after normalization and translation.

It should validate both:

```text
raw → normalized
```

and:

```text
Spanish → Chinese
```

consistency.

## Field Closure Audit

The formal closure diagnostic sits after normalization/translation and before the
display consumer without rewriting the existing pipeline:

```text
Amazon Source → Raw → Canonical → Derived → QA / Field Closure → Display / Excel
```

`audit-fields` reads products, optional raw detail/ranking JSON and saved HTML, then
emits deterministic JSON and Markdown. It distinguishes absent source from parser,
mapping and derived loss and does not add concurrency or alter the three-sheet export.

---

# 39. QA responsibilities

QA should include:

* ASIN validity;
* ASIN/URL match;
* Spanish/Chinese row match;
* image association;
* ranking semantics;
* category evidence;
* brand validity;
* specification-unit validation;
* translation product-type validation;
* missing-field statistics.

---

# 40. QA output

Conceptual output:

```text
qa_status
qa_issues
```

Possible status:

```text
PASS
WARN
FAIL
SOURCE_CONFLICT
```

---

# 41. Strict export gate

For a strict clean export:

```text
FAIL
SOURCE_CONFLICT
```

records should normally be excluded.

`WARN` may be included according to export policy.

---

# 42. Export layer

The export layer should consume already-normalized and QA-evaluated data.

It should not contain major business inference logic.

Bad architecture:

```text
Excel writer guesses brand
Excel writer guesses categories
Excel writer decides product type
```

Good architecture:

```text
normalized data
+
translated data
+
QA status
↓
Excel writer
```

---

# 43. Current preferred workbook

Current simplified business workbook:

```text
AmazonES workbook
├── 类目规划
├── 西班牙语选品清单
└── 中文选品清单
```

---

# 44. Category planning sheet

Internal planning layer.

Purpose:

* category priority;
* Spanish name;
* Chinese name;
* recommendations;
* notes.

No product rows should be stored here.

---

# 45. Spanish product sheet

Purpose:

> human-readable source-oriented business view.

Should preserve source-oriented information.

Embedded images are not currently required.

---

# 46. Chinese product sheet

Purpose:

> internal selection workflow.

Includes:

* Chinese product title;
* normalized Chinese specification;
* product image;
* research fields.

---

# 47. Image export architecture

Images should remain associated through product identity.

Recommended flow:

```text
ASIN
↓
image_url / local image record
↓
Excel row
```

not:

```text
image #17
↓
Excel row #17
```

without identity verification.

---

# 48. Original image preservation

If export requires original image quality:

```text
downloaded/original bytes
↓
embed directly
```

Do not recompress simply to create Excel thumbnails.

Display dimensions may be adjusted independently.

---

# 49. Manual selection data

Human-generated fields should be treated as a separate ownership layer.

Conceptually:

```text
manual_selection
----------------
asin
selection_status
research_notes
```

Exports should merge this data by ASIN.

---

# 50. Human-data preservation

Regeneration process:

```text
new automated product data
+
existing manual selection data by ASIN
↓
new workbook
```

Do not overwrite manual data.

---

# 51. Offline-first parser development

Parser improvements should use saved fixtures.

Recommended:

```text
Amazon real page
      ↓
save once
      ↓
tests/fixtures/
      ↓
offline parser development
```

Benefits:

* lower Amazon traffic;
* repeatable tests;
* easier regression detection.

---

# 52. Testing architecture

Recommended layers:

```text
Unit tests
    ↓
Parser fixture tests
    ↓
Integration tests
    ↓
Bounded real Amazon validation
```

Do not use real Amazon pages as ordinary unit tests.

---

# 53. Unit-test scope

Useful unit tests:

* price parsing;
* Spanish number parsing;
* date parsing;
* brand cleanup;
* monthly-bought parsing;
* specification parsing;
* translation QA.

---

# 54. Fixture-test scope

Useful fixtures:

* Best Sellers page;
* category page;
* product page with variants;
* product page with list price;
* product page without price;
* known translation edge cases;
* known specification edge cases.

---

# 55. Integration testing

Integration tests should verify pipeline boundaries.

Example:

```text
saved ranking HTML
↓
ranking parser
↓
ranking_records
```

Another:

```text
saved detail HTML
↓
detail parser
↓
raw product
↓
normalizer
↓
QA
```

---

# 56. Real Amazon validation

Real Amazon tests should remain:

* bounded;
* low-frequency;
* intentional.

Use them to verify:

> selectors and real-world access still work.

Do not run unnecessary live tests for every code edit.

---

# 57. Configuration architecture

Reusable modules should eventually read behavior from configuration.

Examples:

```text
marketplace
output_root
page_delay
category limits
detail limits
image settings
export settings
```

Avoid introducing new hard-coded machine paths.

---

# 58. Existing hard-coded paths

Existing working scripts may contain paths such as:

```text
E:\amazon_es\...
```

These should be migrated gradually.

Do not break working behavior only to remove a hard-coded path.

---

# 59. CLI architecture

A unified CLI is implemented and is the supported runtime entry point.

Implemented commands, grouped by whether they reach an external service:

```text
online   collect          Best Sellers + detail pages (serial, explicit delay)
         translate-ds     DeepSeek display-field translation (explicit YES required)

offline  select-quota     choose the globally unique ASIN quota from rankings
         enrich           rankings + details → normalized product table
         repair-cache     backfill canonical fields from saved detail HTML
         reparse-details  rebuild raw details under the current detail schema
         audit-detail-cache  classify saved HTML as valid / challenge / invalid
         qa               product table → QA results
         audit-fields     Source → Raw → Canonical → Derived → Excel closure audit
         export           product table → 3-sheet Excel workbook
```

`--offline` is a global flag; `collect` and `translate-ds` reject it.

There is no `normalize` command. Normalization runs inside `enrich`; the
`normalize` name appears only in the aspirational flow in §60.

These commands should only be documented as executable when actually implemented.

---

# 60. Possible future flow

Example future workflow:

```text
amazon-es collect
        ↓
ranking records

amazon-es enrich
        ↓
product details

amazon-es normalize
        ↓
normalized dataset

amazon-es qa
        ↓
QA results

amazon-es export
        ↓
Excel
```

Again:

> target architecture, not proof of current commands.

---

# 61. Code organization target

A gradual target may be:

```text
src/amazon_es_bestseller/
├── access/
│   ├── detector.py
│   └── browser.py
│
├── collection/
│   ├── category.py
│   ├── ranking.py
│   └── detail.py
│
├── normalization/
│   ├── brand.py
│   ├── price.py
│   ├── specification.py
│   └── dates.py
│
├── translation/
│   └── zh.py
│
├── qa/
│   └── validators.py
│
├── export/
│   └── excel.py
│
└── models.py
```

Do not migrate everything at once.

---

# 62. Historical scripts

Current root-level scripts may represent:

* live collection;
* experiments;
* data repair;
* workbook migration;
* auditing.

Before moving them:

classify them.

Suggested future organization:

```text
scripts/
├── active/
├── audit/
├── migration/
└── historical/
```

but only when doing so does not disrupt current usage.

---

# 63. Reports and historical artifacts

Reports are useful evidence but should not dominate repository root.

Potential structure:

```text
docs/history/
```

for completed one-time reports.

---

# 64. Generated outputs

Generated files should ideally not be treated as source code.

Examples:

```text
CSV
JSON
XLSX
images
screenshots
runs
```

Large generated artifacts should normally be ignored or stored outside Git unless intentionally preserved as fixtures or reference samples.

---

# 65. Test fixtures are different from production outputs

Small curated fixtures should be version-controlled.

Example:

```text
tests/fixtures/product_30l.html
```

A full 5GB run archive should not.

---

# 66. Error handling

Collection failures must not silently become valid empty data.

Example:

```text
page blocked
```

is different from:

```text
page loaded normally but brand absent
```

Preserve this distinction.

---

# 67. Access failure propagation

If access state is not normal:

do not parse the challenge page as if it were a product page.

Expected:

```text
access state
→ failure record
```

not:

```text
captcha page
→ empty product fields
→ normal SKU
```

---

# 68. Parsing failure

A parser failing on one field should normally:

* preserve raw page;
* set field null;
* record issue;
* continue processing other safe fields.

One missing attribute should not necessarily abort the entire product.

---

# 69. Source conflict

If:

```text
title
image
details
```

strongly disagree:

do not resolve inside normalization using guesses.

Send to QA:

```text
SOURCE_CONFLICT
```

---

# 70. Data flow ownership

Each layer should own only its own responsibility.

## Browser layer

Owns:

> access.

## Parser

Owns:

> extraction.

## Normalizer

Owns:

> canonical values.

## Translator

Owns:

> Chinese business text.

## QA

Owns:

> validity decisions.

## Exporter

Owns:

> presentation.

This separation should guide refactoring.

---

# 71. What the exporter must not do

The exporter should not:

* infer Amazon categories;
* decide brand from title;
* calculate fake rank;
* perform major translation;
* repair ASIN mismatches.

Those belong upstream.

---

# 72. What the translator must not do

Translator must not modify:

* ASIN;
* category;
* rank;
* price;
* URLs.

It only generates derived language fields.

---

# 73. What the normalizer must not do

Normalizer must not invent missing source evidence.

Example:

```text
missing original_price
```

must not become estimated price.

---

# 74. What the collector must not do

Collector should not perform Chinese business translation during page navigation.

Keep live page interaction minimal.

---

# 75. Scale architecture

Future scale should come primarily from:

* category planning;
* resumable runs;
* deduplicated detail enrichment;
* offline processing;
* caching raw evidence.

Not from immediately increasing concurrency.

---

# 76. Ranking scale vs detail scale

These have different costs.

Ranking pages:

> discover many products relatively efficiently.

Detail pages:

> much more expensive.

Therefore:

```text
collect rankings broadly
        ↓
deduplicate ASIN
        ↓
enrich details selectively
```

is preferable to:

```text
open every product detail repeatedly
```

---

# 77. Detail caching

Once a valid detail page has been collected:

raw evidence should ideally be reusable.

Do not revisit the same ASIN unnecessarily during parser development.

---

# 78. Future detail refresh policy

Not currently frozen.

Potential future behavior:

```text
new ASIN
→ detail immediately
```

```text
existing ASIN
→ refresh after interval
```

This should be designed later based on observed update needs.

---

# 79. Category expansion architecture

Recommended progression:

```text
Hogar y cocina
        ↓
stable end-to-end
        ↓
Bricolaje y herramientas
        ↓
stable end-to-end
        ↓
additional priority categories
```

Do not create category-specific architecture that cannot generalize.

---

# 80. Marketplace scope

Current marketplace:

```text
amazon.es
```

Do not prematurely generalize architecture to every Amazon country unless explicitly required.

A clean abstraction is useful.

A multi-market system is not currently necessary.

---

# 81. Database migration compatibility

Even while using CSV/JSON/Excel, design identities so future migration remains possible.

Important stable keys:

```text
asin
browse_node_id
ranking_source_url
collected_at
```

This will make future PostgreSQL migration easier if needed.

---

# 82. Excel is currently an output, not the canonical database

Excel is the main human-facing artifact.

It should not become the only source of truth for all internal processing if structured raw/normalized data exists.

Long-term:

```text
structured data
↓
Excel
```

is safer than:

```text
Excel
↓
everything
```

---

# 83. Current architecture maturity

Current state can be summarized as:

```text
Working collection scripts
+
real output
+
stronger data-processing scripts
+
developing modular architecture
```

The project is not greenfield.

The project is also not yet a fully packaged production crawler.

---

# 84. Refactoring strategy

Preferred:

```text
working script
↓
tests
↓
extract stable function
↓
module
↓
reuse
```

Avoid:

```text
working scripts
↓
delete everything
↓
large architecture rewrite
```

---

# 85. Architecture change policy

Any major architecture change should answer:

1. What real problem does this solve?
2. What currently verified behavior may regress?
3. Can the change be done incrementally?
4. What tests protect the migration?
5. Can old output be compared against new output?

If these questions cannot be answered:

do not perform a large rewrite.

---

# 86. Final architecture principle

The target architecture should make this possible:

```text
collect once
↓
preserve evidence
↓
process many times offline
↓
trace every business value back to source
```

The most important architectural properties are:

> safe collection, raw evidence preservation, clear data contracts, deterministic normalization, strong QA and replaceable exports.

Architecture exists to protect data quality.

Detail schema upgrades first reparse saved HTML offline. The quota selector
enforces one global ASIN set across category groups and raises
`QUOTA_UNIQUE_SHORTFALL` when the requested 200 cannot be satisfied.

After detail navigation, the browser uses short fixed render delays instead of
DOM selector/function waits. This prevents a partially responsive Playwright page
from blocking the whole batch; access-state detection still runs against saved
HTML and remains the authority for stopping on challenges or denied pages.

Not to maximize abstraction.

## Pipeline Production Hardening (2026-08-27)

The current stable path is `collect → select-quota → detail planning/collection →
offline reparse → enrich → translate-ds (explicit YES confirmation) → enrich with
translation overlay → qa → field closure → export gate → Excel`. The CLI and smoke tests protect this path without implementing a new
orchestrator. `run_manifest.py` is observability metadata beside the pipeline; it is
not a product/ranking source of truth. CI runs the offline tests only.
