# Amazon.es Reconnaissance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a low-frequency, serial Playwright reconnaissance tool that determines whether Amazon.es Best Sellers data for `Hogar y cocina` can be collected reliably without attempting to circumvent access controls.

**Architecture:** Only `browser_probe.py` may open Amazon pages. It writes immutable run artifacts and stops navigation immediately when `access_detector.py` finds a block, rate limit, challenge, login requirement, or denial page. All discovery, parsing, aggregation, and reporting then run offline from saved HTML so selectors can be tested without new web traffic.

**Tech Stack:** Python 3.12+, Playwright, BeautifulSoup4, lxml, PyYAML, pandas, pytest.

---

## Planned file structure

```text
AmazonESBestseller/
  pyproject.toml
  README.md
  config/settings.yaml
  src/amazon_es_bestseller/
    __init__.py
    models.py
    access_detector.py
    run_store.py
    browser_probe.py
    page_inspector.py
    category_discovery.py
    product_card_parser.py
    reports.py
    cli.py
  tests/
    fixtures/
    test_access_detector.py
    test_run_store.py
    test_page_inspector.py
    test_category_discovery.py
    test_product_card_parser.py
    test_reports.py
```

`runs/` and Playwright browser downloads are local generated artifacts and must be ignored by Git. Production access is deliberately excluded from unit tests.

### Task 1: Create the isolated Python project and configuration

**Files:**

- Create: `AmazonESBestseller/pyproject.toml`
- Create: `AmazonESBestseller/config/settings.yaml`
- Create: `AmazonESBestseller/.gitignore`
- Create: `AmazonESBestseller/src/amazon_es_bestseller/__init__.py`
- Create: `AmazonESBestseller/tests/test_settings.py`

- [ ] **Step 1: Write the failing configuration test**

```python
from pathlib import Path
from amazon_es_bestseller.config import load_settings

def test_load_settings_enforces_reconnaissance_hard_limits():
    settings = load_settings(Path('config/settings.yaml'))
    assert settings.max_categories == 3
    assert settings.max_products_per_category == 50
    assert settings.max_detail_samples == 5
    assert settings.page_delay_seconds >= 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_settings.py -v`

Expected: FAIL because the package and `load_settings` do not exist.

- [ ] **Step 3: Add minimal packaging and settings loader**

Use a `pyproject.toml` with dependencies `playwright`, `beautifulsoup4`, `lxml`, `pandas`, `pyyaml`, and optional test dependency `pytest`. Add `src/amazon_es_bestseller/config.py` with a frozen `Settings` dataclass and a `load_settings(path: Path) -> Settings` function. Store these exact defaults in YAML:

```yaml
root_urls:
  home: https://www.amazon.es/
  bestsellers: https://www.amazon.es/gp/bestsellers
  kitchen: https://www.amazon.es/gp/bestsellers/kitchen
page_delay_seconds: 3
max_categories: 3
max_products_per_category: 50
max_detail_samples: 5
headless: false
```

Add `.gitignore` entries for `.venv/`, `__pycache__/`, `.pytest_cache/`, `runs/`, and `playwright/.cache/`.

- [ ] **Step 4: Run the focused test**

Run: `python -m pytest tests/test_settings.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the project foundation**

```powershell
git add AmazonESBestseller/pyproject.toml AmazonESBestseller/config AmazonESBestseller/.gitignore AmazonESBestseller/src AmazonESBestseller/tests/test_settings.py
git commit -m "build: initialize reconnaissance project"
```

### Task 2: Model access states and detect stop conditions

**Files:**

- Create: `AmazonESBestseller/src/amazon_es_bestseller/models.py`
- Create: `AmazonESBestseller/src/amazon_es_bestseller/access_detector.py`
- Create: `AmazonESBestseller/tests/test_access_detector.py`

- [ ] **Step 1: Write failing access-state tests**

```python
from amazon_es_bestseller.access_detector import detect_access_state
from amazon_es_bestseller.models import AccessState

def test_detects_robot_check_from_title_and_body():
    result = detect_access_state('Robot Check', 'To discuss automated access...')
    assert result.state is AccessState.CHALLENGE

def test_detects_rate_limit_from_http_status():
    result = detect_access_state('Amazon.es', '', http_status=429)
    assert result.state is AccessState.RATE_LIMITED

