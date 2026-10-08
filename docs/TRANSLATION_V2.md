# Translation V2

## Production V1 status addendum

Translation/dictionary/rerender are implemented and tested offline, but this
release made **zero** Provider API calls. Provider 0 is not live-verified.
Formal release requires real provider provenance plus field-level
source/target/context/version/hash binding and Chinese QA; cached or sealed
PASS data alone is insufficient.

Translation V2 is an opt-in, field-level Spanish-to-Chinese pipeline. It is
separate from the legacy `translate-ds` command and does not modify the old
DeepSeek or deterministic translation modules.

## Data flow

`Raw Spanish` → `Pre-Clean` → `Dictionary / Rules` → `Identity Protection` →
`Translation Memory` → `Provider Pool` → token restore and QA → exception
queue → display translation. Raw Spanish fields are never overwritten. A field
cache key includes ASIN, field, source hash, provider, model, schema version
and prompt version. A separate persistent TM key uses source hash, language
pair and field type, so the same category or attribute text can be reused by a
different ASIN and a later process.

Pre-Clean is deterministic and offline. It emits
`translation_input_records.json` and audit CSV/JSON/Markdown reports; only
`CLEAN` and `NORMALIZED` fields have `translate_allowed=true`. Review states
remain in `review_queue.csv` and are not silently repaired. TranslationService
can consume this derived record shape directly and refuses fields whose
`translate_allowed` flag is false. The audit also reports an offline workload
estimate; the dictionary stage remains explicitly separate.

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

The reviewed dual-provider mode accepts aliases without storing keys in the
configuration file:

```json
{
  "providers": [
    {"name": "qwen-a", "type": "qwen-mt", "endpoint_env": "QWEN_A_ENDPOINT", "api_key_env": "QWEN_A_API_KEY", "model": "qwen-mt-flash", "rate": 0.5},
    {"name": "qwen-b", "type": "qwen-mt", "endpoint_env": "QWEN_B_ENDPOINT", "api_key_env": "QWEN_B_API_KEY", "model": "qwen-mt-flash", "rate": 0.5}
  ],
  "max_workers": 2
}
```

`ProviderPool` uses bounded round-robin assignment with a per-provider request
lock, in-flight task deduplication, shared successful results, bounded failover
for received 429/5xx responses and provider health states. QA failures do not
trigger failover. Timeout, transport exceptions and HTTP 599 have an unknown
send outcome: preserve pending without internal retry, failover or implicit
resubmission. Other independent tasks may continue on healthy providers.

### Three existing credential aliases

`configs/translation_v2_three_existing.json` explicitly maps the existing
credential variable names. It does not contain credentials or infer that keys
belong to the same host:

| Alias | Credential variable | Endpoint variable | Optional base URL |
| --- | --- | --- | --- |
| QWEN_A | QWEN_API_KEY | QWEN_API_ENDPOINT | QWEN_MT_BASE_URL |
| QWEN_B | DASHSCOPE_API_KEY | DASHSCOPE_API_ENDPOINT | QWEN_MT_BASE_URL (shared fallback) |
| QWEN_C | QWEN_THIRD_API_KEY | QWEN_THIRD_API_ENDPOINT | QWEN_MT_BASE_URL (shared fallback) |

The third endpoint name is an optional dedicated override, not evidence that
it is configured. Missing all configured endpoint sources blocks dispatch before key values
or transports are loaded. Strict dry-run enumerates credential variable names
only, records non-sensitive missing mappings, and never reads credential values.
This configuration requests three workers and zero provider-internal retries.
No system environment variables are changed by loading or preflighting it.

For the single adapter, precedence is explicit endpoint, `QWEN_API_ENDPOINT`,
`DASHSCOPE_API_ENDPOINT`, `QWEN_MT_BASE_URL`, then the existing public default.
An explicit model wins over `QWEN_MT_MODEL`, then the default. Strict pool
aliases use their own explicit endpoint or endpoint variable, then the global
configured endpoint, `QWEN_API_ENDPOINT`, `DASHSCOPE_API_ENDPOINT`, their declared
base URL variable, and shared `QWEN_MT_BASE_URL`, in that order. They retain
their explicitly mapped credential variables even when sharing an address.
Strict preflight never uses the unconfigured public default. A malformed
configured address blocks dispatch rather than falling through to another address.
Per-alias model, model environment, global configured model, then the default
are used in that order. Base URL paths `/compatible-mode/v1` and `/v1` append
`/chat/completions`; a complete chat path remains unchanged. HTTPS, no embedded
credentials/query/fragment and a recognized path are required. Unknown base
paths fail closed; host ownership is not inferred or verified by an offline
preflight. Legacy review and field-closure gates remain necessary before live use.
Pool stats expose adapter dispatches as `requests` and transport attempts as
`http_attempts`; retries/failover may make attempts exceed dispatches. A dry-run
estimate counts unique semantic dispatches before retries, not batches or cost.

## CLI

First inspect the work without making a network request:

```text
amazon-es --offline translate --products outputs/products.json \
  --config configs/translation_v2.json --out outputs/translation_plan.json --dry-run
```

Run the required offline preparation separately:

```text
amazon-es --offline preclean --products outputs/scale_4500_final/master/sku_list_7365_internal_research.csv \
  --out-dir outputs/translation_v2_preclean
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

Enable the configured dual pool explicitly with `--parallel-providers`. Dry-run
never creates API requests; real runs print both aliases, model/rate, worker
count and still require `YES` confirmation.

## Statuses and recovery

Fields use `pending`, `success`, `cached`, `partial`, `failed`, `source_missing`,
`preclean_blocked` and `qa_failed` semantics. A provider failure on one field does not discard
other fields. Cache writes use a temporary file, fsync and atomic replace. A
corrupt cache is preserved with a `.corrupt-*` suffix and the run starts with
an empty cache rather than silently accepting malformed data.

The current schema (`translation-v2.25`) treats repeated facts across fields as
non-blocking audit warnings, permits the title display policy to omit the
separately stored brand, and validates source/target negation parity. Detail
labels are dictionary-backed with unknown Spanish labels retained verbatim;
raw Spanish evidence is never overwritten. The schema bump invalidates older
cache entries so these rules are applied on the next run.

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
