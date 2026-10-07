# Production Closure State — Canonical Normalization Patch

Run ID: `20261007T000000Z_price_self_parent`

## Scope

This isolated development worktree contains narrowly scoped closure patches.
It does not change production outputs, raw evidence, candidate data, Excel
export, provider behavior, brand derivation, category mapping, or CLI behavior.

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

## Follow-on HTML and unit-audit patch

- Fixed tag-aware handling in translation pre-clean and source-field junk
  detection: escaped product text and comparison thresholds such as
  `&lt; MULTIFUNCIÓN &gt;`, `<50% RH`, and `>70% RH` are retained, while real
  HTML tags such as `<script>` remain detected and removed from derived text.
- Fixed unit-type audit to inspect structured `attributes` / `details_json`
  label-value pairs first. Legacy compact specifications are only inspected as
  explicit label-value segments; a flattened detail blob no longer lets
  `Potenciador` impersonate the `Potencia` label, and dimension abbreviations
  such as `14,6l.` are not treated as litres.
- Locked the existing display behavior that keeps detail-bullet BSR raw
  evidence while excluding the ranking-specific label from derived product
  details. No rank value is inferred from a number in raw evidence.
- Focused verification: `tests/test_translation_preclean.py`,
  `tests/test_source_fields.py`, `tests/test_source_fields_production.py`, and
  `tests/test_full_detail_render.py` passed (`44` tests). No full source audit
  or translation run was performed.

## Authorized next task — optional-field damage isolation

The user approved a separate, later builder task to retain damaged raw source
but isolate its corresponding optional canonical field as empty with
`EVIDENCE_UNAVAILABLE`; it must not mark those fields PASS or suppress other
blockers:

- `B015YK51H2`: damaged `Marca=Bons�i`; no independently intact brand evidence.
- `B017WK9SSK`: damaged manufacturer `TulipÃ¡n negro`; the intact `Marca` must
  not be substituted as manufacturer.
- `B08BYLMK7C`: damaged speaker-type values `Port�til`; no replacement source.

This isolation has not been implemented in the current patch.

## Follow-on locale-price patch

- Fixed Spanish grouped-price parsing for the explicit `1.499,00 €` form.
  The parser removes dots only when the complete token is unambiguously
  Spanish grouped-thousands plus comma-decimal notation; bare three-decimal
  forms such as `1.499` and `1,499` remain rejected as ambiguous.
- Root cause evidence: `B0DRFZ8C31` has raw current `899,00 €` and raw
  original `1.499,00 €`. The old parser returned `None` for the original.
- In the same `5480` record-binding scope, the previous `3093` result was a
  parser defect, not a scope difference. The fixed in-memory normalization
  yields `3094` raw legal `original > current` records and `3094` matching
  canonical discounts, with zero invalid `original <= current` conflicts.
- Focused verification: `tests/test_price.py` and `tests/test_pipeline.py`
  passed (`32` tests). No full source audit was run.

The current self-parent clearing remains unchanged. Any later builder work
that needs to retain a self-parent must bind the decision to saved-cache HTML
and parser-produced variation evidence; an ordinary dict/status remains
insufficient.
