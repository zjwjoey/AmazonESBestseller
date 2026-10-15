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

## Current executable boundary and unresolved work (2026-10-15)

`v4_cli` implements offline replay, pending plan generation, approval checking,
and bounded T1/T2 runners. The old CLI/runner rejects V4 plans before browser
work. The dedicated runners preserve reservations, shared/local gates, serial
pacing, original start, locks, receipts and local replay. Native BEST/NEW grid
and carousel requests and captured Deals/Haul loading contracts now have
receipt-level validators. Unknown protocols remain aborted discovery evidence.

The completed T2 diagnostic `v4_t2_15da5305d976` captured 2,274 observations,
666 normalized URLs and 619 eligible conservative review groups using 84 charged
requests, including 14 PAGE requests. Its final full-DOM/receipt review passed;
the 2,000-group sample target was short by 1,381. All five loading capabilities
were observed, but global source completeness and sustained 30,000-candidate
supply remain unknown. The original lightning source's EXPLICIT_EMPTY evidence
and all failed/stopped histories remain valid historical records.

The saved-DOM navigation inventory and evidence-bound continuous Favorites
pagination are implemented as offline development. `review-navigation` produces
a disabled queue of literal URLs. Pagination plans validate an ordered sequence
of saved Next links with consecutive page numbers and unchanged filter bytes;
each destination still needs exact request approval. `prepare-t2-supplement`
now converts an explicit bounded selection into a fresh disabled T2 proposal,
after repeating formal prerequisites and checking each selected anchor against
its original T2 DOM. It preserves the prior empty-source evidence, exact URLs,
finite budgets, unapproved authorization and both pending/enabled-preview plan
hashes. See `V4_T2_CAPACITY.md` for the command and selection contract. Real
capacity measurement still requires separate exact-plan approval. A navigation
link proves only that the link was present, not that its destination supplies
products. The current T2 approval is not extended by offline development or
discovery. T3 execution remains closed; no promotion, fresh 30,000 collection,
historical seeding or main merge is authorized.

The later three-source task uses a separate `V4_THREE_SOURCE_5001` campaign
contract and independent exact-plan approval. Its T3 marker does not promote
the earlier five-family diagnostic or open generic 30,000 collection. See
`THREE_SOURCE_5001.md` for source-specific quotas, batches and execution limits.

The development objective is 30,000 effective deduplicated product-link
candidates. Confirmed independent buyable child-ASIN identity is a separate
future product-detail check, not a prerequisite invented for this candidate
phase. Retain variant parameters and identity doubts, and never relabel URL
review groups as confirmed buyable SKUs.

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