def test_normal_page_has_no_stop_reason():
    result = detect_access_state('Amazon.es: compra online', '<main>content</main>', 200)
    assert result.state is AccessState.NORMAL
    assert result.reason is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_access_detector.py -v`

Expected: FAIL because access detection is absent.

- [ ] **Step 3: Implement explicit access models and detector**

Define `AccessState` as a string enum with exactly `NORMAL`, `BLOCKED`, `RATE_LIMITED`, `CHALLENGE`, `NETWORK_ERROR`, and `UNKNOWN`. Define immutable `AccessResult(state, reason)`. `detect_access_state(title, body, http_status=None)` must prioritize HTTP 429, then 403, then case-insensitive body/title markers for `robot check`, `captcha`, `type the characters`, `resolver el captcha`, `access denied`, and login/sign-in requirements. It must not retry, mutate browser state, or attempt a challenge.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_access_detector.py -v`

Expected: PASS.

- [ ] **Step 5: Commit access detection**

```powershell
git add AmazonESBestseller/src/amazon_es_bestseller/models.py AmazonESBestseller/src/amazon_es_bestseller/access_detector.py AmazonESBestseller/tests/test_access_detector.py
git commit -m "feat: detect Amazon access states"
```

### Task 3: Create immutable run storage and serial browser probe

**Files:**

- Create: `AmazonESBestseller/src/amazon_es_bestseller/run_store.py`
- Create: `AmazonESBestseller/src/amazon_es_bestseller/browser_probe.py`
- Create: `AmazonESBestseller/tests/test_run_store.py`
- Create: `AmazonESBestseller/tests/test_browser_probe.py`

- [ ] **Step 1: Write failing run-storage and stop-on-block tests**

```python
from pathlib import Path
from amazon_es_bestseller.models import AccessState
from amazon_es_bestseller.run_store import RunStore

def test_run_store_creates_required_artifact_folders(tmp_path: Path):
    store = RunStore.create(tmp_path, '20260824_120000')
    for name in ('html', 'screenshots', 'raw', 'failures', 'parsed', 'logs'):
        assert (store.root / name).is_dir()

def test_probe_does_not_navigate_after_challenge(fake_page, store):
    fake_page.next_result = ('Robot Check', 'captcha', 200)
    events = run_probe(fake_page, store, ['https://www.amazon.es/', 'https://example.invalid/'])
    assert len(events) == 1
    assert events[0].access_state is AccessState.CHALLENGE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_run_store.py tests/test_browser_probe.py -v`

Expected: FAIL because the store and probe do not exist.

- [ ] **Step 3: Implement storage and probe with a page adapter**

`RunStore.create(base_dir, run_id)` must create a unique `runs/<run_id>/` directory and its six subdirectories, never overwrite an existing run, append `logs/run.log`, and write `access_events.csv`. Add methods `save_html(page_name, html)`, `save_screenshot(page_name, page)`, and `save_failure(page_name, html, page)`.

Implement `probe_urls(page, store, targets, delay_seconds)` around a narrow page protocol (`goto`, `title`, `content`, `url`, `screenshot`). For each target, record requested URL, final URL, page title, timestamp, load duration, navigation status, body length, access state, and reason. Save HTML and screenshot before determining whether to continue. If state is not `NORMAL`, save the same page under `failures/`, write the event, and return without navigating the remaining targets. Playwright must run with one page, one context, and no concurrency; `time.sleep(delay_seconds)` occurs only between successful pages.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_run_store.py tests/test_browser_probe.py -v`

Expected: PASS without opening a network connection.

- [ ] **Step 5: Commit probe infrastructure**

```powershell
git add AmazonESBestseller/src/amazon_es_bestseller/run_store.py AmazonESBestseller/src/amazon_es_bestseller/browser_probe.py AmazonESBestseller/tests/test_run_store.py AmazonESBestseller/tests/test_browser_probe.py
git commit -m "feat: add serial browser probe and run artifacts"
```

### Task 4: Inspect saved HTML and discover categories offline

**Files:**

- Create: `AmazonESBestseller/src/amazon_es_bestseller/page_inspector.py`
- Create: `AmazonESBestseller/src/amazon_es_bestseller/category_discovery.py`
- Create: `AmazonESBestseller/tests/fixtures/kitchen_sample.html`
- Create: `AmazonESBestseller/tests/test_page_inspector.py`
- Create: `AmazonESBestseller/tests/test_category_discovery.py`

- [ ] **Step 1: Write failing offline fixture tests**

```python
from amazon_es_bestseller.category_discovery import discover_categories
from amazon_es_bestseller.page_inspector import inspect_html

