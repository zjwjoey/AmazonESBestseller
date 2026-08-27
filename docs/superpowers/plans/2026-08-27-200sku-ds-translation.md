# 200 SKU Amazon.es Collection and DS Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect 150 Hogar y cocina and 50 Bricolaje y herramientas ASINs, preserve detail evidence, translate approved Chinese display fields through DS API, and export a QA-gated bilingual workbook.

**Architecture:** Keep collection, normalization, translation and export as separate stages. Collection writes raw ranking/detail evidence and a quota-selected ASIN manifest; a cache-backed DS client consumes normalized Spanish records after collection and emits only Chinese derived fields. Existing Playwright access gates, ASIN identity rules, QA and Excel contract remain authoritative.

**Tech Stack:** Python 3.12, Playwright, BeautifulSoup/lxml, openpyxl, pytest, DeepSeek-compatible HTTP JSON API via the standard library.

---

## File map

- Create: `src/amazon_es_bestseller/translation/ds.py` — DS API client, prompt/schema validation, bounded retry and per-ASIN cache.
- Create: `src/amazon_es_bestseller/collection/quota.py` — category-group tagging, ASIN deduplication and 150/50 quota selection.
- Modify: `src/amazon_es_bestseller/cli.py` — `select-quota` and `translate-ds` offline/online commands.
- Modify: `src/amazon_es_bestseller/pipeline.py` — merge DS translations into Chinese derived fields without changing raw Spanish fields.
- Modify: `src/amazon_es_bestseller/export/excel.py` — pass translated detail/spec/category fields already present in canonical records.
- Create: `tests/test_ds_translation.py` — client contract, cache, retry, malformed response and failure isolation tests.
- Create: `tests/test_quota.py` — group tagging, deduplication and exact quota tests.
- Modify: `tests/test_cli.py`, `tests/test_pipeline.py`, `tests/test_excel_export.py` — command wiring, translation mapping and bilingual output regression tests.
- Create during execution: `configs/amazon_es_200sku_categories.json` — reviewed ranking URLs with `group`, `category_name`, `url`, and `quota`.
- Create during execution: `outputs/amazon_es_200sku_<timestamp>/` — raw runs, manifests, normalized products, translation cache, QA, audit and workbook.

## Task 1: Add quota selection as a pure offline component

**Files:** Create `src/amazon_es_bestseller/collection/quota.py`; create `tests/test_quota.py`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_select_quota_deduplicates_by_asin_within_group():
    records = [
        {"asin": "b1", "ranking_source_url": "u1", "category_l1": "Hogar y cocina"},
        {"asin": "B1", "ranking_source_url": "u2", "category_l1": "Hogar y cocina"},
        {"asin": "D1", "ranking_source_url": "u3", "category_l1": "Bricolaje y herramientas"},
    ]
    selected = select_quota(records, {"hogar": 1, "diy": 1})
    assert selected["hogar"][0]["asin"] == "B1"
    assert selected["diy"][0]["asin"] == "D1"


def test_select_quota_fails_when_group_is_short():
    with pytest.raises(QuotaError, match="diy.*需要 2.*只有 1"):
        select_quota([{"asin": "D1", "category_group": "diy"}], {"diy": 2})
```

- [ ] **Step 2: Run the tests and verify they fail because the module is absent.**

Run: `python -m pytest tests/test_quota.py -q`  
Expected: collection error for missing `amazon_es_bestseller.collection.quota`.

- [ ] **Step 3: Implement the minimal pure functions.**

```python
class QuotaError(ValueError):
    pass


def normalize_group(value):
    value = str(value or "").strip().casefold()
    if value in {"hogar", "hogar y cocina", "kitchen"}:
        return "hogar"
    if value in {"diy", "bricolaje", "bricolaje y herramientas", "tools"}:
        return "diy"
    return value


def select_quota(records, quotas):
    selected = {normalize_group(k): [] for k in quotas}
    seen = {k: set() for k in selected}
    for record in records:
        group = normalize_group(record.get("category_group") or record.get("category_l1"))
        asin = str(record.get("asin") or "").strip().upper()
        if group not in selected or not asin or asin in seen[group]:
            continue
        seen[group].add(asin)
        selected[group].append(dict(record, asin=asin))
        if len(selected[group]) >= int(quotas[group]):
            continue
    for group, quota in quotas.items():
        group = normalize_group(group)
        if len(selected[group]) < int(quota):
            raise QuotaError("%s组需要 %d，只有 %d" % (group, int(quota), len(selected[group])))
    return selected
