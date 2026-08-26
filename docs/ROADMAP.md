# AmazonESBestseller — Roadmap

Last updated: 2026-08-26

This document defines the recommended development sequence for the `AmazonESBestseller` project.

The roadmap is intentionally conservative.

The project should expand only after existing behavior is stable and data quality is protected.

---

# 1. Long-term goal

Build a reliable Amazon.es bestseller research dataset covering major physical-product categories.

Target future scale:

- approximately 6,000–10,000 unique ASINs;
- multiple ranking records per ASIN where applicable;
- structured product details;
- bilingual Spanish/Chinese business output;
- repeatable collection;
- traceable ranking/category evidence;
- strong QA.

The project is not currently required to become:

- a general Amazon crawler;
- a SaaS;
- a cloud service;
- a distributed crawler;
- a high-concurrency scraping system.

---

# 2. Current phase

Current phase:

> Stabilization and engineering of the already-working collection pipeline.

The project already has:

- real Amazon.es collection results;
- Best Sellers data;
- detail-page data;
- structured intermediate data;
- Chinese normalization;
- Excel export;
- real-world QA findings.

Therefore the next goal is not:

> prove that Amazon data can be collected.

That has already been demonstrated.

The next goal is:

> make the existing working process stable, repeatable and testable.

---

# 3. Development principles

Roadmap execution must follow these principles:

1. correctness before scale;
2. tests before large refactors;
3. preserve working behavior;
4. raw evidence before derived values;
5. small vertical improvements;
6. no unnecessary infrastructure;
7. no scope expansion until current milestone passes.

---

# 4. Phase 0 — Reconnaissance and feasibility

Status:

> ✅ COMPLETED / substantially completed

Goals originally included:

- verify Amazon.es Best Sellers accessibility;
- inspect Best Sellers structure;
- inspect product-card structure;
- extract ASINs;
- test product detail access;
- identify useful fields;
- understand access limitations;
- create initial sample datasets.

Verified outcomes include:

- real Best Sellers data collected;
- real ASINs extracted;
- detail pages accessed;
- prices, brand and technical fields recovered;
- product data exported;
- category and ranking-model issues discovered.

No need to repeat reconnaissance from scratch.

---

# 5. Phase 1 — Freeze project rules and data contracts

Status:

> 🟡 IN PROGRESS

Goal:

Create a stable development context so future agents do not reinterpret the project.

Required documents:

- `AGENTS.md`
- `docs/CURRENT_STATE.md`
- `docs/DATA_MODEL.md`
- `docs/QA_RULES.md`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`

Exit condition:

All seven documents exist and reflect current reality.

---

# 6. Phase 2 — Identify the real production path

Status:

> ⬜ NEXT

Current repository contains:

- active scripts;
- experiments;
- workbook builders;
- translation scripts;
- historical repair scripts;
- reports.

The first engineering task is to determine:

> which scripts currently form the real working pipeline.

Required outcome:

Document the actual current execution sequence.

Example conceptual result:

```text
ranking collection
→ detail extraction
→ data preparation
→ translation
→ QA
→ workbook export
```

Do not refactor yet unless necessary.

---

# 7. Phase 2 deliverable

Create a short internal map such as:

```text
Current runtime entry:
<real script>

Ranking input:
<real file/module>

Detail enrichment:
<real file/module>

Normalization:
<real file/module>

Translation:
<real file/module>

