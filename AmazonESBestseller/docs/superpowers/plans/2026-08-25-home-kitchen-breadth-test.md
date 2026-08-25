# Home and Kitchen Breadth-Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce one bounded Amazon.es `Hogar y cocina` field-validation run covering 5–10 diverse leaf-category ranking pages and 5–10 distributed detail samples.

**Architecture:** Preserve the existing serial Playwright probe and access stop gate. Add offline parsers that transform saved ranking/detail HTML into canonical records, then use a bounded category traversal to choose diverse deepest-observed nodes without requesting pagination. Keep ranking appearances separate from ASIN-keyed product profiles and report required/candidate field availability by source.

**Tech Stack:** Python 3.12, Playwright sync API, BeautifulSoup, PyYAML, pytest.

---

## File map

- `config/settings.yaml`, `src/amazon_es_bestseller/config.py`: breadth-test caps: 5–10 leaf rankings, 5–10 distributed detail samples, minimum 3-second delay.
- `src/amazon_es_bestseller/models.py`: canonical ranking and product output fields.
- `src/amazon_es_bestseller/product_card_parser.py`: canonical URLs, numeric price/discount, monthly lower bounds, per-leaf index.
- `src/amazon_es_bestseller/detail_parser.py`: new offline parser for product facts, readable details, specification, availability date, and identifiers.
- `src/amazon_es_bestseller/category_discovery.py`, `cli.py`: bounded category traversal, diverse leaf selection keyed by root level-2 branch, distributed detail selection, and run orchestration.
- `src/amazon_es_bestseller/reports.py`: ranking/product CSV schemas, required/candidate field availability, and breadth-test report values.
- `tests/`: offline fixtures for parsing, category selection, output schemas, and stop conditions.

### Task 1: Model canonical ranking fields and visible-price rules

**Files:**
- Modify: `src/amazon_es_bestseller/models.py`
- Modify: `src/amazon_es_bestseller/product_card_parser.py`
- Test: `tests/test_product_card_parser.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Write failing parser tests**

```python
def test_parser_emits_canonical_url_numeric_prices_discount_and_monthly_lower_bound():
    record = parse_product_cards(card_html, RANKING_URL, {"leaf_category": "Baño"})[0]
    assert record.product_url == "https://www.amazon.es/dp/B012345678"
    assert record.current_price == 14.99
    assert record.original_price == 19.99
    assert record.discount_rate == 25.01
    assert record.monthly_bought_min == 1000
    assert record.index == 1
    assert record.category_rank == 1
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python -m pytest tests/test_product_card_parser.py -v`

Expected: current records have text prices, no computed discount, no canonical URL, and no `index` field.

- [ ] **Step 3: Implement minimal canonical parsing**

```python
def canonical_product_url(asin: str | None, fallback_url: str) -> str:
    return f"https://www.amazon.es/dp/{asin}" if asin else fallback_url

def parse_eur_amount(text: str | None) -> float | None:
    # Parse only explicit Euro amounts; return None for absent or ambiguous text.
```

Extend `RankingRecord` with the approved ranking fields. Parse explicit sale/struck-through prices, calculate the discount only when both positive prices are observed, map Spanish `mil` to a lower bound of 1000, and assign `index` after records are limited per leaf.

- [ ] **Step 4: Run focused tests and verify they pass**

Run: `python -m pytest tests/test_product_card_parser.py tests/test_reports.py -v`

Expected: all parser/report tests pass.

### Task 2: Parse detail pages into product profiles

**Files:**
- Create: `src/amazon_es_bestseller/detail_parser.py`
- Modify: `src/amazon_es_bestseller/models.py`
- Modify: `src/amazon_es_bestseller/product_card_parser.py`
- Test: `tests/test_detail_parser.py`

- [ ] **Step 1: Write failing detail-parser tests**

```python
def test_detail_parser_keeps_structured_and_readable_details():
    detail = parse_detail_page(detail_html, asin="B012345678")
    assert detail.parent_asin == "B099999999"
    assert detail.details_json["brand"] == "Marca"
    assert "Marca: Marca" in detail.details
    assert detail.specification == "90 x 190 cm"
    assert detail.date_first_available == "2024-01-31"
    assert detail.date_first_available_raw == "31 de enero de 2024"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest tests/test_detail_parser.py -v`

Expected: import failure because no detail parser exists.

- [ ] **Step 3: Implement an offline, conservative detail parser**

```python
def parse_detail_page(html: str, asin: str | None) -> ProductDetail:
    # Extract only visible detail bullets/tables and known public identifiers.
    # Keep unknown values as None; do not call an endpoint or infer a parent ASIN.
