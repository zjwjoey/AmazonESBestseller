# Production Release Gate V1

`amazon_es_bestseller.production.release.evaluate_release_gate(artifacts)` is
the only formal Production V1 release decision. It is offline and read-only:
it neither repairs facts nor exports workbooks.

Every input other than `spanish_master` is a sealed artifact created by
`seal_artifact(kind, payload, version="v1", evidence={})`. The gate verifies
both the payload hash and envelope hash before looking at the payload. A plain
`passed: true`, stale hash, missing version, or foreign artifact type blocks
formal release.

Required artifact keys and payloads:

- `spanish_master`: result from `build_spanish_master`; its native artifact
  hash is rechecked.
- `ranking_authority`, `detail_identity`, `offline_replay`: `{check, status:
  "PASS", summary: {records_checked: <master SKU count>}, issues: []}`.
- `source_audit` and `source_gate`: the actual audit plus gate result. The
  gate recomputes their binding with `verify_source_gate`.
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
- `chinese_qa` and `chinese_gate`: all Master ASINs PASS and gate status
  `SKU_ZH_READY`.
- `spanish_output` and `chinese_output`: ordered one-per-ASIN canonical rows.
  ASIN order/set, product URL, image URL and human notes must exactly match
  the Spanish Master.

`formal=False` returns `DRAFT`; it never returns READY. `debug=True` and
`force=True` both block a formal release. `export_ready(artifacts, exporter,
output_path)` always calls the gate itself and only gives the exporter a deep
copy of canonical records, with Master notes retained. It is intentionally not
connected to a CLI here; the orchestrator should seal real stage artifacts and
call this function rather than passing a caller-created READY flag.
