# AmazonESBestseller — Current State

Last updated: 2026-08-26

This document describes the **current verified state** of the project.

Unlike `AGENTS.md`, which defines long-term development rules, this file is expected to change as the project progresses.

The purpose of this file is to prevent coding agents from:

* assuming the project is less complete than it actually is;
* redoing already-validated work;
* treating historical prototypes as the current main goal;
* expanding scope before current quality issues are resolved.

---

# 1. Current phase

The project has moved beyond pure reconnaissance.

Current phase:

> Working Amazon.es bestseller collection pipeline + data quality stabilization.

The crawler has already produced real Amazon.es product data and working Excel outputs.

The current development focus is:

1. stabilize the working collection pipeline;
2. improve data quality;
3. freeze field semantics;
4. convert fragile one-off logic into reusable components;
5. add regression tests;
6. expand collection only after quality is stable.

---

# 2. What has already been proven

The following capabilities have been demonstrated with real Amazon.es data.

## Amazon.es access

Real Amazon.es pages have been accessed successfully using browser automation.

Verified page types include:

* Amazon.es Best Sellers pages;
* product detail pages.

The project currently uses conservative browser access behavior.

---

# 3. Bestseller collection

Best Sellers collection has successfully produced real ranking data.

Verified fields include:

* ranking position;
* ASIN;
* Spanish product title;
* current price where shown;
* rating;
* review count;
* product URL.

A real example dataset exists for:

`Hogar y cocina`

with Top-30 bestseller records.

Therefore:

> Bestseller discovery itself is proven to work.

However, the current implementation is not yet considered the final production-grade all-category collector.

---

# 4. ASIN handling

ASIN extraction is working.

ASIN is currently used as the main product identity across:

* ranking data;
* product details;
* Excel exports;
* translation mappings;
* image mapping.

No replacement identity should be introduced.

---

# 5. Product detail extraction

Product-detail-page extraction has been successfully tested.

Existing detail extraction has retrieved fields including:

* title;
* current price;
* struck-through/list price where available;
* rating;
* review count;
* availability;
* Amazon BSR;
* seller;
* brand;
* selected variation/specification;
* sold-by-Amazon flag;
* fulfilled-by-Amazon flag.

The current detail extraction logic exists primarily in experimental/working scripts rather than a fully modularized production pipeline.

---

# 6. Current detail-data coverage observed in sample data

Existing sample datasets show that product-detail extraction can recover substantial information, but coverage varies by product.

Observed fields include:

* brand;
* manufacturer;
* product model;
* material;
* dimensions;
* capacity;
* weight;
* color;
* package count;
* special features;
* dishwasher suitability;
* microwave suitability;
* freezer suitability;
* country of origin;
* certifications;
* Amazon BSR;
* Parent ASIN;
* first-available date.

The presence of these fields differs significantly between product pages.

Missing fields must not be treated as parser failure unless evidence shows the field exists on the page.

---

# 7. Current sample dataset size

Current working dataset contains approximately:

> 193–200 Amazon.es product records

depending on the intermediate output version being examined.

A cleaned subset of:

> 100 SKUs

has already been selected as sufficiently reliable for direct internal use.

That 100-SKU subset was filtered using stricter rules for:

* product-name correctness;
* brand reliability;
* price availability;
* specification availability;
* image availability;
* Spanish/Chinese ASIN consistency.

---

# 8. Current clean export

A clean 100-SKU workbook has been produced with three worksheets:

1. category planning;
2. Spanish product list;
3. Chinese product list.

The Spanish and Chinese sheets use the same 100 ASINs in the same order.

The Chinese sheet includes embedded product images.

The Spanish sheet does not require embedded images.

---

# 9. Image handling

Image URLs are available for essentially all current product records.

Embedded-image export has been successfully demonstrated.

Current desired workbook behavior:

* Chinese sheet contains embedded images;
* Spanish sheet does not need embedded images;
* one image per product row;
* images must not be combined into one long montage;
* source image quality should be preserved;
* display resizing inside Excel is allowed;
* underlying image bytes should not be recompressed when preservation is requested.

---

# 10. Current category state

Current category data is incomplete.

Known category levels include combinations of:

* category L1;
* category L2;
* category L3;
* leaf category.

The earlier workbook version incorrectly duplicated category levels to simulate hierarchy.

That behavior has been corrected.

Current rule:

> unknown deeper category levels remain empty.

However, actual leaf-category coverage is still limited.

A future collection phase must improve category discovery using real Amazon evidence such as:

* Best Sellers navigation;
* breadcrumbs;
* Browse Nodes;
* source ranking pages.

