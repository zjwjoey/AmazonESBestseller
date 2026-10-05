# Production Source Gate and Spanish Master

`quality.source_fields.audit_source_fields` is an offline evidence audit. It
does not request Amazon, infer product values, or mutate input records.

Each inspected field is classified as `PASS`, `WARN`, `SOURCE_MISSING`,
`PARSER_MISSED`, `MAPPING_MISSED`, `DERIVED_MISSING`, `REVIEW_REQUIRED`, or
`BLOCKED`. `SOURCE_MISSING` is used only when evidence explicitly says Amazon
did not show the value. `PARSER_MISSED` means the evidence says it was exposed
but raw collection is empty. Missing evidence is never treated as a pass.

SKU decisions are `SOURCE_READY`, `REVIEW_REQUIRED`, and `BLOCKED`. P0/P1
facts prevent master promotion. P2/P3 coverage notices remain reportable and
do not manufacture missing source data.

`quality.source_gate.evaluate_source_gate` creates an audit-bound decision.
Its `audit_hash` is the canonical SHA-256 digest of source facts.
`verify_source_gate` recomputes it, so callers cannot promote a record by
passing an arbitrary ready/PASS value.

`production.spanish_master.build_spanish_master` is the only production
promotion entry point. It requires the exact audit plus its verified gate,
stores one canonical row per ASIN, preserves independent ranking contexts, and
keeps previous `raw_source` evidence immutable. A later collection is retained
as `observed_source`; it cannot overwrite the accepted source. Human `notes`
are retained by ASIN unchanged. The returned artifact includes stable
`artifact_hash`, source hash, parser/schema versions, audit status, collected
and observed timestamps, and run id. `verify_artifact_hash` validates it after
serialization.
