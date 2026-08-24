# Reconnaissance Trial Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the five review blockers so a bounded reconnaissance run stops on unsafe navigation and reports only trustworthy category-specific evidence.

**Architecture:** Keep network behavior inside `browser_probe.py`; add an expected-page identity check before the existing stop gate. Keep data-quality rules in `cli.py`, parsing/tree helpers in their existing modules, and verify behavior through offline fixtures only. Do not add Amazon requests or change configured limits.

**Tech Stack:** Python 3.12, Playwright sync API, BeautifulSoup, pytest.

---

## File map

- `src/amazon_es_bestseller/access_detector.py`: identify Spanish/English login pages.
- `src/amazon_es_bestseller/browser_probe.py`: reject same-host redirects that do not preserve the requested page path.
- `src/amazon_es_bestseller/cli.py`: gate GO on category-specific records; filter detail samples; attach offline depth-3 discovery.
- `src/amazon_es_bestseller/category_discovery.py`: represent direct child links with an explicit parent and depth.
- `src/amazon_es_bestseller/reports.py`: render hierarchical category rows and calculate duplicate metrics from ASIN-bearing records.
- `src/amazon_es_bestseller/product_card_parser.py`: avoid nested candidates and duplicate records.
- `tests/`: add minimal regression tests alongside each module's existing suite.

### Task 1: Stop same-host unexpected navigation

**Files:**
- Modify: `src/amazon_es_bestseller/access_detector.py:14-20`
- Modify: `src/amazon_es_bestseller/browser_probe.py:9-18`
- Test: `tests/test_access_detector.py`
- Test: `tests/test_browser_probe.py`

- [x] **Step 1: Write failing tests**

```python
def test_detects_spanish_sign_in_page():
    result = detect_access_state("Amazon.es", "Inicia sesi\u00f3n para continuar", 200)
    assert result.state is AccessState.BLOCKED

def test_probe_marks_same_host_path_redirect_as_unknown(tmp_path):
    # Fake page returns https://www.amazon.es/ for a kitchen request.
    events = probe_urls(fake_page, store, [KITCHEN_URL])
    assert events[0].access_state is AccessState.UNKNOWN
```

- [x] **Step 2: Run the two tests and verify they fail**

Run: `python -m pytest tests/test_access_detector.py tests/test_browser_probe.py -v`

Expected: the new assertions fail because only host equality is checked and `Inicia sesi\u00f3n` is not a block marker.

- [x] **Step 3: Write minimal implementation**

```python
# access_detector.py
_BLOCK_MARKERS = (..., "inicia sesi\u00f3n")

# browser_probe.py
def _expected_page_identity(requested_url: str, final_url: str | None) -> bool:
    requested = urlparse(requested_url)
    final = urlparse(final_url or "")
    return _same_expected_host(requested_url, final_url) and (
        final.path.rstrip("/") == requested.path.rstrip("/")
    )
```

Use the identity helper where `probe_urls()` currently checks only `_same_expected_host()` and set `UNKNOWN` when it returns false.

- [x] **Step 4: Run the focused tests and verify they pass**

Run: `python -m pytest tests/test_access_detector.py tests/test_browser_probe.py -v`

Expected: all tests pass.

### Task 2: Require category-specific GO evidence

**Files:**
- Modify: `src/amazon_es_bestseller/cli.py:138-165`
- Test: `tests/test_cli.py`

- [x] **Step 1: Write a failing test**

```python
def test_choose_decision_does_not_go_without_records_from_each_category():
    decision = choose_decision(root_events, category_events, [], root_only_records)
    assert decision == "CONDITIONAL GO"
```

`root_events` contains three normal root events, `category_events` contains three normal distinct category URLs, and `root_only_records` has high-ASIN/high-rank records whose `level2_category_es` is `None`.

- [x] **Step 2: Run the test and verify it fails**

Run: `python -m pytest tests/test_cli.py::test_choose_decision_does_not_go_without_records_from_each_category -v`

Expected: fails because the current function returns `GO`.

- [x] **Step 3: Write minimal implementation**

```python
normal_category_urls = {event.requested_url for event in category_events}
record_category_urls = {
    record.source_url for record in records
    if record.asin is not None and record.rank is not None
}
if len(normal_category_urls) < 3 or not normal_category_urls <= record_category_urls:
    return "CONDITIONAL GO"
```

Place this check before calculating field availability.

- [x] **Step 4: Run focused tests and verify they pass**

Run: `python -m pytest tests/test_cli.py -v`

Expected: all CLI tests pass.

### Task 3: Preserve only normal detail evidence and offline depth-3 nodes

