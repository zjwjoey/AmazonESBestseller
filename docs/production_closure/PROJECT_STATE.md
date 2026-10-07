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

## Spanish source closure candidate — r2

- Offline candidate builder output:
  `outputs/production_closure_20261007T000000Z_spanish_source_5480_r2`.
  It is a candidate artifact only, not Spanish Master and not `SOURCE_READY`.
- It binds the exact 5,480-ASIN reviewed source-audit scope to candidate
  manifest canonical hash
  `7774cc27bfccb7a1377bfc031103881085d1e8cdaf3018451e848f0505137414`.
  All selected detail records have `IDENTITY_MATCH`; ranking contexts remain
  independent and detail BSR remains raw evidence.
- Both the reviewed SourceGate and the rebuilt closure audit are `BLOCKED`
  (`ready=false`).  No translation, Excel export, source re-review, or formal
  Master promotion was performed.
- The source-only candidate has no `_zh`/Chinese display fields. It retains
  raw structured attributes and feature bullets, plus collection, ranking,
  detail-parser, and snapshot-hash provenance.
- Generic evidence rules blank the eight author/editorial/format bylines in
  the reviewed fixtures, prefer explicit `Marca=Bontempi` for `B01NBM854W`,
  preserve raw evidence for the three approved damaged optional fields, and
  clear all `845` unproven self-parents (`0` retained; status `unconfirmed`).
- Current unresolved blocker counts are reported in `audit.md` and
  `source_review_queue.json`; they are not suppressed by this candidate.
- Focused verification: translation pre-clean, source fields/production
  source fields, and source-closure tests: `34 passed`.

The earlier non-r2 closure directory is retained as an immutable, superseded
failed evidence attempt: its brand rule was too broad and it is not the output
to use for review.

## Category/provenance mapping correction (no full r3 run yet)

- The r2 builder allowed `normalize_product` to enrich blank ranking L3/leaf
  values from `detail_category_trail`, and omitted `category_provenance`.  This
  inflated r2 diagnostic `CATEGORY_COPIED` findings; it did not establish a
  new reviewed taxonomy.
- The builder now restores frozen selected-ranking `category_l1/l2/l3/leaf`
  and `browse_node_id` after normalization, retains the detail breadcrumb as
  raw evidence only, and attaches a hash-bound ranking-context provenance map.
  `research_category` remains ranking/task evidence.
- Source-field audit now accepts `leaf_category == category_l3` only if a
  matching ranking category path and provenance map support it. It continues
  to block L1=L2 or L2=L3 fill-downs and unsupported L3=leaf duplicates.
- In-memory diagnosis only (no full r3 artifact): all `5480` frozen records
  have ranking URL provenance; `464` have a provenance-supported L3=leaf; the
  corrected copied-hierarchy predicate finds `0` records.
- The proposal artifact was not missing: it remains read-only at
  `F:\AmazonESBestseller\.worktrees\production-v1-complete\outputs\amazon_es_bestseller_5500_202610_fresh2\source_audit_5480\category_l3_backfill_proposals.json`.
  It is `REVIEWABLE_NOT_APPLIED` and is not consumed by this change.

## Spanish source closure candidate — r3 diagnostic

- Immutable output: `outputs/production_closure_20261007T000000Z_spanish_source_5480_r3`.
  It remains `CANDIDATE_SOURCE_GATE_BLOCKED`; no Master promotion, new
  authoritative SourceGate, translation, or Excel export was performed.
- Its manifest separates records canonical dataset hash
  `4ff1b2388db3f7c19271a02551e2c55e011888df3887760d1c0efefda1d5bd46`
  from frozen candidate-manifest canonical hash
  `7774cc27bfccb7a1377bfc031103881085d1e8cdaf3018451e848f0505137414`.
- The current closure audit has `1109` findings: `247` blocking P0/P1 across
  `177` SKUs (`246` unit-type mismatches and `1` field-misplacement); `782`
  REVIEW findings across `648` SKUs (`694` ambiguous-unit semantics, `72`
  misplaced text, `16` rank gaps); and `96` P2 findings, excluded from the
  blocking count. `CATEGORY_COPIED=0` and
  `CATEGORY_PROVENANCE_MISSING=0`.
- `audit.md` now lists current closure code/severity and field/severity counts,
  BLOCK/REVIEW disposition, and excludes P2 from the blocking total. The
  pre-existing reviewed input is copied separately as
  `historical_source_audit.json`; it is history only.

