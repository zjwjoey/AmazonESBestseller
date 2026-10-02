# Translation V2 Production Integration

This integration keeps the Spanish 5000-SKU Master immutable and treats
Translation V2 as a downstream derived layer. The 700-SKU corpus remains a
regression namespace; it is never copied into production state.

```text
Spanish Master
    ↓
build-input (manifest + source hashes)
    ↓
preclean / dictionary / translation memory
    ↓
plan (offline)
    ↓
canary / translation shards (optional real API)
    ↓
aggregate + QA + repair queue
    ↓
promote (only QA-passed values become final_zh)
    ↓
existing Excel release gate
```

## Stage commands

```powershell
python -m amazon_es_bestseller.cli translation-production `
  --stage build-input --master <spanish-master.json-or-csv> `
  --run-id production-20261002

python -m amazon_es_bestseller.cli translation-production `
  --stage preclean --run-id production-20261002

python -m amazon_es_bestseller.cli translation-production `
  --stage plan --run-id production-20261002 `
  --config configs/translation_v2_production.example.json

# Real API calls require an explicit --yes and can be bounded with --limit,
# --offset, --asin-list or --category.
python -m amazon_es_bestseller.cli translation-production `
  --stage translate --run-id production-20261002 --limit 100 --yes `
  --config configs/translation_v2_production.example.json

python -m amazon_es_bestseller.cli translation-production `
  --stage promote --run-id production-20261002 `
  --config configs/translation_v2_production.example.json
```

Production artifacts are written below
`runtime/translation_v2/production/<run_id>/`:

- `input_manifest.json` and `translation_input.json`
- `preclean/`
- `plan/`
- `translations/`
- `translations/shards/batch_<id>.json` (immutable, auditable batch output)
- `translations/translation_results.json` (field-level aggregate; never the only history)
- `state/translation_state.json`
- `repair/repair_queue.json`
- `release/production_release_candidate.json`

The example configuration keeps three Qwen-MT provider slots, each at
`0.5` requests/second and `max_workers=3`. Credentials are read only from
environment variables. A content-safety/data-inspection 400 is recorded as a
field-level policy block and does not disable the provider for unrelated SKUs.

Qwen-MT guidance is sent through the supported `translation_options` fields:
field-specific English domain guidance and only the relevant reviewed terms.
It does not prepend an unsupported chat-style system message to the Spanish
source. The prompt version participates in cache and translation-memory keys.

The promotion gate preserves candidates and QA evidence, but only a field with
`translation_status=success` (or `cached`), `qa_status=pass`, and no QA issues
gets `promotion_status=PROMOTED` and a non-null `final_zh`.

## Source preservation and batch recovery

CSV `build-input` keeps every original column in `source_record`; the
canonical translation projection is added as aliases and never replaces the
source row. `ASIN` is the identity and duplicate or missing values fail fast.
Each input has a field-level `source_hash`, record-level
`source_record_hash`, and manifest `dataset_hash`.

Every real translation batch writes an immutable shard. Aggregation merges on
`ASIN + target_field`, so a later repair field cannot erase earlier fields.
An identical batch is idempotent. A changed source hash is retained as
`SOURCE_CHANGED` with old evidence and cannot be promoted silently.

`--asin-list` accepts a JSON string array, a JSON array of `{"asin": ...}`
objects, a JSON file containing either form, or a comma-separated list. Values
are trimmed, upper-cased and deduplicated. Invalid elements fail with an
explicit error. `--category` checks all four levels: L1, L2, L3 and leaf.

## Release gate and export

The global release status is derived from every field promotion status, not
from whether `repair_queue.json` happens to be empty:

- `READY`: all non-empty source fields are `PROMOTED` (empty source is
  `SOURCE_MISSING` and remains empty).
- `REVIEW_REQUIRED`: QA or manual-review findings exist.
- `BLOCKED`: pending, pre-clean, provider, policy or source-change findings
  exist.

Formal Production Excel export is allowed only when the release candidate is
`READY` and the field-closure gate has no blocking findings. Production
`--force` is rejected. For diagnosis only, `--debug-export` writes a
`production_debug_unreleased.xlsx`-style artifact plus a
`release_status.json` marker with `formal_release=false`; it is not a release.

The 700-SKU corpus may provide regression coverage or a legitimate translation
memory hit, but its run state, cache namespace and release candidate are never
used as Production state.
