# DS Confirmation and CLI Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent unintended DeepSeek calls, repair the offline vertical-path test, and keep the documented pipeline accurate.

**Architecture:** `translate-ds` remains the only DS network entry point. It will reject `--offline`, show a bounded request summary, and require an explicit interactive `YES` before the first request. Tests inject a fake translator and exercise normalized-product translation after enrichment.

**Tech Stack:** Python, argparse, pytest, Markdown.

---

### Task 1: Add failing regression tests

**Files:**
- Modify: `tests/test_cli_smoke.py`

- [ ] Add tests asserting `--offline translate-ds` exits before translator construction and that a non-YES confirmation exits before any translation call.
- [ ] Rewrite the vertical test order to enrich first, translate normalized products, then enrich with the overlay; assert hash/schema-bearing output is accepted.
- [ ] Run the focused tests and confirm they fail for the current implementation.

### Task 2: Implement the CLI safety and pipeline fixes

**Files:**
- Modify: `src/amazon_es_bestseller/cli.py`

- [ ] Reject `args.offline` in `cmd_translate_ds` before constructing `DeepSeekTranslator`.
- [ ] Print a request summary and require `YES` from stdin before the first DS call; abort without calling the translator otherwise.

### Task 3: Synchronize documentation and verify

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/CURRENT_STATE.md`
- Modify: `src/amazon_es_bestseller/run_manifest.py`

- [ ] Document the confirmation gate and corrected `enrich → translate-ds → enrich` sequence.
- [ ] Correct the `write_manifest` return-value docstring.
- [ ] Run the full pytest suite, compileall, and diff check.

