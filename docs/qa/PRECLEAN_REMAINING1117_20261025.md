# Remaining Pre-Clean review, 2026-10-25

The reviewed collection contains 5,412 unique ASINs. The previous derived view
admitted 4,295 products and held 1,117 products / 1,164 fields. The user's mention
of 1,175 did not identify a separate source cohort; this review uses that exact
1,117-product queue.

## Verified parser corrections

- Recognize the literal `Grosor` dimension-axis suffix in an otherwise exact,
  ordered dimension vector. Do not reorder axes, round numbers or ignore
  conflicting appended weights.
- Treat the singular/plural spelling of the same integer lower age bound
  (`3 año y más` / `3 años y más`) as equivalent, while keeping both rows.
  Finite age ranges, different values and months still require review.
- Treat only `Cuadrado` / `Cuadrada` as the same square shape. Additional
  qualifiers and different shapes remain blocked.
- Separate explicitly bound minimum fill, maximum/nominal capacity, useful
  capacity and recommended fill only when their literal bounds are consistent.
  Unknown additional values, competing maxima and recommendations above the
  nominal limit still block admission.
- Read the exact Spanish phrase `1 litro y medio` as 1.5 litres without
  rewriting the source text. A following independent unit is not inferred.

The saved-source fixtures cover 11 equivalent attribute groups and 17
capacity fields. The two new test modules contain 70 regression cases, including
negative and production-compatibility preservation cases. An ASIN-only product
description remains held: changing only Pre-Clean to filter it would disagree
with the existing formal production release adapter.

## Task-specific display-category decision

The human explicitly selected product-detail breadcrumbs for the formal display
categories of the 296 held products. Their saved detail breadcrumbs and ranking
navigation were independently verified against DOM text and SHA-256. A new
derived input copy applies the existing `category_levels` projection to the
complete detail path and retains the complete ranking path with its evidence
separately. The original canonical input and collection evidence are unchanged.
This is a recorded choice for this batch, not a change to the repository's
default ranking-category policy. Remove only the resolved category-source hold;
independent field checks still apply.

## Offline replay result

- 5,412 products retained; 4,596 admitted by Pre-Clean; 816 still held.
- 301 additional products admitted; no previously admitted product newly held.
- All 1,164 original held fields have an individual evidence-bound ledger entry;
  324 of those fields resolved. 840 fields remain held.
- All 296 category display choices applied, with 278 products fully admitted
  and 18 retaining other field holds.
- Remaining reason counts overlap: 705 products have distinct duplicate
  attributes, 106 have capacity context/value questions, 9 have source-unit or
  placeholder anomalies, and 1 has an identity-only description.
- 65 complex capacity candidates received a second review. Five show additional
  source/arithmetic risks; the other 60 are candidates for narrow tested rules,
  not current admission approvals. For example, 3 × 933 ml is not 2,800 ml, and
  700 + 310 ml is not the 1 litre claimed by another field.

No new official page was obtained. Direct URL access was unavailable through
the web tool, and the existing Edge selection was stopped because the tool
could not reliably determine its current URL for policy checks. No access
restriction was bypassed. Saved official evidence is explicitly distinguished
from fresh website verification.

No collection, real translation, production deployment, database migration,
checkpoint modification or original-data replacement occurred. Pre-Clean
admission is neither translation completion nor production acceptance. CI and
test receipts for the exact submitted commit remain the source of test status.