Export:
<real file/module>
```

This must describe reality, not the planned architecture.

---

# 8. Phase 3 — Regression test foundation

Status:

> ⬜ HIGH PRIORITY

Before major parser or translation refactoring, convert known real failures into tests.

Minimum regression groups:

## Product type

* thermal lunch bag must not become lunch box;
* reusable containers must not become disposable;
* portafilter must not become tamper;
* cleaning tablets must not become portafilter;
* mini chainsaw must not become chain lubricant;
* trimmer line must not become trimmer machine.

## Specification

* 9L must remain 9L;
* 30L selected variant must remain 30L;
* 10×15cm must remain 10×15cm;
* package quantity must not be replaced by generic quantity=1.

## Ranking

* Bestseller page rank must remain separate from Detail BSR.

## Brand

* ordinary Spanish nouns must not become brand.

## Category

* missing leaf category must remain null.

Exit condition:

Known historical P0/P1 failures are protected by automated tests or deterministic QA checks.

---

# 9. Phase 4 — Stabilize ranking records

Status:

> ⬜

Goal:

Turn Best Sellers collection into a traceable ranking dataset.

Each ranking record should preserve:

* ASIN;
* rank;
* source URL;
* category context;
* Browse Node where available;
* collection timestamp;
* monthly-bought evidence where available.

Key rule:

```text
one ASIN
×
one ranking context
=
one ranking record
```

Do not collapse multi-ranking products.

---

# 10. Phase 4 acceptance criteria

For a bounded test category:

* ranking source URL available for nearly all records;
* ASIN extraction reliable;
* ranking semantics verified;
* no Detail BSR contamination;
* duplicate ASINs across different ranking contexts preserved correctly;
* category evidence traceable.

---

# 11. Phase 5 — Category discovery stabilization

Status:

> ⬜

Goal:

Build reliable Amazon category discovery from real page evidence.

Focus on:

* Best Sellers category navigation;
* category URLs;
* Browse Node extraction;
* parent-child relationships;
* actual category depth.

Do not infer categories from product titles.

---

# 12. Phase 5 output

Expected conceptual data:

```text
category_tree
-------------
category_name_es
category_name_zh
parent_category
depth
browse_node_id
ranking_url
source
```

Unknown hierarchy levels remain null.

---

# 13. Phase 6 — Detail collector engineering

Status:

> ⬜

Current detail extraction has already proven that useful data can be captured.

Next goal:

Convert it from batch-specific/manual ASIN input toward reusable structured input.

Desired flow:

```text
ranking records
→ unique ASINs
→ detail planner
→ detail collector
→ raw detail data
```

Do not immediately redesign everything.

Extract stable behavior incrementally.

---

# 14. Detail collector priorities

Priority fields:

1. ASIN verification;
2. product title;
3. current price;
4. original price where shown;
5. brand;
6. image;
7. selected variation;
8. Parent ASIN;
9. technical details;
10. first available date;
11. Detail BSR.

Secondary fields:

* seller;
* availability;
* fulfilled by Amazon;
* sold by Amazon.

---

# 15. Detail access strategy

Detail pages are more expensive than ranking pages.

Preferred architecture:

```text
collect ranking pages broadly
        ↓
deduplicate ASIN
        ↓
only enrich ASINs that need detail data
```

Avoid repeatedly visiting the same detail page.

---

# 16. Phase 7 — Specification normalization V2

Status:

> ⬜

Goal:

Make specification parsing reliable enough for large-scale use.

Required improvements:

* selected variation must outrank alternative sizes;
* title specification must outrank generic technical fields;
* capacity/dimension/weight types must be validated;
* package counts must distinguish pieces/containers/sets;
* suspicious placeholder dimensions must be rejected.

---

# 17. Specification V2 acceptance criteria

Known regression cases all pass.

Additionally:

* no dimension unit appears in capacity field;
* no weight unit appears in capacity field;
* selected variant is preserved;
* generic `quantity=1` does not override explicit multi-piece title;
* invalid values become null/WARN rather than plausible-looking wrong data.

---

# 18. Phase 8 — Brand normalization V2

Status:

> ⬜

Goal:

Reduce false-positive brands.

Required:

* byline extraction;
* structured brand extraction;
* prefix cleanup;
* Unicode cleanup;
* canonical case normalization;
* no title-first-word generic fallback.

Acceptance condition:

Known false-brand examples no longer pass QA.

---

# 19. Phase 9 — Chinese title pipeline V2

Status:

> ⬜

Current ASIN-specific/manual mappings successfully improve the sample dataset but do not scale.

Target architecture:

```text
Spanish raw title
+
normalized specification
+
brand
+
technical details
        ↓
