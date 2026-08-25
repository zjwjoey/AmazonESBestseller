# Complete Catalog and DIY Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the existing Home/Kitchen data, complete 150 product details and images, then collect 50 equivalent DIY/Tools products.

**Architecture:** Keep parsing, image transfer, and run continuation independent. Saved HTML is reparsed offline first; only absent Home/Kitchen details are requested. Category collection becomes root-category-agnostic so DIY/Tools can reuse the same bounded serial flow.

**Tech Stack:** Python 3.12, BeautifulSoup, Playwright sync API, CSV, pytest.

---

### Task 1: Normalize detail facts and product enrichment

**Files:**
- Modify: `src/amazon_es_bestseller/detail_parser.py`
- Modify: `src/amazon_es_bestseller/models.py`
- Modify: `src/amazon_es_bestseller/product_card_parser.py`
- Test: `tests/test_detail_parser.py`
- Test: `tests/test_product_card_parser.py`

- [ ] Write failing tests for explicit Spanish detail-bullet labels, accent-normalized keys, date extraction, brand enrichment, and parent-ASIN status.
- [ ] Run the focused tests and observe failure.
- [ ] Parse table `th`/`td` and bullet label/value pairs explicitly; map `Producto en Amazon.es desde` to the date fields.
- [ ] Populate `brand`, date fields, and `parent_asin_status` on `ProductSummary`.
- [ ] Run focused tests, then `python -m pytest -q`.

### Task 2: Add serial image downloader and reporting fields

**Files:**
- Create: `src/amazon_es_bestseller/image_downloader.py`
- Modify: `src/amazon_es_bestseller/models.py`
- Modify: `src/amazon_es_bestseller/reports.py`
- Test: `tests/test_image_downloader.py`

- [ ] Write failing tests for stable ASIN image filenames, a successful local image response, one failed response, and no retry behavior.
- [ ] Run the focused tests and observe failure.
- [ ] Implement one-at-a-time image download with a configurable minimum delay, content-type extension, status/error capture, and no retry loop.
- [ ] Include image fields and detail candidate availability in product/field reports.
- [ ] Run focused tests, then `python -m pytest -q`.

### Task 3: Rebuild existing Home/Kitchen artifacts offline

**Files:**
- Modify: `src/amazon_es_bestseller/cli.py`
- Test: `tests/test_cli.py`

- [ ] Write a failing test for rebuilding a run from saved detail HTML without invoking a probe.
- [ ] Implement an offline rebuild command that re-parses known detail pages, updates products/field availability/report, and preserves ranking evidence.
- [ ] Run the focused test and full test suite.

### Task 4: Complete missing Home/Kitchen detail pages and images

**Files:**
- Modify: `src/amazon_es_bestseller/cli.py`
- Test: `tests/test_cli.py`

- [ ] Write a failing test that selects only products without details and stops after a non-normal event.
- [ ] Implement a continuation command that visits missing details once, stores separate continuation evidence, merges parsed fields, and never re-visits already saved detail pages.
- [ ] Run Home/Kitchen continuation with the existing 150-SKU source run; then run its serial image-download stage.
- [ ] Verify 150 product rows have parsed details and image status evidence.

### Task 5: Generalize bounded category collection for DIY/Tools

**Files:**
- Modify: `src/amazon_es_bestseller/category_discovery.py`
- Modify: `src/amazon_es_bestseller/config.py`
- Modify: `src/amazon_es_bestseller/cli.py`
- Modify: `config/settings.yaml`
- Test: `tests/test_category_discovery.py`
- Test: `tests/test_cli.py`

- [ ] Write failing tests for discovery rooted at `/gp/bestsellers/diy` and a 50-unique-SKU cap.
- [ ] Generalize the root-path filter and collection function; keep five discovery/leaf page caps and serial detail collection.
- [ ] Add a DIY configuration with 50 details and 50 image downloads.
- [ ] Run focused tests and `python -m pytest -q`.

### Task 6: Execute DIY/Tools collection and final audit

**Files:**
- Output: a new `runs/<timestamp>` directory
- Modify: `task_plan.md`, `findings.md`, `progress.md`

- [ ] Run DIY/Tools collection once with no retries or concurrency.
- [ ] Download the observed main images serially.
- [ ] Verify unique SKU count, detail count, image result count, field availability, access-event states, and minimum request interval.
- [ ] Update the report and planning files with the exact outcome; do not claim full completion if a stop condition occurs.
