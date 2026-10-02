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
canary / translate (optional real API)
    ↓
QA + repair queue
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