product-type identification
        ↓
Chinese concise title
        ↓
QA
        ↓
exception overrides
```

---

# 20. Chinese title V2 rules

Target format:

```text
核心商品类型
+
关键规格/数量
+
必要兼容型号
```

The pipeline should prioritize:

1. correct product type;
2. useful buying specification;
3. concise wording;
4. compatibility information;
5. removal of unnecessary marketing text.

---

# 21. Chinese title V2 acceptance criteria

For a representative fixture set:

* no known product-type regressions;
* brand duplication limited;
* ordinary marketing phrases translated/removed;
* compatibility brands retained correctly;
* Chinese title remains usable for selection research.

---

# 22. Phase 10 — QA gate

Status:

> ⬜

Goal:

Turn current manual/audit logic into a repeatable export gate.

Recommended statuses:

```text
PASS
WARN
FAIL
SOURCE_CONFLICT
```

Strict export should exclude:

* FAIL;
* SOURCE_CONFLICT.

WARN policy can depend on export purpose.

---

# 23. QA gate checks

Minimum:

* ASIN validity;
* ASIN/URL match;
* bilingual ASIN match;
* image match;
* rank semantics;
* category source;
* brand validity;
* specification validation;
* Chinese product-type validation;
* field completeness statistics.

---

# 24. Phase 11 — Export pipeline stabilization

Status:

> 🟡 PARTIALLY COMPLETE

Excel export already works.

Next goal:

Move business inference out of workbook-building code.

Target:

```text
normalized data
+
Chinese derived data
+
QA result
+
manual fields
        ↓
Excel exporter
```

Exporter should mostly format, not guess.

---

# 25. Preferred business workbook

Keep:

```text
1. 类目规划
2. 西班牙语选品清单
3. 中文选品清单
```

Current preference:

* Spanish sheet does not require embedded images;
* Chinese sheet includes one product image per row;
* original image quality preserved where requested.

---

# 26. Human field preservation

Before every regeneration:

preserve by ASIN:

```text
选品状态
研究备注
```

This is mandatory before the workbook becomes a long-term operating tool.

---

# 27. Phase 12 — Full `Hogar y cocina` validation

Status:

> ⬜ MAJOR MILESTONE

This should be the first true end-to-end category validation.

Required pipeline:

```text
category discovery
→ ranking collection
→ unique ASIN
→ detail enrichment
→ normalization
→ Chinese transformation
→ QA
→ Excel
```

---

# 28. Hogar y cocina target

Do not define success solely by product count.

Measure:

* category coverage;
* ranking-record count;
* unique ASIN count;
* ASIN extraction rate;
* price fill rate;
* brand fill rate;
* specification fill rate;
* image coverage;
* category coverage;
* Detail enrichment completion;
* QA PASS/WARN/FAIL distribution;
* access stability.

---

# 29. Repeatability requirement

A single successful full run is insufficient.

Require at least:

> multiple bounded full runs

without high-severity data corruption.

Compare:

* run success;
* category discovery;
* ranking counts;
* schema consistency;
* access state;
* export integrity.

---

# 30. Phase 13 — Second major category validation

Recommended category:

> `Bricolaje y herramientas`

Reason:

It contains different product structures from Home & Kitchen:

* electrical tools;
* consumables;
* accessories;
* batteries;
* drill bits;
* pumps;
* mechanical products.

This is useful for testing whether the parser generalizes.

---

# 31. Second-category acceptance criteria

The system must handle both:

```text
Hogar y cocina
```

and:

```text
Bricolaje y herramientas
```

without category-specific hard-coded logic becoming unmanageable.

---

# 32. Phase 14 — Category expansion

Status:

> ⬜

Only begin once the first two major categories are stable.

Expand according to business relevance.

Possible future physical-product groups may include:

* hogar;
* cocina;
* bricolaje;
* jardín;
* iluminación;
* baño;
* almacenamiento;
* limpieza;
* mascotas;
* oficina;
* deporte;
* automóvil;
* pequeños accesorios electrónicos;
* temporada.

Actual categories must come from real Amazon.es structure.

---

# 33. Category expansion strategy

Do not immediately crawl every Amazon category.

Use priority tiers.

Example:

```text
Tier 1
high relevance