def test_inspector_counts_repeated_product_card_candidates(kitchen_html):
    result = inspect_html(kitchen_html)
    assert result.product_card_candidate_count == 2
    assert 'json_ld' in result.structured_data_kinds

def test_discovery_preserves_real_category_url_and_node_id(kitchen_html):
    nodes = discover_categories(kitchen_html, 'https://www.amazon.es/gp/bestsellers/kitchen')
    assert nodes[0].category_name_es == 'Baño'
    assert nodes[0].browse_node_id == '12345'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_page_inspector.py tests/test_category_discovery.py -v`

Expected: FAIL because offline inspection is absent.

- [ ] **Step 3: Implement structural inspection and category discovery**

Use BeautifulSoup and only the supplied HTML. `inspect_html` must count repeated elements that contain a product link matching `/dp/[A-Z0-9]{10}`, list JSON-LD and JSON script blocks, and retain selector/attribute evidence for the report. `discover_categories` must collect only visible category anchors from the saved kitchen page, resolve relative URLs with `urljoin`, extract `browse_node_id` from `node=` or `zgbs=` query values when present, otherwise set it to `None`, and never invent a hierarchy. Emit nodes with Spanish name, source page, parent category, and depth.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_page_inspector.py tests/test_category_discovery.py -v`

Expected: PASS.

- [ ] **Step 5: Commit offline discovery**

```powershell
git add AmazonESBestseller/src/amazon_es_bestseller/page_inspector.py AmazonESBestseller/src/amazon_es_bestseller/category_discovery.py AmazonESBestseller/tests
git commit -m "feat: inspect saved pages and discover categories"
```

### Task 5: Parse ranking records and build the unique-product table

**Files:**

- Create: `AmazonESBestseller/src/amazon_es_bestseller/product_card_parser.py`
- Create: `AmazonESBestseller/src/amazon_es_bestseller/reports.py`
- Create: `AmazonESBestseller/tests/test_product_card_parser.py`
- Create: `AmazonESBestseller/tests/test_reports.py`

- [ ] **Step 1: Write failing parser and aggregation tests**

```python
def test_parser_uses_product_url_as_asin_source(kitchen_html):
    record = parse_product_cards(kitchen_html, source_url='https://example.test')[0]
    assert record.asin == 'B012345678'
    assert record.asin_source == 'product_url'
    assert record.rank == 1
    assert record.rank_source == 'visible_text'

def test_product_aggregation_keeps_multiple_ranking_records():
    products = build_products([record('B012345678', rank=8), record('B012345678', rank=2)])
    assert products[0].ranking_count == 2
    assert products[0].best_rank == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_product_card_parser.py tests/test_reports.py -v`

Expected: FAIL because parsing and aggregation are absent.

- [ ] **Step 3: Implement validated, tolerant parsing and CSV outputs**

Implement `parse_product_cards(html, source_url, category_context)` with one semantic primary strategy and one fallback strategy. Extract ASIN from a `/dp/<10-character ASIN>` product URL first, then a DOM attribute; keep `asin_source`. Set `rank` only when visible rank text explicitly matches `#N` or `n.º N`; set it to `None` otherwise. Preserve null for missing price, rating, reviews, image, monthly-bought, brand, and deal fields. Preserve each appearance as a `RankingRecord`.

Implement `build_products(records)` grouped by non-null ASIN, calculating `first_seen`, `last_seen`, `ranking_count`, and minimum `best_rank`. Write `ranking_records.csv`, `products.csv`, and `field_availability.csv`. The availability report must expose `field`, `records`, `non_null`, `null`, `availability_rate`, and `source`; the run report must flag ASIN availability below 95%.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_product_card_parser.py tests/test_reports.py -v`

Expected: PASS.

- [ ] **Step 5: Commit parsing and reporting primitives**

```powershell
git add AmazonESBestseller/src/amazon_es_bestseller/product_card_parser.py AmazonESBestseller/src/amazon_es_bestseller/reports.py AmazonESBestseller/tests/test_product_card_parser.py AmazonESBestseller/tests/test_reports.py
git commit -m "feat: parse ranking records and field availability"
```

### Task 6: Add the guarded CLI, final report, and operator instructions

**Files:**

- Create: `AmazonESBestseller/src/amazon_es_bestseller/cli.py`
- Create: `AmazonESBestseller/README.md`
- Modify: `AmazonESBestseller/src/amazon_es_bestseller/reports.py`
- Create: `AmazonESBestseller/tests/test_cli.py`

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_cli_stops_before_category_pages_when_root_probe_is_blocked(monkeypatch, tmp_path):
    result = run_reconnaissance(tmp_path, fake_probe(state='BLOCKED'))
    assert result.visited_page_count == 1
    assert result.decision in {'NO-GO', 'CONDITIONAL GO'}

def test_report_contains_required_final_sections(tmp_path):
    report = write_report(tmp_path, normal_summary())
    assert '## 11. ASIN提取成功率' in report.read_text(encoding='utf-8')
    assert '## 30. 是否建议进入正式开发' in report.read_text(encoding='utf-8')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -v`

