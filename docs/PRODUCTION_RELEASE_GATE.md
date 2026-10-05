# Production Release Gate V1

`amazon_es_bestseller.production.release.evaluate_release_gate(artifacts)` is
the only formal Production V1 release decision. It is offline and read-only:
it neither repairs facts nor exports workbooks.

Every input other than `spanish_master` is a sealed artifact created by
`seal_artifact(kind, payload, version="v1", evidence={})`. The gate verifies
both the payload hash and envelope hash before looking at the payload. A plain
`passed: true`, stale hash, missing version, or foreign artifact type blocks
formal release.

A seal only proves transport integrity; it is not proof that a stage ran. For
`ranking_authority`, `detail_identity`, and `offline_replay`, adapters must
also use `build_stage_evidence(spanish_master, stage)` and emit a material
report with `produced_stage`, `report_schema_version`, that exact
`evidence_ref`, `summary.records_checked`, and one PASS row per ASIN containing
the exact `record_hash`. The gate recomputes those hashes from the native
Spanish Master. A resealed empty report, ASIN-only PASS report, stale count,
or report generated for another parser/schema/raw record is blocked.

Required artifact keys and payloads:

- `spanish_master`: result from `build_spanish_master`; its native artifact
  hash is rechecked.
- `ranking_authority`, `detail_identity`, `offline_replay`: `{check, status:
  "PASS", summary: {records_checked: <master SKU count>}, issues: []}`.
- `source_audit` and `source_gate`: the actual audit plus gate result. The
  gate reconstructs source rows from each Master record's immutable
  `raw_source` and ranking context, reruns `audit_source_fields`, checks every
  record/raw-evidence hash and parser/schema binding, then recomputes the gate.
- `field_closure`: `{check: "field_closure", ...}` with no P0/P1 finding.
- `translation`: `state` must have a READY release candidate, complete
  hash-bound input manifest and promoted fields for every Master ASIN;
  `provider_provenance` requires a non-test provider/model, positive request
  count and `verified: true`; `execution.records` carry field dictionary
  versions.
- `dictionary_sync`: `completed: true`, a manifest with version/hash/schema,
  and rerender evidence. Every execution envelope and rerender must use the
  same dictionary version; selective repair or non-ready rerender prevents
  READY.
- `chinese_qa` and `chinese_gate`: QA covers every translated candidate field,
  not merely one row per ASIN. Each PASS must exactly bind `asin`, `field_type`,
  source hash, target value, context, dictionary version, translation schema
  version and candidate hash. The target must equal the Chinese output field.
  The Chinese gate carries the exact QA payload hash and field count.
- `spanish_output` and `chinese_output`: ordered one-per-ASIN canonical rows.
  ASIN order/set, product URL, image URL and human notes must exactly match
  the Spanish Master.

`formal=False` returns explicitly non-formal `DRAFT`; it never returns READY.
`debug=True` and `force=True` both block a formal release. `export_ready`
always calls the gate itself and, by default, calls the real `export_workbook`
with the research profile. It then verifies the frozen three-sheet layout and
the exact 25 Spanish / 26 Chinese columns before accepting the result.
Compatibility exporters must return a workbook passing the same check; a legacy
CLI `--force` export is never a formal release path.