```

- [ ] **Step 4: Run the focused tests and commit.**

Run: `python -m pytest tests/test_quota.py -q`  
Expected: all quota tests pass.

Commit: `git add src/amazon_es_bestseller/collection/quota.py tests/test_quota.py && git commit -m "feat: add category quota selection"`

## Task 2: Build the DS translation client with cache and safety boundaries

**Files:** Create `src/amazon_es_bestseller/translation/ds.py`; create `tests/test_ds_translation.py`.

- [ ] **Step 1: Write failing tests for request shape, cache hit, retry and malformed response.**

```python
def test_translate_record_returns_only_allowed_chinese_fields(fake_transport):
    client = DeepSeekTranslator(api_key="secret", transport=fake_transport)
    out = client.translate_record({"asin": "B000000001", "title_es_raw": "Taladro"})
    assert out == {"asin": "B000000001", "title_zh": "电钻"}
    assert "api_key" not in fake_transport.last_payload


def test_translate_record_uses_cache_without_second_request(tmp_path, fake_transport):
    client = DeepSeekTranslator(api_key="secret", cache_path=tmp_path / "translations.json",
                                 transport=fake_transport)
    first = client.translate_record({"asin": "B000000001", "title_es_raw": "Taladro"})
    second = client.translate_record({"asin": "B000000001", "title_es_raw": "Taladro"})
    assert first == second
    assert fake_transport.calls == 1


def test_translate_record_retries_then_saves_failure(fake_transport):
    fake_transport.failures_before_success = 2
    client = DeepSeekTranslator(api_key="secret", max_retries=2, transport=fake_transport)
    result = client.translate_record({"asin": "B000000001", "title_es_raw": "Taladro"})
    assert result["translation_status"] == "failed"
    assert fake_transport.calls == 3
```

- [ ] **Step 2: Run the focused tests and verify the expected missing-module failure.**

Run: `python -m pytest tests/test_ds_translation.py -q`  
Expected: collection error for missing `DeepSeekTranslator`.

- [ ] **Step 3: Implement `DeepSeekTranslator` with explicit interfaces.**

The client will expose `translate_record(record)`, `translate_records(records)`, and `save_cache()`. It will read `DEEPSEEK_API_KEY` only when no key is passed, send one ASIN per request to a configurable endpoint/model, validate that the response is a JSON object, retain only `title_zh`, `category_l1_zh`, `category_l2_zh`, `category_l3_zh`, `leaf_category_zh`, `selected_variation_zh`, `specification_zh`, `product_details_zh`, and `feature_bullets_zh`, and preserve the ASIN. It will use bounded exponential backoff for transport, HTTP 429/5xx and JSON failures. A failed ASIN produces a failure record and does not stop later ASINs.

- [ ] **Step 4: Run the client tests and commit.**

Run: `python -m pytest tests/test_ds_translation.py -q`  
Expected: all DS client tests pass without network access.

Commit: `git add src/amazon_es_bestseller/translation/ds.py tests/test_ds_translation.py && git commit -m "feat: add cache-backed DS translation client"`

## Task 3: Wire translations into the canonical pipeline and CLI

**Files:** Modify `src/amazon_es_bestseller/pipeline.py`, `src/amazon_es_bestseller/cli.py`; modify `tests/test_pipeline.py`, `tests/test_cli.py`.

- [ ] **Step 1: Add failing regression tests.**

```python
def test_enrich_applies_ds_translation_without_changing_spanish_source():
    translations = {"B078C6QR1C": {"title_zh": "床垫保护垫", "specification_zh": "90×190厘米"}}
    product = enrich_products(RANKING, DETAIL, translations=translations)[0]
    assert product["title_es_raw"] == "Fiambrera de cristal con 4 piezas"
    assert product["title_zh"] == "床垫保护垫"
    assert product["specification_zh"] == "90×190厘米"
