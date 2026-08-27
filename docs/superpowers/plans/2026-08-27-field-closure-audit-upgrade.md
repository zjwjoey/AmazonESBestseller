# Field Closure Audit Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the offline field-closure audit classify source evidence accurately and verify the final Excel display layer.

**Architecture:** Add an ASIN-indexed saved-HTML evidence layer to `qa.field_closure`, keep the audit read-only, and add optional workbook reconciliation using the existing exporter’s row-value functions. Separate optional-field coverage from real defects so a source’s normal absence is not counted as a P2 failure.

**Tech Stack:** Python 3, pytest, BeautifulSoup, openpyxl.

---

### Task 1: Index arbitrary saved HTML by page ASIN

**Files:**
- Modify: `tests/test_field_closure.py`
- Modify: `src/amazon_es_bestseller/qa/field_closure.py`

- [ ] **Step 1: Write the failing test**

```python
def test_page_named_html_is_indexed_by_embedded_asin(tmp_path):
    p = _base_product(brand_raw="", brand="")
    (tmp_path / "page_01.html").write_text(
        '<input id="ASIN" value="B000000001"><div id="bylineInfo">Marca: DeLonghi</div>',
        encoding="utf-8")
    issue = _issue(audit_field_closure([p], html_dir=tmp_path), p["asin"], "brand")
    assert issue["classification"] == "PARSER_MISSED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_field_closure.py::test_page_named_html_is_indexed_by_embedded_asin -v`

Expected: FAIL because the existing filename-only lookup does not discover `page_01.html`.

- [ ] **Step 3: Write minimal implementation**

```python
def _html_by_asin(html_dir: Optional[str | Path]) -> dict[str, str]:
    indexed = {}
    for path in sorted(Path(html_dir).rglob("*.html")):
        html = path.read_text(encoding="utf-8", errors="ignore")
        asin = _extract_html_asin(html)
        if asin and asin not in indexed:
            indexed[asin] = html
    return indexed
```

Use the index inside `audit_field_closure` rather than calling `_find_html` once per record.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_field_closure.py::test_page_named_html_is_indexed_by_embedded_asin -v`

Expected: PASS.

### Task 2: Separate unavailable evidence and normal optional absences

**Files:**
- Modify: `tests/test_field_closure.py`
- Modify: `src/amazon_es_bestseller/qa/field_closure.py`

- [ ] **Step 1: Write failing tests**

```python
def test_optional_original_price_absent_on_available_page_is_not_observed(tmp_path):
    p = _base_product(original_price_raw="", original_price=None)
    (tmp_path / "B000000001.html").write_text(
        '<input id="ASIN" value="B000000001"><span id="productTitle">Caja</span>',
        encoding="utf-8")
    issue = _issue(audit_field_closure([p], html_dir=tmp_path), p["asin"], "original_price")
    assert issue["classification"] == "NOT_OBSERVED"
    assert issue["severity"] == "INFO"

def test_missing_html_is_evidence_unavailable_not_source_missing():
    issue = _issue(audit_field_closure([_base_product()]), "B000000001", "original_price")
    assert issue["classification"] == "EVIDENCE_UNAVAILABLE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_field_closure.py -k "optional_original or missing_html" -v`

Expected: FAIL because the current audit reports `SOURCE_MISSING`.

- [ ] **Step 3: Write minimal implementation**

```python
NOT_OBSERVED = "NOT_OBSERVED"
EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
_CONDITIONAL_FIELDS = frozenset({"parent_asin", "original_price", "discount_rate",
                                 "monthly_bought_min", "selected_variation_raw", "seller"})
```

