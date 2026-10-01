# Translation V2

Translation V2 is an opt-in, field-level Spanish-to-Chinese pipeline. It is
separate from the legacy `translate-ds` command and does not modify the old
DeepSeek or deterministic translation modules.

## Data flow

`ASIN + Spanish source field` → source hash → protection → provider → token
restore and QA → field cache → display translation. Raw Spanish fields are
never overwritten. A field cache key includes ASIN, field, source hash,
provider, model, schema version and prompt version. A separate persistent TM
key uses source hash, language pair and field type, so the same category or
attribute text can be reused by a different ASIN and a later process.

## Provider and credentials

The default provider is `qwen-mt` with model `qwen-mt-flash`. Credentials are
read from `QWEN_API_KEY` or `DASHSCOPE_API_KEY`; no key is stored in source,
config or cache. The request protocol is explicit (`openai_compatible` or
`dashscope`) and uses Qwen-MT's `translation_options` with Spanish → Chinese.
The endpoint can be configured in `configs/translation_v2.json` or with
`QWEN_API_ENDPOINT` / `DASHSCOPE_API_ENDPOINT`. When running from the isolated
worktree, set `PYTHONPATH=<worktree>\\src` so the CLI does not accidentally load
an older editable install from another checkout. The default serial limiter is
`rate=0.5` calls/second; `--rate` or config can override it. Retry backoff is
5 seconds, then 10 seconds by default.

## CLI

First inspect the work without making a network request:

```text
amazon-es --offline translate --products outputs/products.json \
  --config configs/translation_v2.json --out outputs/translation_plan.json --dry-run
```

Real calls require an explicit `YES` confirmation, or `--yes` in an approved
noninteractive run:

```text
amazon-es translate --products outputs/products.json \
  --config configs/translation_v2.json --cache outputs/translation_v2_cache.json \
  --out outputs/translations_v2.json --qa-out outputs/translation_qa.json
```

Use `--repair-partial` and `--repair-failed` to retry only those field states;
`--limit` and `--offset` support bounded batches. The command is serial and
does not bypass Amazon or provider access controls.

## Statuses and recovery

Fields use `pending`, `success`, `cached`, `partial`, `failed`, `source_missing`
and `qa_failed` semantics. A provider failure on one field does not discard
other fields. Cache writes use a temporary file, fsync and atomic replace. A
corrupt cache is preserved with a `.corrupt-*` suffix and the run starts with
an empty cache rather than silently accepting malformed data.

`product_details` structured attributes keep label order and translate values
item-by-item; structured bullet arrays/newline bullets keep their boundaries
and order. Known, fully structured specifications use the existing deterministic
rules before any provider request; unknown/non-deterministic text remains
eligible for the configured provider.

`translation_qa.json` records numeric/unit/token, added-number, brand,
bullet-count and conservative residual-Spanish findings with stable zero-filled counters. Optional
`--audit-out` writes one JSONL row per translated field, including source hash,
provider, model, status and QA status. Existing deterministic rules from
`zh.py` and `full_detail.py` are applied as a terminology post-processing layer;
they do not overwrite Spanish evidence.

The confirmation line reports SKU count, field count, cache hits, source-missing
records and the estimated provider request count. Category text shares one TM
namespace across category levels, so identical Spanish labels are translated
once per run. Structured detail values and bullet items use item-level TM and
are counted item-by-item by dry-run.

## Migration and compatibility

`translate-ds` and its ASIN-level DeepSeek cache remain unchanged. Translation V2
does not silently import or rewrite that cache: start with a separate
`translation_v2_cache.json`, and let source hashes populate field entries and
the persistent TM incrementally. Existing `products.json`, Spanish evidence,
and Excel export inputs remain valid; V2 emits a flat Chinese overlay plus an
auditable `fields` envelope for downstream integration.