```

- [ ] **Step 2: Run the regression test and verify the new translated field is absent.**

Run: `python -m pytest tests/test_pipeline.py::test_enrich_applies_ds_translation_without_changing_spanish_source -q`  
Expected: FAIL because the canonical record does not yet copy the DS fields.

- [ ] **Step 3: Implement translation merge and CLI command.**

`normalize_product` will copy only approved `*_zh` keys from the ASIN translation map, while existing deterministic dictionary renderers remain the fallback when a DS field is absent. The canonical record will retain `spec_v2` as the normalized evidence summary and add `specification_zh` as the optional DS display override; the exporter will use `specification_zh` first and `spec_v2` as fallback. Category, variation, detail and bullet translations follow the same raw-preserving overlay pattern. `translate-ds` will load `products.json`, require the environment key, process records in ASIN order, save a cache JSON and report success/failure counts. `select-quota` will load ranking JSON plus a category config, annotate each record by exact normalized source URL (or an explicit `category_group` already present), and write a manifest with exact 150/50 counts or a non-zero error.

- [ ] **Step 4: Run pipeline and CLI tests and commit.**

Run: `python -m pytest tests/test_pipeline.py tests/test_cli.py -q`  
Expected: all selected tests pass.

Commit: `git add src/amazon_es_bestseller/pipeline.py src/amazon_es_bestseller/cli.py tests/test_pipeline.py tests/test_cli.py && git commit -m "feat: wire DS translations into pipeline and CLI"`

## Task 4: Prepare and validate the 150/50 category plan

**Files:** Create `configs/amazon_es_200sku_categories.json`; add `tests/fixtures/ranking_category_plan.json`.

- [ ] **Step 1: Build the config from current validated Amazon.es category URLs.**

Each row must contain a real URL, `category_group` (`hogar` or `diy`), Spanish category name, and a positive page quota. The home group must cover storage, cleaning, bedding, kitchen tools and bathroom; the DIY group must cover hand tools, power tools, hardware, safety and workshop organization. No URL may be invented from a title-only label.

- [ ] **Step 2: Run the offline selector against saved ranking records.**

Run: `amazon-es select-quota --rankings <rankings.json> --config configs/amazon_es_200sku_categories.json --out <manifest.json>`  
Expected: a manifest with `hogar=150`, `diy=50`, unique ASINs, source URL and category group on every selected item. If saved data is short, stop and collect more ranking pages before selecting details.

- [ ] **Step 3: Commit only the reviewed configuration and fixture.**

Commit: `git add configs/amazon_es_200sku_categories.json tests/fixtures/ranking_category_plan.json && git commit -m "chore: define 200 sku category quota plan"`

## Task 5: Execute low-frequency Playwright collection

**Files:** Runtime outputs under `outputs/amazon_es_200sku_<timestamp>/`; no raw HTML committed.

- [ ] **Step 1: Collect configured ranking pages serially.**

Run the existing `amazon-es collect --urls ... --out-dir <run_dir>` with the configured category URLs and the existing access gates. Save ranking HTML and JSON before selecting the manifest. Stop on any non-NORMAL access state.

- [ ] **Step 2: Apply the quota manifest and collect selected detail pages serially.**

Use the existing state/resume mechanism; do not issue a second request for a NORMAL cached HTML page. Verify every returned detail page ASIN against the requested ASIN before accepting it.

- [ ] **Step 3: Verify collection counts before translation.**

Run: `python -c "import json; ..."` using the manifest and details JSON to assert 150 Hogar ASINs, 50 DIY ASINs, 200 total unique ASINs, and 200 accepted detail records. If any assertion fails, do not call DS API.

## Task 6: Enrich, translate and run QA

**Files:** Runtime outputs in the 200 SKU directory.

- [ ] **Step 1: Run offline enrich on ranking/detail JSON.**

Run: `amazon-es enrich --rankings <rankings.json> --details <details.json> --out <products.json>`  
Expected: one canonical product record per selected ASIN with Spanish/raw evidence intact.

- [ ] **Step 2: Run DS translation from the environment key.**

Run: `$env:DEEPSEEK_API_KEY=<configured-secret>; amazon-es translate-ds --products <products.json> --cache <translations.json> --out <translations.json>`  
Expected: cache entries keyed by ASIN, no secret in output/logs, and failed records explicitly listed.

- [ ] **Step 3: Re-run enrich with translations and execute QA/audit.**

Run: `amazon-es enrich --rankings <rankings.json> --details <details.json> --translations <translations.json> --out <products_translated.json>`; then `amazon-es qa --products <products_translated.json> --out <qa.json>` and `amazon-es audit-fields --products <products_translated.json> --details <details.json> --rankings <rankings.json> --html-dir <html_dir> --run-dir <run_dir> --out <field_closure.json>`.

Expected: 0 P0/P1; any P2/source-missing field remains explicitly reported.

## Task 7: Export, inspect and final verification

**Files:** `outputs/amazon_es_200sku_<timestamp>/AmazonES_200SKU_Hogar150_DIY50.xlsx`.

- [ ] **Step 1: Export the frozen three-sheet workbook.**

Run: `amazon-es export --products <products_translated.json> --translations <translations.json> --category-planning <planning.json> --images-dir <images_dir> --out <workbook.xlsx>`.

- [ ] **Step 2: Verify workbook content programmatically.**

Assert sheet order `类目规划`, `西班牙语选品清单`, `中文选品清单`; 200 rows in both product sheets; identical ASIN order; 150/50 group counts; Chinese fields populated where DS returned success; Spanish title/detail fields unchanged; image URLs and product links remain aligned.

- [ ] **Step 3: Run the complete test suite and inspect Git state.**

Run: `python -m pytest -q`; then `git diff --check` and `git status --short`. Expected: all tests pass, only intentional commits present, no API key or runtime raw output staged.

- [ ] **Step 4: Commit implementation and push the branch.**

Commit: `git add src tests docs configs && git commit -m "feat: collect 200 amazon es skus with DS translation"`  
Push: `git push origin codex/field-closure-audit`

## Self-review checklist

- The design’s 150/50 quota, raw evidence preservation, low-frequency access gate, DS cache/retry behavior, translation field scope, QA gate and Excel alignment each have an explicit task above.
- No step permits API keys in source or generated artifacts.
- No step uses Detail BSR to populate bestseller rank.
- No step infers missing values; failures remain visible and export remains QA-gated.
- Every production change is preceded by an offline failing test and followed by focused plus full-suite verification.