```

Collect brand, parent ASIN only when explicit, key-value facts, bullet features, a single main sales specification, the first-available date raw/ISO values, and candidate fields. Render `details` from the structured values without translation.

- [ ] **Step 4: Run focused tests and verify they pass**

Run: `python -m pytest tests/test_detail_parser.py -v`

Expected: all detail parser tests pass.

### Task 3: Discover and select breadth-test leaf rankings

**Files:**
- Modify: `src/amazon_es_bestseller/category_discovery.py`
- Modify: `src/amazon_es_bestseller/cli.py`
- Modify: `src/amazon_es_bestseller/config.py`
- Modify: `config/settings.yaml`
- Test: `tests/test_category_discovery.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing selection tests**

```python
def test_select_leaf_trial_nodes_prefers_distinct_top_level_branches():
    selected = select_leaf_trial_nodes(branch_nodes, max_leaf_categories=5)
    assert len(selected) == 5
    assert len({branch for branch, _node in selected}) >= 3

def test_leaf_trial_settings_reject_out_of_bounds_limits():
    with pytest.raises(ValueError):
        load_settings(path_with(max_leaf_categories=11))
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python -m pytest tests/test_category_discovery.py tests/test_cli.py tests/test_settings.py -v`

Expected: no leaf-trial selector or breadth-test setting exists.

- [ ] **Step 3: Implement bounded traversal and selection**

```python
MAX_LEAF_CATEGORIES = 10
MAX_DETAIL_SAMPLES = 10

def select_leaf_trial_nodes(branch_nodes, max_leaf_categories):
    # `branch_nodes` is an ordered mapping of level-2 category name to observed
    # deepest candidate nodes; return (branch_name, node) pairs round-robin.
```

Visit only discovered category pages serially. Treat a node as a test leaf only when its saved page has no confirmed child-navigation nodes; otherwise continue one discovered child path within the cap. Never request a pagination URL. Select detail URLs round-robin by `leaf_category`.

- [ ] **Step 4: Run focused tests and verify they pass**

Run: `python -m pytest tests/test_category_discovery.py tests/test_cli.py tests/test_settings.py -v`

Expected: leaf selection is bounded, diverse, and does not increase access after a non-normal event.

### Task 4: Write final artifacts and availability report

**Files:**
- Modify: `src/amazon_es_bestseller/reports.py`
- Modify: `src/amazon_es_bestseller/cli.py`
- Test: `tests/test_reports.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing artifact tests**

```python
def test_ranking_and_product_csvs_match_approved_contract(tmp_path):
    write_ranking_csv([record], tmp_path / "ranking_records.csv")
    write_products_csv([product], tmp_path / "products.csv")
    assert required_ranking_columns <= csv_columns(tmp_path / "ranking_records.csv")
    assert {"details_json", "details", "specification"} <= csv_columns(tmp_path / "products.csv")

def test_availability_separates_ranking_detail_and_candidate_sources():
    rows = build_field_availability(records, details=[detail])
    assert {row["source"] for row in rows} >= {"ranking_records", "detail_pages", "candidate_fields"}
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python -m pytest tests/test_reports.py tests/test_cli.py -v`

Expected: current CSV schema and availability function lack the approved source separation.

- [ ] **Step 3: Implement exact artifacts**

```python
write_ranking_csv(records, run_dir / "ranking_records.csv")
write_products_csv(products, run_dir / "products.csv")
write_field_availability_csv(ranking_records, details, run_dir / "field_availability.csv")
```

Merge selected parsed detail profiles into ASIN-keyed products without overwriting observed ranking appearances. Report natural page counts, sampled leaf categories, pagination observation, duplicate-ASIN rate, access events, and GO / CONDITIONAL GO / NO-GO.

- [ ] **Step 4: Run focused tests and verify they pass**

Run: `python -m pytest tests/test_reports.py tests/test_cli.py -v`

Expected: all artifacts preserve the approved fields and source-specific availability.

### Task 5: Verify, review, commit, then perform one live breadth test

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-25-home-kitchen-breadth-test.md`

- [ ] **Step 1: Run full offline verification**

Run: `python -m pytest -v; python -m compileall -q src tests; git diff --check`

Expected: every test passes, compilation succeeds, and diff check is empty.

- [ ] **Step 2: Review and commit the bounded test implementation**

Run: `git diff --stat; git status --short; git add src tests config README.md docs; git commit -m "feat: add home kitchen breadth test"`

Expected: only scoped parsing, selection, reporting, configuration, tests, and documentation changes are committed.

- [ ] **Step 3: Run exactly once and inspect artifacts**

Run: `python -m amazon_es_bestseller.cli run --config config/settings.yaml`

Expected: one serial run; any non-normal access state ends navigation with preserved evidence. Do not retry or perform a second live run.

- [ ] **Step 4: Report and stop**

Read the newest run's `access_events.csv`, `ranking_records.csv`, `products.csv`, `field_availability.csv`, category tree, and report. State observed results and GO / CONDITIONAL GO / NO-GO. Do not begin DIY/tools or a larger collection.