Tier 2
medium relevance

Tier 3
low relevance / later
```

Category planning sheet should control this.

---

# 34. Phase 15 — 1,000 unique ASIN milestone

Before attempting 6,000–10,000:

first prove:

> approximately 1,000 unique ASINs

can be processed reliably.

Acceptance:

* no identity corruption;
* manageable access behavior;
* acceptable QA failure rate;
* export remains usable;
* runtime/recovery behavior understood.

---

# 35. Phase 16 — 3,000 unique ASIN milestone

Then:

> approximately 3,000 unique ASINs

Focus on:

* resumability;
* detail caching;
* incremental enrichment;
* output size;
* image handling;
* QA performance.

---

# 36. Phase 17 — 6,000–10,000 ASIN production target

Only after previous milestones pass.

Target capabilities:

* multi-category ranking collection;
* deduplicated products;
* repeatable detail enrichment;
* structured category evidence;
* QA;
* bilingual output;
* historical refresh capability.

---

# 37. Scaling should not mean concurrency first

Preferred scaling tools:

1. avoid duplicate requests;
2. reuse saved HTML/raw data;
3. cache detail data;
4. refresh only when needed;
5. separate ranking refresh from detail refresh;
6. resume interrupted runs;
7. prioritize categories.

Concurrency is not the first solution.

---

# 38. Future incremental refresh

Once large-scale initial collection is stable, introduce incremental runs.

Potential model:

```text
ranking pages
→ refresh frequently

existing product details
→ refresh less frequently

new ASIN
→ detail enrichment

changed/high-priority ASIN
→ targeted refresh
```

Exact cadence is not yet frozen.

---

# 39. Future history layer

Potential future analyses:

* rank movement;
* price movement;
* new Best Seller entrants;
* persistent sellers;
* disappearing products;
* review growth.

This may eventually require historical snapshots.

Do not build before stable collection exists.

---

# 40. Future database

PostgreSQL may become useful when:

* dataset grows substantially;
* historical snapshots are needed;
* cross-run querying becomes difficult;
* Excel/JSON no longer provides enough operational structure.

It is not a current prerequisite.

---

# 41. Database adoption gate

Do not migrate to PostgreSQL merely because:

> databases are more professional.

Migrate when there is a concrete operational need.

Possible trigger:

```text
thousands of ASINs
+
multiple historical snapshots
+
repeated analytical queries
```

---

# 42. Future UI

A web dashboard may eventually be useful for:

* category browsing;
* selection;
* filtering;
* product comparison;
* historical trend review.

Not a current milestone.

Excel remains sufficient for the current research phase.

---

# 43. Future AI selection layer

After data quality is stable, AI may assist with:

* product clustering;
* assortment gaps;
* price-band opportunities;
* compatibility with Chinese retail stores;
* duplicate product detection;
* category opportunity scoring;
* comparison with Action or other retailers.

AI selection should operate on validated data.

Do not use AI to compensate for poor source data.

---

# 44. Important field roadmap

Current high-value missing/incomplete fields:

## P1

* ranking source URL completeness;
* Browse Node;
* reliable leaf category;
* selected variation;
* specification correctness.

## P2

* monthly bought;
* original price;
* first available date coverage;
* Parent ASIN coverage.

## P3

* seller;
* fulfillment;
* other secondary attributes.

---

# 45. Monthly-bought roadmap

Because monthly bought may be particularly valuable for selection:

1. verify where Amazon displays it;
2. save raw examples;
3. build offline fixtures;
4. create locale parser;
5. preserve raw text;
6. store minimum value;
7. measure real coverage.

Do not infer missing values.

---

# 46. Image roadmap

Current image support is sufficient for business output.

Future improvements may include:

* local image cache;
* original image preservation;
* deterministic ASIN/image mapping;
* duplicate-image QA;
* optional separate image archive.

Do not make image processing part of live Amazon navigation unless necessary.

---

# 47. Repository cleanup roadmap

Repository organization should improve gradually.

Potential target:

```text
README.md
AGENTS.md

