# Translation V2

Translation V2 is an opt-in, field-level Spanish-to-Chinese pipeline. It is
separate from the legacy `translate-ds` command and does not modify the old
DeepSeek or deterministic translation modules.

## Data flow

`ASIN + Spanish source field` → source hash → protection → provider → token
restore and QA → field cache → display translation. Raw Spanish fields are
never overwritten. A cache key includes ASIN, field, source hash, provider,
model, schema version and prompt version.

## Provider and credentials

The default provider is `qwen-mt` with model `qwen-mt-flash`. Credentials are
read from `QWEN_API_KEY` or `DASHSCOPE_API_KEY`; no key is stored in source,
config or cache. The request protocol is explicit (`openai_compatible` or
`dashscope`) and the endpoint can be configured in `configs/translation_v2.json`
or with `QWEN_API_ENDPOINT`.

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

`translation_qa.json` records numeric/unit/token and conservative residual
Spanish findings. Optional `--audit-out` writes one JSONL row per translated
field for audit/retry analysis.
