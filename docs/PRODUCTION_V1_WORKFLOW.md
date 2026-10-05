# Production V1 evidence workflow

`amazon-es --offline production-run` now accepts a `TaskConfig`, not a map of
self-declared stage results. It executes the existing V1 modules in this
order:

```text
saved ranking evidence -> immutable ranking snapshot -> DetailPlan
  -> saved-detail reparse -> normalize -> source audit -> Spanish Master
```

The `source-only` profile ends at Spanish Master and is always labelled
`DRAFT_SOURCE_ONLY`. It does not produce a release-ready workbook.

## Minimal offline task

```json
{
  "task_id": "offline-example",
  "mode": "initial",
  "network_mode": "offline",
  "history_dir": "history",
  "source": {
    "ranking_evidence": "ranking_evidence.json",
    "detail_html_dirs": ["saved_detail_html"]
  },
  "translation": {"provider_mode": "fake"}
}
```

`ranking_evidence.json` supplies `records`, `planned_sources`, and page-level
`source_statuses`. The snapshot stage rejects missing or non-authoritative
source status. Detail HTML is reparsed through the V1 parser; a plan requiring
a network fetch fails as `OFFLINE_NETWORK_ACTION_REQUIRED`.

The JSON history repository stores immutable ranking snapshot receipts in
`ranking_snapshots.jsonl`, cache/detail state in `details.json`, and the
current Spanish Master in `spanish_master.json`. Incremental runs compare the
latest receipt and retain `NEW`, `REMOVED`, `RANK_UP`, `RANK_DOWN`,
`UNCHANGED`, `CONTEXT_ADDED`, and `CONTEXT_REMOVED` events. Spanish Master
promotion receives the existing master so human `notes` remain ASIN-owned.

Each stage persists a producer artifact plus hash-bound `runmanifest.json`,
`progress.json`, `summary.json`, `errors.jsonl`, and the stable operational
`run_manifest.json`. Any changed config or evidence fingerprint invalidates a
completed stage and fails closed; it never partially reruns over immutable
artifact files.

Formal translation/release is intentionally not available under the offline
fake provider. It requires the real Qwen budget/provenance path and the single
formal release gate before Excel export.