docs/
src/
tests/
scripts/
outputs/
```

Historical reports can move to:

```text
docs/history/
```

One-time scripts can move to:

```text
scripts/historical/
scripts/audit/
scripts/migration/
```

Only move files after active usage is understood.

---

# 48. Documentation maintenance

`CURRENT_STATE.md`

should be updated when:

* a major feature becomes verified;
* a known problem is solved;
* scale milestone changes.

`ROADMAP.md`

should change only when:

* priorities materially change;
* milestones are completed/reordered.

Do not rewrite the roadmap after every small patch.

---

# 49. Milestone review rule

At the end of each major milestone:

1. run automated tests;
2. review QA statistics;
3. compare output against previous known-good data;
4. document regressions;
5. update CURRENT_STATE;
6. only then start the next milestone.

---

# 50. Suggested risk levels

## High-risk changes

* ASIN identity;
* ranking semantics;
* category mapping;
* specification parser;
* product-type translation;
* image mapping;
* price parsing.

Require strong review/tests.

## Medium-risk changes

* brand normalization;
* date parsing;
* output mapping;
* manual-field preservation.

## Low-risk changes

* documentation;
* formatting;
* non-business refactors with no behavior change.

---

# 51. Agent development cycle

Recommended vibe-coding cycle:

```text
User defines bounded goal
        ↓
Agent reads docs + relevant code
        ↓
Agent makes change
        ↓
Tests
        ↓
QA
        ↓
Review
        ↓
Fix P0/P1
        ↓
Merge
```

Do not combine several unrelated roadmap stages in one large coding pass unless explicitly requested.

---

# 52. Review strategy

For larger batches of work:

review findings can be classified:

```text
HIGH
MEDIUM
LOW
```

High:

must fix before merge.

Medium:

normally fix before or soon after merge depending on scope.

Low:

can be deferred if cosmetic or low-risk.

This keeps Agent review efficient.

---

# 53. Definition of Phase 1 production readiness

The project may be considered ready for larger-scale collection when:

* the real runtime path is documented;
* regression tests exist;
* category/rank semantics are stable;
* no known P0 product-name/specification issues remain;
* QA gate exists;
* one major category runs successfully multiple times;
* a second category validates parser generalization;
* export is repeatable;
* manual fields are preserved.

---

# 54. Definition of large-scale success

Large-scale success is not:

> 10,000 rows in Excel.

It is:

> thousands of traceable ASINs whose identity, ranking context, product type and core specifications are trustworthy enough to support real selection decisions.

---

# 55. Current immediate next steps

Recommended immediate order from the current project state:

```text
1. Finish the seven core MD files
2. Audit the actual current runtime path
3. Freeze known-good current output
4. Add regression fixtures/tests
5. Stabilize ranking records
6. Stabilize category discovery
7. Refactor detail input away from hard-coded ASIN batches
8. Improve specification parser
9. Improve brand QA
10. Improve Chinese title pipeline
11. Add formal QA gate
12. Full Hogar y cocina validation
13. Repeat-run validation
14. Bricolaje y herramientas validation
15. Begin controlled category expansion
```

---

# 56. What should NOT happen next

Do not immediately:

* rewrite the entire repository;
* add PostgreSQL;
* build a website;
* increase concurrency;
* crawl all Amazon.es categories;
* add proxy rotation;
* add CAPTCHA solving;
* translate thousands of products before QA is stable;
* optimize Excel appearance while core data errors remain.

---

# 57. Final roadmap principle

The project should grow in this order:

```text
WORKING
↓
CORRECT
↓
TESTED
↓
REPEATABLE
↓
SCALABLE
```

Not:

```text
WORKING
↓
MASSIVE SCALE
↓
FIX DATA LATER
```

The roadmap prioritizes:

> correctness first, scale second.
