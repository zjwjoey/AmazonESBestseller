# V4 multisource discovery

## Baseline and boundaries

Remote `codex/highspeed-snapshot-v3` was checked with fetch and ls-remote:
`7ad773722f0545f38e77bb1a9c4211916fd92dc6`. GitHub run 38567511666 succeeded.
Development uses `codex/highspeed-multisource-v4` in an independent worktree.
Production main remains at 2f6292e with its pre-existing 22 tracked modifications.
No historical data, access gate, translation or human notes are modified.

The October 14 browser survey manifest contains 193 verified SHA/size entries.
Thirteen complete rendered DOM frames are copied byte-for-byte into fixtures;
the transient empty Deals frame and eleven empty deep-page findings remain
distinct from verified product supply. Survey DOM has NO observed HTTP status
and is never a new LIVE receipt. Truncated early survey files are not fixtures.

V3 T1 stopped at 33/50 requests and 20/20 PAGE, with 212 raw observations and
144 URL candidates. It did not pass T1: missing supply, unknown completeness
and three ambiguous manual UI reservations remain. The new task does not erase
those failures or inherit that approval.

## Reuse map

* `highspeed.adapters`: BEST/NEW card ownership, title/rating extraction,
  category context, explicit empty detection and ACP fragment parsing.
* `transport.playwright`: intercepted native contracts, metered requests and
  saved-response local UI replay. Later carousel progression needs LIVE proof.
* `highspeed.v3_links`: lexical URL tracking rules and variant preservation.
* `highspeed.store`, `capture_history`: immutable receipts, journal, checkpoint.
* `highspeed.authorization`, `budgets`, `access`: exact reviewed scope,
  prerequisites, request reservations, stop conditions and shared Access Gate.
* `highspeed.category_mapping`: explicit evidence mapping; no guessed themes.

V4 adds a source registry and independent non-ranking card adapters, explicit
field semantics, evidence-backed navigation discovery and export/QA. It must not
relax the V3 runner's five-ranking-source contract to impersonate a new source.
MOVERS_SHAKERS, MOST_WISHED_FOR and MOST_GIFTED code remains available; default
V4 supply status is SOURCE_PRODUCT_SUPPLY_UNVERIFIED_OR_UNAVAILABLE.

## Contract

Candidate equality is exact safely normalized URL equality, never title or
parent-ASIN equality. Raw observations and source memberships remain separate.
Only BEST and NEW may carry numeric ranking_position, scoped to their original
carousel/category. Other sources carry discovery semantics, not inferred ranks.
Rating count means evaluations, not written reviews. Monthly bought is a public
lower bound; Haul cart interest and Bestseller badges are separate raw signals.
Source missing, unverified, parse failure and conflict remain distinct states.
Reference price is preserved without assuming it is a former transaction price.
Unknown source capacity and page completeness stay UNKNOWN.

## Execution boundary

Development, offline fixtures and preparation are authorized. No V4 LIVE request
is authorized by this document. T1, T2 and T3 each need separate explicit approval
of exact URLs, request/PAGE budgets, duration, expiry and stop conditions. T3 is
a new batch, excludes historical 30026 from new capture, targets 30000 candidates
and at most ten hours; neither supply nor performance is yet demonstrated.

Rollback: use the unchanged V3 worktree/branch and its existing CLI. Do not merge
V4 to main, revert original data, reset gates, or seed fresh capture with history.

## Offline sample and performance boundary

The eight stable survey frames contain 336 observations, 334 exact safely
normalized URLs and 295 conservative review families. These are three different
metrics. Family grouping does NOT merge the 334 candidate records or confirm
SKU identity. Unknown query/variant bytes are preserved. They are not fresh
captures; new V4 LIVE observations and requests remain zero.

Each export runs a central complete-DOM reparse, then independently reads CSV
and JSONL to verify counts, URL sets, source memberships and file SHA256. Source
contributions are sample memberships/unique-only counts, not future capacity.
Aggregation is one grouping pass plus sorting, and reuses existing transactional
RunStore indexes. Offline duration does not demonstrate 3000 LIVE candidates/hour.

## Current executable boundary and unresolved work

`v4_cli` implements offline replay, pending plan generation, approval checking,
and a bounded T1 entry/protocol diagnostic runner. The old CLI/runner explicitly
reject V4 plans before browser work. The dedicated runner preserves the existing
request reservations, shared/local gate, serial pacing, original start, locks,
saved receipts, native BEST/NEW ACP validation and local UI replay.

Non-ACP native fetch/XHR is aborted and saved as unapproved discovery evidence.
There is no guessed Deals/Haul API, unmetered scroll loading, automatic pagination
approval, or claim that those dynamic loading protocols have been implemented.
This means a source that needs a new native protocol can stop T1 with its actual
incomplete frame. That frame is NOT an official empty page. Later scoped T1
diagnostics must establish and implement those exact contracts before T2/T3.

The prior-stage validator intentionally rejects LIVE capability summaries until
the new protocols have receipt-level validators. T2/T3 execution is not enabled
by this first development delivery. Full V4 development/acceptance is therefore
not complete; source parsing, offline QA and controlled T1 preparation are ready.

## Commands

Run from this worktree with PYTHONPATH set to its `src`,
PYTHONDONTWRITEBYTECODE=1, PYTHONIOENCODING=utf-8 and RUN_LIVE unset.

```powershell
python -m amazon_es_bestseller.highspeed.v4_cli --help
python -m amazon_es_bestseller.highspeed.v4_cli replay --fixtures tests/fixtures/highspeed_v4/manifest.json --out <new-delivery-directory>
python -m amazon_es_bestseller.highspeed.v4_cli prepare --fixtures tests/fixtures/highspeed_v4/manifest.json --out <new-plan-directory> --runtime-root <new-V4-runtime-parent> --shared-gate <existing-shared-gate>
```

Generated plan and authorization are pending, disabled/unapproved. Approval must
bind the final enabled plan SHA, exact eight URLs, 50 total metered requests,
20 PAGE maximum, one hour, serial minimum three seconds, zero category cooldown,
expiry and restriction/error stops. This is proposed scope, not inherited V3
approval. Eight exact PAGE URLs do not approve any discovered page 2 URL.

Only after separate human approval, using the exact approved files:

```powershell
python -m amazon_es_bestseller.highspeed.v4_cli check --root <approved-new-store> --plan <approved-plan> --authorization <approved-authorization>
python -m amazon_es_bestseller.highspeed.v4_cli run --root <approved-new-store> --plan <approved-plan> --authorization <approved-authorization> --profile-dir <new-profile> --headful --live
python -m amazon_es_bestseller.highspeed.v4_cli resume --root <same-store> --plan <same-plan> --authorization <valid-same-run-authorization> --profile-dir <same-profile> --headful --live
python -m amazon_es_bestseller.highspeed.v4_cli report --root <same-store> --run-id <same-run-id> --out <new-delivery-directory>
```