Pass an explicit `page_available` flag into classification. Keep required-field source absence distinct, but do not count informational coverage states as defects.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_field_closure.py -k "optional_original or missing_html" -v`

Expected: PASS.

### Task 3: Add page-label evidence and Excel reconciliation

**Files:**
- Modify: `tests/test_field_closure.py`
- Modify: `src/amazon_es_bestseller/qa/field_closure.py`
- Modify: `src/amazon_es_bestseller/cli.py`

- [ ] **Step 1: Write failing tests**

```python
def test_amazon_since_label_is_date_source_evidence(tmp_path):
    p = _base_product(date_first_available_raw="", date_first_available=None)
    (tmp_path / "page_01.html").write_text(
        '<input id="ASIN" value="B000000001">Producto en Amazon.es desde: 6 noviembre 2023',
        encoding="utf-8")
    assert _issue(audit_field_closure([p], html_dir=tmp_path), p["asin"], "date_first_available")["classification"] == "PARSER_MISSED"

def test_workbook_value_drift_is_export_value_mismatch(tmp_path):
    from openpyxl import load_workbook
    from amazon_es_bestseller.export.excel import export_workbook
    product = _base_product()
    book = tmp_path / "out.xlsx"
    export_workbook([product], out_path=book)
    wb = load_workbook(book)
    wb["西班牙语选品清单"].cell(2, 6).value = 99.0
    wb.save(book)
    report = audit_field_closure([product], workbook_path=book)
    assert any(r["classification"] == "EXPORT_VALUE_MISMATCH" for r in report["records"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_field_closure.py -k "amazon_since or workbook_value_drift" -v`

Expected: FAIL because neither the date label nor the workbook path is supported.

- [ ] **Step 3: Write minimal implementation**

```python
def _audit_workbook(records, workbook_path, translations=None) -> list[dict]:
    wb = openpyxl.load_workbook(workbook_path, data_only=False)
    records = sorted(records, key=lambda r: normalize_asin(r.get("asin")))
    # Compare expected _es_values/_zh_values by ASIN, then inspect ws._images anchors.
    return findings
```

Extend `audit_field_closure(..., workbook_path=None, translations=None)` and CLI `audit-fields` with `--workbook` and `--translations`. Append workbook-only findings to `records` and include their classifications in `defect_summary`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_field_closure.py -k "amazon_since or workbook_value_drift" -v`

Expected: PASS.

### Task 4: Verify full audit and real cached output

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/amazon_es_bestseller/qa/field_closure.py`

- [ ] **Step 1: Write a failing CLI forwarding test**

```python
def test_audit_fields_forwards_workbook_and_translations(tmp_path, monkeypatch):
    products = tmp_path / "products.json"
    translations = tmp_path / "translations.json"
    workbook = tmp_path / "out.xlsx"
    out = tmp_path / "audit.json"
    products.write_text(json.dumps([_base_product()]), encoding="utf-8")
    translations.write_text(json.dumps({}), encoding="utf-8")
    export_workbook([_base_product()], out_path=workbook)
    assert main(["audit-fields", "--products", str(products), "--workbook", str(workbook),
                 "--translations", str(translations), "--out", str(out)]) == 0
    assert out.exists()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_cli.py::test_audit_fields_forwards_workbook_and_translations -v`

Expected: FAIL because the CLI has no such arguments.

- [ ] **Step 3: Implement and run focused tests**

Run: `pytest tests/test_field_closure.py tests/test_cli.py -v`

Expected: PASS.

- [ ] **Step 4: Run the complete suite and real offline audit**

Run:

```bash
pytest -q
python -m amazon_es_bestseller.cli audit-fields \
  --products outputs/amazon_es_200sku_20260827_cached/products_translated.json \
  --details outputs/amazon_es_200sku_20260827_cached/details.json \
  --rankings outputs/amazon_es_200sku_20260827_cached/rankings.json \
  --html-dir E:/amazon_es/.worktrees/reconnaissance/AmazonESBestseller/runs/20260826_084809/html \
  --workbook outputs/amazon_es_200sku_20260827_cached/AmazonES_200SKU_Hogar150_DIY50_cached.xlsx \
  --translations outputs/amazon_es_200sku_20260827_cached/translations_reused_previous_ds.json \
  --out outputs/amazon_es_200sku_20260827_cached/field_closure_v2.json
```

Expected: all tests pass; report is generated without modifying source JSON or workbook.