---

# 11. Browse Node status

Browse Node support is conceptually required but current collected data has little or no reliable Browse Node coverage.

Current state:

> incomplete.

Future ranking records should preserve:

* browse_node_id;
* ranking source URL;
* category path;
* ranking position;
* collection timestamp.

Do not invent Browse Node IDs.

---

# 12. Ranking semantics

The project previously mixed two ranking concepts.

This issue has now been identified and must not regress.

## Bestseller ranking

Source:

Amazon Best Sellers page.

Meaning:

> position within that specific leaderboard.

Typical range:

1–100.

---

## Detail BSR

Source:

Amazon product detail page.

Meaning:

Amazon Best Sellers Rank displayed inside product details.

Possible values may be:

* hundreds;
* thousands;
* tens of thousands;
* hundreds of thousands.

These two values must remain separate.

Current Chinese workbook already separates the intended bestseller ranking from detail BSR more clearly than earlier versions.

---

# 13. Monthly bought status

Current sample data contains:

> 0 reliable monthly-bought values.

Target field examples include:

```text id="54jnqx"
100+ comprados el mes pasado
500+ comprados el mes pasado
1 mil+ comprados el mes pasado
```

Current interpretation:

> collection support has not yet been validated successfully.

This is an important future field because it provides direct demand evidence.

Do not infer it from ratings, reviews or ranking.

---

# 14. Current-price status

Current-price extraction is relatively mature.

Existing sample coverage is approximately:

> 95%+

depending on dataset version.

Some products legitimately may not display an immediately usable price.

Missing price must remain missing unless the actual page provides one.

---

# 15. Original-price status

Original / struck-through price coverage in the larger current sample is very low or absent.

The detail extractor has demonstrated that list price can sometimes be captured.

However, it is not yet reliably available across the current dataset.

Current rule:

> only store original price when Amazon clearly displays it.

No price reconstruction is allowed.

---

# 16. Discount-rate status

Discount rate depends on both:

* current price;
* original price.

Therefore current discount-rate coverage is also low.

Formula remains:

```text id="eckui6"
(original_price - current_price) / original_price
```

No original price:

> no discount rate.

---

# 17. Brand extraction state

Brand extraction is functional but not fully reliable.

Known successful sources include:

* Amazon byline;
* structured product details;
* explicit brand fields.

Known historical problems include false brands created from ordinary Spanish title words.

Examples previously identified as incorrect brand values include ordinary nouns equivalent to:

* cleaner;
* barrier;
* arm/handle;
* chimney cap.

Current rule:

> never use the first word of the title as a generic brand fallback.

Brand quality still requires QA.

---

# 18. Parent ASIN status

Parent ASIN has been successfully recovered for many sample products.

Observed coverage in earlier data was approximately:

> two thirds of products

depending on the dataset.

Absence does not always mean failure because some products may not belong to a visible variation family.

Current rule:

> preserve Parent ASIN when confirmed; otherwise leave null.

---

# 19. Specification extraction state

Specification extraction is partially mature.

The project can already normalize many values such as:

* dimensions;
* capacity;
* quantity;
* power;
* voltage;
* number of compartments;
* package size.

Examples of intended specification output:

```text id="t6qsa7"
90×190×40厘米
500毫升
2×250毫升
8件套 / 320–1200毫升
18V 4.0Ah / 2块
```

However, current specification logic still has known edge cases.

---

# 20. Known specification errors

Historical real-output errors include:

```text id="9irzgs"
9L → 25.4L
30L → 20L
10×15cm → 10×10mm
```

Other observed issues include:

* package quantity interpreted incorrectly;
* `quantity = 1` overriding real multi-piece sets;
* dimensions interpreted as capacity;
* weight interpreted as capacity;
* wrong variation selected from a list of available sizes;
* placeholder dimensions being treated as meaningful specifications.

These errors must become regression tests.

---

# 21. Current specification priority rule

When fields conflict, preferred evidence order should be:

1. current selected variation;
2. exact title specification;
3. explicit package/quantity description;
4. reliable technical details;
5. generic quantity fields.

This rule is not yet guaranteed to be fully enforced in all existing scripts.

That is a current improvement target.

---

# 22. Chinese product-name state

Chinese product names have been generated and manually reviewed for the current sample.

The current output is significantly better than literal full-title translation.

The desired naming style is:

> core product type + important specification + necessary compatibility/model information.

Current Chinese titles are usable for many SKUs.

However, translation and product-type classification are not yet fully production-safe.

---

# 23. Known product-name translation failures

Historical errors found in real output include cases equivalent to:

* thermal lunch bag → lunch box;
* reusable food containers → disposable food containers;
* coffee-machine cleaning tablets → portafilter;
* mini chainsaw → chainsaw lubricant;
* trimmer line → cordless grass trimmer;
* portafilter → coffee tamper.

These are considered high-severity QA failures.

Product-type correctness is more important than natural wording.

---

# 24. Chinese-name cleanup progress

Recent cleanup has improved:

* excessive title length;
* duplicate brand text;
* leftover Spanish marketing text;
* leftover English marketing text;
* unnecessary `|` separators.

Latin text is still allowed where meaningful, for example:

* Dyson V15;
* De'Longhi Dedica EC685;
* Nespresso Original;
* Dolce Gusto;
* SDS Plus;
* HSS;
* HEPA;
* USB-C;
* E27.

---

# 25. Brand/name separation

Current intended Chinese business model:

Brand field answers:

> Who makes the product?

Chinese title answers:

> What is the product?

Therefore, if the brand is already available separately, the Chinese title should usually not repeat it.

Compatibility brands/models remain exceptions.

---

# 26. Spanish and Chinese table relationship

The Spanish product list is the evidence-oriented business table.

The Chinese product list is a derived internal research table.

For a valid bilingual export:

* ASIN must match;
* row mapping must be deterministic;
* product URL must correspond to the ASIN;
* image URL must correspond to the ASIN;
* product identity must remain unchanged.

Chinese translation must never modify Spanish raw evidence.

---

# 27. Current Excel export state

Excel generation is one of the more mature parts of the project.

Existing workbook features have included:

* product tables;
* category planning;
* embedded images;
* frozen panes;
* filters;
* manual selection status;
* research notes;
* translated fields.

The current preferred simplified business workbook contains:

1. category planning;
2. Spanish product list;
3. Chinese product list.

Technical/raw information may remain outside the simplified export or in backend structures as the project evolves.

---

# 28. Manual business fields

Human-editable fields include:

* selection status;
* research notes.

These must be preserved between exports by ASIN.

Automated regeneration must not overwrite valid human values.

---

# 29. Translation implementation state

Current translation logic contains two different styles:

## reusable deterministic translation

Examples:

* units;
* materials;
* colors;
* common specification terms.

This is reusable and should be retained/improved.

---

## ASIN-specific/manual translation mappings

Some current scripts contain translations keyed directly by individual ASIN.

This was useful for producing a high-quality 200-SKU sample.

However:

> ASIN-specific translation does not scale to thousands of products.

Future translation architecture must gradually move toward:

* reusable product-type logic;
* structured normalization;
* model-assisted translation;
* QA validation;
* exception handling.

Do not delete existing manual mappings until replacement quality is proven.

---

# 30. Current code organization

The repository currently contains a mixture of:

* reconnaissance design documents;
* real collection scripts;
* detail extraction scripts;
* data-preparation scripts;
* translation scripts;
* workbook builders;
* audit scripts;
* historical CSV/JSON data;
* final reports.

The codebase is functional but not yet organized as a clean production package.

This should be improved incrementally.

---

# 31. Planned architecture exists

The repository already contains design documents for a cleaner modular structure including concepts such as:

* access detector;
* run store;
* browser probe;
* page inspector;
* category discovery;
* product-card parser;
* reports;
* CLI;
* offline tests.

These plans should be treated as guidance.

However:

> do not assume every planned module is already implemented.

Also:

> do not rewrite current working behavior merely to make the repository exactly match the old plan.

Implementation must follow verified current reality.

---

# 32. Test state

Automated regression testing is currently one of the weakest areas.

There are audit scripts, but the repository does not yet have sufficient formal regression tests for:

* title translation;
* specification parsing;
* category mapping;
* ranking semantics;
* image association;
* brand extraction.

This is a high-priority improvement.

---

# 33. Recommended regression fixtures

Known real-world failures should become permanent tests.

At minimum include scenarios covering:

### Product type

```text id="m9e8rq"
thermal lunch bag ≠ lunch box
portafilter ≠ tamper
cleaning tablets ≠ portafilter
mini chainsaw ≠ chain lubricant
trimmer line ≠ trimmer machine
```

### Specification

```text id="j8xh36"
9L stays 9L
30L stays 30L
10×15cm stays 10×15cm
```

### Ranking

```text id="388bp8"
Best Sellers rank ≠ Detail BSR
```

### Category

```text id="dw1z0t"
unknown leaf category remains null
```

### Brand

```text id="oc3wh7"
ordinary Spanish noun must not become brand
```

