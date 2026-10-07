# Production Closure State — Canonical Normalization Patch

Run ID: `20261007T000000Z_price_self_parent`

## Scope

This isolated development worktree contains only the canonical-normalization
closure for price chaining and self-parent identity handling.  It does not
change production outputs, raw evidence, candidate data, Excel export,
translation, category mapping, audits, or CLI behavior.

## Base and workspace

- Verified production base branch: `feature/amazon-es-production-v1-complete`
- Verified production base SHA: `1c570e2e708a2b90f8a7ac9be3f2d0e5d2a9bdf9`
- Development branch: `feature/translation-production-closure-v1`
- Development worktree: `F:\AmazonESBestseller\.worktrees\translation-production-closure-v1`
- Read-only production source: `F:\AmazonESBestseller\.worktrees\production-v1-complete\outputs\amazon_es_bestseller_5500_202610_fresh2`

## Patch result

- Self-parent ASINs retain the observed value in `parent_asin_raw`, but have
  an empty canonical `parent_asin` and explicit `parent_asin_status=unconfirmed`
  unless parser-produced variation evidence confirms a multi-ASIN family.
- `parent_asin_status=confirmed` alone is not accepted as provenance.
- `original_price` is canonical only when it is greater than current price;
  `discount_rate` is calculated from that canonical price only.

## Production evidence status

- `5480` exact-source detail records were observed.
- `845` records have self-parent values without confirmed variation provenance.
- Observed price findings: `71` original prices below current price, `190`
  equal prices, and `3094` valid higher original prices missing a derived
  discount in the existing output.
- Candidate-manifest byte hash differs from its canonical JSON hash because of
  CRLF pretty formatting.  The canonical hash
  `7774cc27bfccb7a1377bfc031103881085d1e8cdaf3018451e848f0505137414`
  matches all checked sidecars and checkpoints; this is not a P0 blocker.
- `5480 = 5500 - 19 IDENTITY_MISMATCH - 1 pending (B0DKT3NTS9)`; audit record
  bindings exactly match the MATCH-detail set.

## Gate status

`Source Gate` remains **BLOCKED** and has not been re-reviewed.  This change
does not assert production readiness.  All collection, saved-HTML reparse,
translation, QA/audit rerun, Excel export, and final-manifest phases remain
`NOT_EXECUTED`.

## Focused verification

Using the project test interpreter from the existing production worktree:

- `tests/test_pipeline.py -k "self_parent or discount"` — passed, 5 tests
- `tests/test_price.py` — passed, 6 tests

No full test suite was run for this narrowly scoped patch.