Expected: FAIL because orchestration and final reporting are absent.

- [ ] **Step 3: Implement the single-entry-point workflow**

Expose `python -m amazon_es_bestseller.cli run --config config/settings.yaml`. The CLI creates one run, invokes the three root probes in fixed order, and proceeds to category parsing only if every root event is `NORMAL`. It selects at most the first three actually discovered second-level category URLs, opens them serially, and never follows a guessed page-two URL. It selects at most five unique, parsed product URLs for detail-field reconnaissance only after all category probes are `NORMAL`.

`write_report` must include all 30 sections requested in the design, actual page counts, access events, category counts, card depth/pagination observations, ranking-record and unique-ASIN counts, duplicate rate, fields observed on detail samples, and a GO/CONDITIONAL GO/NO-GO decision derived from evidence. The README must provide installation (`python -m pip install -e .`, `python -m playwright install chromium`), the single run command, generated artifact locations, and an explicit statement that the tool does not bypass access controls.

- [ ] **Step 4: Run focused and full test suites**

Run first: `python -m pytest tests/test_cli.py -v`

Then run: `python -m pytest -v`

Expected: all tests PASS; no command opens Amazon unless the explicit CLI run command is used.

- [ ] **Step 5: Commit the CLI and documentation**

```powershell
git add AmazonESBestseller/src/amazon_es_bestseller/cli.py AmazonESBestseller/src/amazon_es_bestseller/reports.py AmazonESBestseller/README.md AmazonESBestseller/tests/test_cli.py
git commit -m "feat: add guarded reconnaissance workflow"
```

### Task 7: Verify the implementation and perform one bounded reconnaissance run

**Files:**

- Modify: `AmazonESBestseller/tests/fixtures/` only by copying pages produced by the run
- Generate: `AmazonESBestseller/runs/YYYYMMDD_HHMMSS/`

- [ ] **Step 1: Run the complete offline test suite**

Run: `python -m pytest -v`

Expected: all tests PASS before any live page access.

- [ ] **Step 2: Run exactly one configured reconnaissance session**

Run: `python -m amazon_es_bestseller.cli run --config config/settings.yaml`

Expected: only the configured root pages, then no more than three real discovered categories and no more than five detail samples; execution stops immediately on the first non-NORMAL access result.

- [ ] **Step 3: Verify required artifacts and report assertions**

Run: `Get-ChildItem -Recurse runs\<run-id> | Select-Object FullName` and inspect `report.md`, `access_events.csv`, `field_availability.csv`, `category_tree.csv`, `category_tree.json`, `ranking_records.csv`, and `products.csv`.

Expected: all required files exist when their preceding stage was reached; a blocked run contains failure evidence and a clear final decision rather than fabricated extraction results.

- [ ] **Step 4: Commit only source, tests, fixtures, and documentation**

```powershell
git add AmazonESBestseller/src AmazonESBestseller/tests AmazonESBestseller/README.md AmazonESBestseller/config AmazonESBestseller/.gitignore
git commit -m "test: verify reconnaissance workflow"
git push
```

Do not commit `runs/` artifacts, historical root files, or any data that may be sensitive to redistribution.

## Plan self-review

Coverage mapping: Tasks 1–3 establish the low-frequency guarded browser boundary and evidence storage; Task 4 covers offline DOM, category, and structured-data discovery; Task 5 preserves ranking records, ASIN sources, field availability, and unique-product aggregation; Task 6 produces the bounded orchestration and all report sections; Task 7 validates the run and explicitly stops scope expansion. The plan contains no placeholder markers, uses the same `AccessState` names and hard limits defined in the design, and never instructs bypassing access controls.
