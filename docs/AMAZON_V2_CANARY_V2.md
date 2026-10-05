# Amazon.es V2 Canary V2

V2 is a candidate parser only. V1 remains the stable/default parser and the
reviewed 5,500-SKU task (`amazon_es_bestseller_5000_202610`) must not call this
entrypoint.

The profile is disabled, offline and non-promoting by default. Its immutable
scope is one Amazon.es Best Sellers source, two pages and at most five unique
detail ASINs. `dry_run` performs no network request. A future CLI/orchestrator
may call the pure API with saved fixtures or separately collected evidence;
this module itself does not browse Amazon.

Each A/B/C gate is made with `seal_stage(stage, input_data, evidence)`. The
gate recomputes both hashes and rejects missing/stale evidence.

- A: normal access, known expected count, server count, no duplicates/missing
  slots.
- B: V1/V2 shadow comparison plus V2 saved-HTML replay equality across ASIN,
  rank and URL.
- C: V2 detail/saved-HTML replay equality across identity, parent, variation,
  attributes and category evidence; legal detail identity and normal access.

Any challenge/access restriction, request count in the offline evaluator,
budget overflow, missing stage or comparison drift results in
`CANDIDATE_NOT_PROMOTABLE`. A complete evidence chain yields `PROMOTABLE`, but
still reports `promoted: false` and never changes a parser default. A separate
reviewed promotion operation would be required later.