**Files:**
- Modify: `src/amazon_es_bestseller/category_discovery.py:30-65`
- Modify: `src/amazon_es_bestseller/cli.py:94-108,239-300`
- Modify: `src/amazon_es_bestseller/reports.py:101-129`
- Test: `tests/test_cli.py`
- Test: `tests/test_category_discovery.py`
- Test: `tests/test_reports.py`

- [x] **Step 1: Write failing tests**

```python
def test_detail_field_report_excludes_blocked_event(tmp_path):
    # A normal detail has productTitle; a blocked HTML page also has productTitle.
    _write_detail_field_report(store, [normal_event, blocked_event], start_index=7)
    assert detail_csv_row("title")["samples"] == "1"

def test_discovery_records_direct_children_at_depth_three():
    nodes = discover_categories(child_html, source_page=LEVEL2_URL,
                                parent_category="Ba\u00f1o", depth=3)
    assert nodes[0].parent_category == "Ba\u00f1o"
    assert nodes[0].depth == 3
```

- [x] **Step 2: Run the tests and verify they fail**

Run: `python -m pytest tests/test_cli.py tests/test_category_discovery.py tests/test_reports.py -v`

Expected: blocked detail HTML is counted and discovery does not accept a parent/depth argument.

- [x] **Step 3: Write minimal implementation**

```python
for index, event in enumerate(detail_events, start=start_index):
    if event.access_state is not AccessState.NORMAL:
        continue
    # inspect saved page only after this guard
```

Extend `discover_categories()` with keyword-only `parent_category` and `depth` parameters. In the normal-category parsing loop, call it on each saved category HTML with `parent_category=node.category_name_es, depth=3`, append only direct children to the full tree, and pass the combined node list to `write_category_tree()`.

Make `write_category_tree()` render a depth-3 node as `level_2=node.parent_category`, `level_3=node.category_name_es`, and nest it below its parent in JSON.

- [x] **Step 4: Run focused tests and verify they pass**

Run: `python -m pytest tests/test_cli.py tests/test_category_discovery.py tests/test_reports.py -v`

Expected: all tests pass.

### Task 4: Make ranking records and duplicate metrics trustworthy

**Files:**
- Modify: `src/amazon_es_bestseller/product_card_parser.py:11-144`
- Modify: `src/amazon_es_bestseller/reports.py:79-87`
- Test: `tests/test_product_card_parser.py`
- Test: `tests/test_reports.py`

- [x] **Step 1: Write failing tests**

```python
def test_parser_deduplicates_nested_card_candidates():
    records = parse_product_cards(nested_card_html, KITCHEN_URL)
    assert len(records) == 1

def test_duplicate_summary_excludes_missing_asin_from_duplicate_count():
    summary = duplicate_summary([RankingRecord(asin="B012345678"), RankingRecord(asin=None)])
    assert summary["duplicate_records"] == 0
    assert summary["duplicate_rate"] == 0.0
```

- [x] **Step 2: Run the tests and verify they fail**

Run: `python -m pytest tests/test_product_card_parser.py tests/test_reports.py -v`

Expected: nested candidates return two records and null ASIN inflates duplicate counts.

- [x] **Step 3: Write minimal implementation**

```python
# product_card_parser.py
nodes = [node for node in nodes if not any(parent in nodes for parent in node.parents)]
seen: set[tuple[str | None, int | None, str]] = set()
# append only if (asin, rank, product_url) has not appeared on this page

# reports.py
asin_records = [record for record in records if record.asin]
duplicate_records = len(asin_records) - unique_asins
duplicate_rate = duplicate_records / len(asin_records) if asin_records else 0.0
```

- [x] **Step 4: Run focused tests and verify they pass**

Run: `python -m pytest tests/test_product_card_parser.py tests/test_reports.py -v`

Expected: all parser and report tests pass.

### Task 5: Verify, review, commit, and push

**Files:**
- Modify: `docs/superpowers/specs/2026-08-24-reconnaissance-trial-blockers-design.md` only if implementation changes the approved design.
- Modify: `docs/superpowers/plans/2026-08-24-reconnaissance-trial-blockers.md` to mark completed steps.

- [x] **Step 1: Run full verification**

Run: `python -m pytest -v; python -m compileall -q src tests; git diff --check`

Expected: every test passes, compilation exits 0, and diff check emits no errors.

- [x] **Step 2: Re-review the exact final diff**

Run: `git diff --check origin/main...HEAD; git status --short; git diff --stat origin/main...HEAD`

Expected: only scoped source, tests, and approved documentation changes are present.

- [ ] **Step 3: Commit and push**

Run: `git add src tests docs; git commit -m "fix: harden reconnaissance trial evidence"; git push origin feature/amazon-es-reconnaissance`

Expected: the feature branch advances on the remote without including `runs/` artifacts.