---

# 34. Current access-control behavior

The project intentionally does not attempt to bypass Amazon access controls.

Current expected behavior remains conservative.

On:

* 403;
* 429;
* Robot Check;
* CAPTCHA;
* access denied;
* challenge pages;

the system should record the condition and stop according to the current access policy.

No CAPTCHA bypass or stealth escalation is planned.

---

# 35. Current scale status

The current real dataset demonstrates that:

> collection and transformation work.

It does NOT yet prove that the system can reliably collect:

> 6,000–10,000 unique ASINs

in a repeatable production workflow.

Scale validation has not yet been completed.

---

# 36. Full-category collection status

Current status:

> not yet fully validated.

The next major milestone should not be immediately “all Amazon.es categories”.

Instead validate:

1. one major category end-to-end;
2. repeatability across runs;
3. category structure;
4. ranking evidence;
5. detail enrichment rate;
6. QA;
7. export.

Then validate a second major category.

---

# 37. Recommended next major validation

Recommended first full category:

`Hogar y cocina`

Target:

* discover real category nodes;
* collect ranking records;
* deduplicate product ASINs separately;
* enrich selected product details;
* generate bilingual output;
* run QA;
* produce completeness statistics.

After that:

`Bricolaje y herramientas`

can be used as the second major validation category.

---

# 38. What is currently considered stable enough to preserve

The following concepts should be treated as established project assets:

* ASIN as product key;
* ranking records separate from product records;
* raw Spanish evidence preservation;
* current/original price separation;
* Bestseller rank separate from Detail BSR;
* Chinese business-layer output;
* specification normalization;
* category-planning workbook concept;
* image association by product identity;
* human selection status/research notes;
* conservative Amazon access behavior.

Do not casually redesign these concepts.

---

# 39. What is currently considered unstable

The following areas are still expected to change:

* Chinese product-title generation;
* specification precedence;
* brand fallback behavior;
* full category hierarchy extraction;
* leaf-category discovery;
* Browse Node capture;
* monthly-bought extraction;
* original-price coverage;
* detail-page field coverage;
* final production module layout;
* automated QA implementation.

Changes here are expected, but they must preserve raw evidence and verified working behavior.

---

# 40. Immediate engineering priorities

Current recommended priority order:

## P0 — Data correctness

* eliminate known product-type translation failures;
* eliminate obvious specification mis-parsing;
* prevent false brand detection;
* preserve rank semantics.

---

## P1 — Regression protection

Add offline tests for real failures already observed.

---

## P1 — Collection traceability

For ranking records, improve preservation of:

* source category;
* ranking source URL;
* Browse Node where available;
* collection time.

---

## P1 — Pipeline clarity

Identify the real currently used runtime entry path and document it clearly.

Reduce ambiguity between:

* active scripts;
* old experiments;
* migration scripts;
* reports.

---

## P2 — Missing valuable fields

Investigate reliable extraction of:

* monthly bought;
* leaf categories;
* Browse Node;
* original/list price;
* selected variation.

Do not sacrifice access stability for fill rate.

---

## P2 — Reusable translation pipeline

Gradually reduce dependence on per-ASIN translation mappings.

---

# 41. Current non-priorities

Unless explicitly requested, the following are not immediate priorities:

* PostgreSQL migration;
* web dashboard;
* cloud deployment;
* distributed workers;
* multi-machine crawling;
* high concurrency;
* proxy infrastructure;
* mobile interface;
* user authentication;
* public SaaS productization.

---

# 42. Definition of the next meaningful milestone

The next major milestone should be:

> One major Amazon.es category can be collected end-to-end by the current program, producing traceable ranking records, product records, bilingual business output, images and QA results with no known high-severity data corruption.

The milestone should include repeatability.

A single successful run is not enough.

---

# 43. Definition of readiness for large-scale expansion

Do not expand toward 6,000–10,000 unique ASINs until the following are true:

* product identity is stable;
* ranking semantics are stable;
* category-source evidence is stable;
* specification parser passes regression cases;
* brand extraction has false-positive protection;
* Chinese title QA catches product-type mismatches;
* images remain correctly associated;
* human fields survive regeneration;
* run failures do not corrupt final output;
* at least two major categories have completed successfully.

---

# 44. Current project summary

In one sentence:

> AmazonESBestseller is already a working Amazon.es bestseller data-collection and selection-research project, but it is currently transitioning from successful real-world scripts and sample outputs into a stable, tested, reusable collection system.

Do not downgrade it to a prototype.

Do not overstate it as a finished full-scale crawler.

Both statements would be inaccurate.
