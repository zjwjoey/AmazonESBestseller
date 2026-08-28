# Amazon.es Field Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Repair the 200-SKU export pipeline so requested quotas remain globally unique, unconfirmed Parent ASIN values are not exported, and cached page evidence is reused to fill only fields supported by source evidence.

**Architecture:** Keep Amazon as the evidence source and preserve raw values. Add a deterministic repair/enrichment step between collection and export: select globally unique ASINs, merge parsed cached HTML only when the ASIN is confirmed, normalize Parent ASIN status, then run the existing normalization/translation/QA/export chain. Missing values remain blank when evidence is absent.

**Tech Stack:** Python 3.12, BeautifulSoup parser already used by the project, pytest, openpyxl exporter.

---

### Task 1: Prevent cross-group duplicate ASINs

**Files:**
- Modify: `src/amazon_es_bestseller/collection/quota.py`
- Test: `tests/test_quota.py`

- [ ] **Step 1: Write the failing regression test**

Add a case where the same ASIN appears first in `hogar` and then in `diy`; assert the selected result contains two different ASINs and raises `QuotaError` when no replacement exists.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run `pytest tests/test_quota.py -q`; the new test must fail because the current selector tracks duplicates only inside each group.

- [ ] **Step 3: Implement the minimal global identity fix**

Keep a `seen_global` set in `select_quota`; skip any ASIN already selected in another group, while preserving existing per-group quota and source order behavior.

- [ ] **Step 4: Run focused and full quota tests**

Run `pytest tests/test_quota.py -q` and then `pytest -q`; all tests must pass.

### Task 2: Remove unconfirmed self-parent values at normalization boundary

**Files:**
- Modify: `src/amazon_es_bestseller/pipeline.py`
- Test: `tests/test_models.py` or `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Pass a record with `asin == parent_asin` and `parent_asin_status == "self_reported_unconfirmed"`; assert normalized `parent_asin` is empty while a `confirmed` parent ASIN is preserved.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run `pytest tests/test_pipeline.py -q`; the new assertion must fail because the current pipeline copies the parent value unchanged.

- [ ] **Step 3: Implement the minimal boundary rule**

In `normalize_product`, preserve `parent_asin` only when it is a valid 10-character ASIN and its status is not `self_reported_unconfirmed`; retain the raw/status evidence in the record when available.

- [ ] **Step 4: Run focused and full tests**

Run `pytest tests/test_pipeline.py -q` and `pytest -q`.

### Task 3: Add an offline cached-HTML repair command

**Files:**
- Modify: `src/amazon_es_bestseller/cli.py`
- Modify: `src/amazon_es_bestseller/collection/detail.py` only if a parser selector is proven missing
- Create: `src/amazon_es_bestseller/collection/repair.py`
- Test: `tests/test_detail_collection.py` or `tests/test_cli.py`

- [ ] **Step 1: Write a failing test for ASIN-confirmed cached HTML merge**

Create a temporary `html/<asin>.html` fixture containing a seller, struck price, and monthly-bought text. Assert the repair helper fills only blank canonical fields for the matching ASIN and ignores a file whose page ASIN does not match.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run `pytest tests/test_detail_collection.py -q`; the helper does not yet exist.

- [ ] **Step 3: Implement the smallest repair helper**

Enumerate cached HTML files, extract the ASIN from the page, call `parse_detail_page`, and merge only non-empty raw fields into the matching product. Never overwrite a non-empty current value and never fill a field from a different ASIN.

- [ ] **Step 4: Add a CLI command and run tests**

Add `repair-cache --products --html-dir --out` to write repaired canonical JSON through the existing `enrich_products` path. Run the focused test and `pytest -q`.

### Task 4: Rebuild and verify the workbook from repaired evidence

**Files:**
- Modify: `docs/CURRENT_STATE.md` and the relevant run report only if they describe the old 193-SKU result.
- Create: a new ignored output directory under `outputs/`.

- [ ] **Step 1: Generate a globally unique 150/50 manifest from cached ranking evidence**

Run `amazon-es select-quota` against the cached ranking records and reviewed category configuration. If the source cannot supply 50 unique DIY ASINs, stop and report the exact shortfall instead of fabricating replacements.

- [ ] **Step 2: Repair cached product details and re-enrich**

Run the new offline repair command, then `enrich`, `qa`, `audit-fields`, and `export` using the repaired data and existing image directory.

- [ ] **Step 3: Verify the workbook**

Check both product sheets have identical ASIN order, 200 unique rows (or a documented source shortfall), no self-parent values, no formula errors, and no data inferred without evidence. Confirm image coverage and field completeness before claiming completion.

