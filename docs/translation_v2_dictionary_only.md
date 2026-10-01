# Translation V2 dictionary-only

`dictionary-only` is a fully offline preparation mode for the large Spanish
Amazon.es dataset. It profiles source fields, extracts high-frequency
dictionary candidates, and resolves only high-confidence deterministic values.
It does not construct Qwen, DeepSeek, or any other translation API client.

Example:

```powershell
$env:PYTHONPATH = "src"
python -m amazon_es_bestseller.cli dictionary-only `
  --products F:\AmazonESBestseller\outputs\scale_4500_final\master\sku_list_7365_internal_research.json `
  --out F:\AmazonESBestseller\outputs\translation_v2_dictionary
```

The independent output directory contains:

- `field_profile.json` / `field_profile.csv`: source-field statistics and high-frequency values;
- `dictionary_candidates.csv`: typed candidates with frequency, ASIN samples, and context;
- `category_review_candidates.csv`: category values not yet in the curated dictionary;
- `unresolved_high_frequency.csv`: unresolved long-tail input for the next dictionary pass;
- `dictionary_only_results.json`: per-ASIN field results retaining `source_text`;
- `dictionary_coverage.json` / `dictionary_coverage.md`: counts, rates, and API-unit savings.

Resolution provenance is explicit: `dictionary`, `rule`, `source_preserved`,
`protected`, `unresolved`, or `source_missing`. Unresolved values retain their
Spanish source and are not converted to empty strings.
