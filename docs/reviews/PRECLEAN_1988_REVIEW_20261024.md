# Review of 1,988 Pre-Clean exceptions

This review uses the existing 5,412-record corrected copy and saved Amazon
pages. Collection, translation, production deployment and source overwrites
are outside this change. The original 305 source/business review holds remain.

## Confirmed code defects

- Description metadata detection mistook ordinary Spanish B-words and prose
  about classification/customer appreciation for ASINs or metadata. Match
  actual identity syntax and explicit metadata labels instead.
- The `l.` length axis and explicit `L x A x A cm` dimensions were read as
  litres. Exclude these dimension spellings from volume comparison.
- Explicit component/total arithmetic such as `3 x 8 L = 24 L` or
  `500 ml + 150 ml = 650 ml` was treated as a contradictory scalar. Admit
  only source-explicit, exactly consistent arithmetic; unexplained or wrong
  totals still require review. No inferred capacity is written to a record.
- Exact bullet duplicates were removed and then still blocked admission.
  Preserve the duplicate finding, normalize the derived list, and continue
  blocking empty/malformed or otherwise conflicting content.
- Flattened attribute labels containing colons lost their boundary. Recover
  a label only when both its complete name and value bind to an original raw
  attribute; stale/unmatched hints cannot replace the selected source.
- A generic angle-bracket regex deleted source comparisons such as
  `<300 g/m² ... >399 g/m²` and bracketed identifiers. Remove known HTML
  elements only; preserve comparison text and product identifiers.
- Duplicate statistics counted affected SKUs as occurrences. Report literal
  duplicate occurrences and affected SKUs separately.

## Evidence and results

All 1,988 source HTML files passed their recorded SHA-256 checks. The 948
remaining duplicate-label cases were reconciled to actual source cells and
their DOM paths: different literal source values remain under review, not
declared incorrect or silently unified. Three parent-ASIN form cases replayed
through the existing identity gate and resolved to their original child ASINs;
no identity rule was changed.

Of the original 1,988 products, 645 leave the review queue after the fixes,
1,220 retain source/page differences, and 123 retain multi-object/capacity or
identity-in-description context review. Fixes affected 874 products; 229 of
those still have another review reason. These are not additive error counts.

The full 5,412-record rerun contains 4,069 Pre-Clean usable products and 1,343
review products. There are 51,338 retained field slots, 1,416 review field
slots, and 6,778 originally absent field slots. No previously clear field
acquired a new review finding. "Usable" refers to Pre-Clean admission only,
not translation, final QA or production acceptance.

Nine hash-bound saved-page fixture cases and negative conflict cases are in
`tests/fixtures/preclean_review1988_20261024/` and
`tests/test_preclean_review1988_regressions.py`. The 44 new regressions and
related preclean/service/CLI/production/specification tests total 153 passes,
zero failures and zero skips. Remote CI, a PR, main merge and deployment have
not been performed for this review change.

Derived reports and screening copies retain source text, ASIN, source hashes,
review holds and human notes. Original HTML, DetailState/checkpoints, frozen
queues, prior reports and workbooks remain unchanged. No Amazon or provider
calls were made. No page values were guessed or repaired.
