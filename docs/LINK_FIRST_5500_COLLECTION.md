# Link-first 5500 collection (opt-in candidate workflow)

This is a new explicit runtime contract, not a modification of the formal
ASIN-frozen scheduler. The reviewed source plan remains immutable and must pass
the existing registry fingerprint and source-snapshot validation before a browser
is created. Existing `task-collect` ranking/detail contracts remain unchanged.

## Run contract

- Ranking captures product href, source/page/visible rank/category and HTML only.
  No ASIN field is extracted into the ranking snapshot. Temporary occurrence IDs
  are not canonical SKU identities.
- Navigate the original product href. Resolve the selected detail child identity
  using corroborated input/current/canonical/final-page evidence. Visible parent
  ASINs remain raw evidence and produce review warnings. Unrelated redirects or
  conflicting identities are quarantined and cannot inherit ranking positions.
- Capture primary sources first. Same-category reviewed reserve sources become
  active only when confirmed unique detail ASINs cannot meet a category quota.
- All category/detail requests are serial. Keep the reviewed serial fallback
  cooldown (1800 seconds) between categories, including the small-sample stage.
  The diversified 50-item sample therefore has substantial cooling time; it is
  not a promise of a short 50-request job.
- The sample covers all 15 categories and source diversity. Zero structural or
  binding/parser omissions and at least 90% successful attempts are required for
  automatic full continuation. Source missing values are not filled or counted
  as parser defects. Individual identity/ordinary-load failures remain explicit.
- Checkpoints, raw HTML, immutable snapshot manifests, attempt results and code/
  plan/source hashes are persisted. Successful same-run occurrence checkpoints
  resume without refetching. Historical detail caches are never counted as new
  successes. Code/hash drift fails closed; repair requires audited offline
  reparse/migration, not an override or silent overwrite.
- Challenge/403/429/CAPTCHA/access denied holds the process and visible browser;
  no requests, refreshes, clicks or automated solving occur while held. Explicit
  operator control bound to the current `hold_token` is necessary after human
  handling. A normal load error may reopen the current URL once only.

## Staged audit

1. Snapshot: safe original product link, source/page, explicit badge rank and
   HTML hash. Two pages per reviewed source, no rank fabrication.
2. Each detail: selected identity; raw schema; independent row-local DOM key/value
   pairing and duplicate-key order; all feature/detail bullets; missing visible
   title/price/rating/image/review fields; evidence-based category depth.
3. Each 100 attempts: status/field-empty/error counts. Three consecutive matching
   structure/parser/binding errors pause for offline repair.
4. Derived Spanish fields: deterministic numeric/date normalization, specification,
   full key/value display and bullet display. Ambiguous source units, contradictory
   values and missing derived/mapped fields remain recorded, not guessed.
5. Final: detail-ASIN global dedup, exact total/per-category quotas and source caps,
   all successful ranking contexts preserved; formal Source Audit unchanged.

The light collection audit is **not** the formal Source/Closure/Release Gate.
Final output stays candidate-only even when collection reaches 5500. A quota
shortfall cannot be disguised by duplicates, other categories or reducing scope.
No Qwen/provider translation, Git commit/push or old-manifest changes occur.

## Explicit entry point

```powershell
python tools/link_first_collection.py `
  --plan configs/tasks/amazon_es_bestseller_5500_202610_plan.json `
  --out-dir outputs/amazon_es_bestseller_5500_20261010_linkfirst `
  --profile-dir outputs/amazon_es_bestseller_5500_20261010_linkfirst/browser_profile `
  --sample-size 50 --continue-after-sample
```

Use `--prepare-only` for a no-network preflight. Use `--resume` only for that same
run with unchanged hashes. There is no `--force` or formal gate bypass.

Outputs: `run_metadata.json`, `reviewed_plan_copy.json`, `progress.json`,
`ranking_snapshots/*/manifest.json`, `detail_evidence/*/result.json`,
`staged_audit/*.json`, `sample_gate.json`, `spanish_candidate.json`,
`resolved_ranking_contexts.json`, `detail_resolved_manifest.json`,
`source_audit.json`, `collection_report.json`. The last files exist only after
the relevant stage completes; a running background process is not completion.
