# Translation Preparation V1 field contract

Version: `translation-preparation-v1.0`. This is an offline input contract, not
a translated product or a model request. No collector or translation service
is started by this package. Source records are retained byte-for-byte in the
snapshot and as original objects in `raw_record`.

## Identity and evidence

`candidate_id` + `selection_id` trace a selected URL task, while ASIN is the
confirmed product identity. Preserve both: do not replace candidate IDs with
ASINs or merge variant tasks sharing a parent. Every unit binds an audit
manifest, snapshot manifest, source HTML hash and task receipt. A copied
snapshot is consistent at observation, not proof that the producer has stopped.

## Fields and routing

| Source | Routing |
| --- | --- |
| title_es_raw | complete title unit |
| full Amazon breadcrumb | one unit per level with full parent path; no three-level truncation |
| selected_variation_raw | separate variant unit |
| specification_es | reuse existing Spanish builder in variant → exact title → explicit attribute priority passes; unbound historical summaries held |
| feature_bullets_raw | ordered unit per bullet, repeated bullets retained |
| product_description_raw | complete source text with line breaks retained |
| attributes | ordered label/value units, section/source/position/index retained |
| attribute identity/code/brand/model or pure numeric quantity | PASSTHROUGH, no model |
| candidate/selection/ASIN/URLs/images/prices/rating/counts/brand/hash/time | original record retained, no model |

`Color: Natural` and `Material: Natural` have different semantic namespaces
using `translation.field_contract.canonical_translation_unit_field`. Category
parent paths are additionally part of semantic reuse identity. Repeated
attributes have different unit IDs even when their semantic reuse key is equal.
Per-product cache identity retains ASIN, candidate ID, source hash, schema and
glossary version. Future consumers must additionally bind provider/model/prompt
versions before writing to Translation V2; these are not production cache keys.

Each unit contains `translation_unit_id`, candidate/selection/ASIN, field,
attribute section/position/label/role/source, exact `source_text_es`, SHA256,
semantic field type, glossary content version, protected terms, source evidence,
preparation status, reuse key, and `translation_result=null`.

Statuses: READY, PARTIAL_READY, SOURCE_REVIEW_REQUIRED, IDENTITY_BLOCKED,
SOURCE_BLOCKED, NOT_READY at product level; READY, FIELD_MISSING,
SOURCE_NOT_DISPLAYED, SOURCE_REVIEW_REQUIRED at field level. Dictionary/cache
reuse availability and PASSTHROUGH are unit routes, not completed translations.

Severe identity/evidence errors block the product. Broken attributes block
that field, not independent valid fields. No field is filled to improve rates.
Source-not-displayed requires complete saved HTML with no owned source node;
empty values alone are insufficient. Prices are never translation units.

## Dictionary and cache protocol

Reuse checked-in labels, colors and materials in their own semantic namespace.
Record counts, SHA256 and normalized collisions for all dictionaries including
units, packaging, categories and protected terms. Flat category mappings are
unapproved hints, never automatic path-aware resolution. Unknown terms remain
ready for a future explicitly authorized translator. No dictionary is edited.

Optional `--cache-copy` accepts only an explicitly supplied immutable review
copy shaped as `{ "contract_version":"source-audit-verified-memory-v1",
"entries": { "<semantic_reuse_key>": { "semantic_reuse_key":"...",
"source_text_sha256":"...", "translation_text":"...",
"translation_sha256":"...", "status":"success", "qa_status":"pass" } } }`.
Every available entry is exact-semantic/hash matched. Legacy TranslationCache
is NOT instantiated: its corrupt-cache recovery can rename files. Historical
cache migration requires a separately audited adapter; no blind text-only hits.

## Publication and integration

All bundles publish an input-bound manifest last, containing code commit,
working code hashes, rule version, source references and all output hashes.
Identical reruns are idempotent; changed bytes fail closed without overwrite.
Audit requires a snapshot manifest and refuses input/output overlap or path
escape. Outputs are proposals: source repairs require re-audit before a repaired
field may enter translation. No production database, evidence, notes, Excel or
translation cache is overwritten. Any subsequent model call or production
integration belongs to the original Detail5500 task after human approval.
Derived specifications carry source-field, source hash, output text hash and
method evidence separately; they never overwrite the original raw record.

