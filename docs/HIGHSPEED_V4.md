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
