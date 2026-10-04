# Stable Research Pipeline

`stable-research` is the production entry point for the V1 collector and the
offline Quality Gate. It deliberately keeps collection and quality auditing as
separate stages:

```text
V1 ranking/detail collector
        ↓ saved JSON + saved HTML
offline Quality Gate
        ↓ quality_manifest.json + issue JSON/CSV + summary
RESEARCH_READY / REVIEW_REQUIRED / BLOCKED
```

## Profile

The reviewed profile is
[`configs/profiles/stable_research.json`](../configs/profiles/stable_research.json).
It enables V1 ranking/detail collection and all eight offline checks while ACP,
Ranking V2, Detail V2, translation and Excel export remain disabled.

## Offline audit

```powershell
python -m amazon_es_bestseller.cli quality-audit `
  --rankings outputs/rankings.json `
  --details outputs/details.json `
  --ranking-html outputs/runs/<run>/html `
  --detail-html outputs/html `
  --out-dir runtime/quality
```

The command makes no network request. Use repeated `--asin` or `--check` for a
focused audit. `--allow-non-ready` is intended for diagnostics only; without it,
`REVIEW_REQUIRED` and `BLOCKED` return exit code 2.

## Unified entry point

Offline use:

```powershell
python -m amazon_es_bestseller.cli stable-research `
  --offline --rankings outputs/rankings.json `
  --details outputs/details.json --out-dir runtime/stable_research
```

Live use still calls the existing V1 `collect` implementation and only then
runs the offline checks:

```powershell
python -m amazon_es_bestseller.cli stable-research `
  --urls "https://www.amazon.es/Best-Sellers/..." `
  --out-dir runtime/stable_research
```

The audit is read-only: it deep-copies input records, replays saved HTML with
the existing V1 parsers, and reports source hashes. It never repairs or
overwrites ranking/detail evidence.

## Checks and artifacts

The gate checks ranking identity and integrity, access evidence, detail
identity, offline replay, ordered detail structure, category provenance and
field closure. Each run writes individual check JSON files plus
`quality_manifest.json`, `run_manifest.json`, `quality_issues.json`,
`quality_issues.csv` and `summary.json` under a unique run directory.