## Unit-semantics diagnostic and owner exclusion scope

- The audit accepts only explicit, domain-bound unit meanings for airflow
  (`m³/h`), drill capacity (length), hand-gripper resistance (title-bound),
  `cc/cm³` capacity, and battery count plus voltage. It never rewrites raw
  label/value evidence.
- Contradictory labels such as battery-capacity with volts, weight-capacity
  with litres, liquid-volume with kilograms, and voltage with watts are kept
  as `SOURCE_SEMANTIC_CONFLICT` review findings. Generic `Tamaño` or
  unit-count measurements pass only when the exact numeric/unit measure is
  independently present in the title or selected variation; otherwise they
  remain review findings.
- Programmatic diagnostic queue (not a formal Gate rerun):
  `outputs/production_closure_20261007T000000Z_unit_semantics_5480/remaining_unknown_conflict_queue.json`.
  It is bound to the r3 5,480-record dataset hash
  `4ff1b2388db3f7c19271a02551e2c55e011888df3887760d1c0efefda1d5bd46`
  and preserves issue severity, source hash, and evidence locator. It has
  `385` P1 REVIEW entries: `59` `SOURCE_SEMANTIC_CONFLICT` and `326`
  `UNIT_SEMANTICS_AMBIGUOUS`.
- Separate derived owner scope:
  `outputs/production_closure_20261007T000000Z_owner_scope_5478/owner_exclusions.json`.
  It is hash-bound to the same 5,480-record parent and contains only
  `B07F6LYVT6` and `B077H1MZ35`; effective scope is `5478`, no replacements
  are added, and raw damaged special-function evidence remains in the parent.
- The runtime extraction issue register is referenced, not modified, at
  `outputs/production_closure_issue_register_20261007T074352Z`. It remains a
  future collector-improvement input; this audit work neither repairs nor
  hides upstream extraction errors.
- Independently reported P1 follow-ups are recorded but **not executed** in
  this slice: verify category leaf provenance against the actual ranking
  contexts and strengthen tag-aware strict-text detection while preserving
  literal `<M>` / `<USB-C>` product text. No historical or new SourceGate was
  promoted or marked ready.

## P1 provenance and strict-text follow-up

- Category provenance now passes `leaf_category == category_l3` only when its
  stored canonical context hash, URL, page, category path, and hierarchy all
  match an actual `ranking_contexts` entry on the same record. Missing contexts
  or a forged hash remain `CATEGORY_COPIED`; no other product or ranking list
  can supply the evidence. A top-level ranking context with a non-empty source
  path is valid even without a browse node.
- Source-field strict text detection now shares the translation pre-cleaner’s
  known-element tag rule. Escaped real `<a>` / `<p>` markup is reviewable,
  while ordinary product wording such as `Molde para cookie de Navidad` and
  literal `<M>` / `<USB-C>` tokens are preserved. This changes audit detection
  only; it does not repair raw evidence.
- A historical reviewed `SOURCE_READY` audit is insufficient for promotion if
  the current closure audit is REVIEW/BLOCK: the candidate status is explicitly
  `CANDIDATE_CLOSURE_GATE_BLOCKED`. No promotion API, translation, or export
  was invoked.
- The runtime extraction issue register remains referenced only at
  `outputs/production_closure_issue_register_20261007T074352Z`; this slice
  does not change collector behavior or its raw errors.
- The requested rank-matrix/subset diagnostic is intentionally deferred to a
  separate small slice to avoid combining rank scope semantics with these P1
  evidence and text-boundary changes.

## Current SourceGate chain and 5,478 owner scope

- Current-gate evidence binds candidate, detail, ranking, parent, and owner-scope hashes before selecting the exact 5,478 ASINs. Historical audits remain history only.
- `CANDIDATE_CURRENT_GATE_READY` is still a candidate, never a reviewed Master; any current P0/P1/REVIEW yields `CANDIDATE_CURRENT_GATE_BLOCKED`.
- Complete rank-matrix diagnostics mark owner/exact-scope omissions as `OUT_OF_EXACT_SCOPE` with their exclusion reference, while real matrix gaps remain REVIEW. No rank changes.
- Generic unit PASS evidence now includes the exact numeric/unit signature, corroborating source field/hash, attribute label/value hash, and same-ASIN binding. Changed title/variation evidence no longer supports the old explanation.
- The attempted full 5,478 local diagnostic emitted no artifact under the current process resource limit; it remains `NOT_EXECUTED`, not asserted READY, and needs a higher-memory rerun.
