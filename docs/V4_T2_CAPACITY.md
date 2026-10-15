# V4 T2 capacity diagnostics

T2 reuses the bounded serial request engine, immutable stores, native browser
contracts, shared Access Gate, pacing and resume. It accepts only an explicitly
approved T2 plan with independently verified T0/T1 references. T3 remains closed
in the V4 CLI. No automatic phase promotion or historical seeding is supported.

`python -m amazon_es_bestseller.highspeed.v4_cli prepare-t2 --t0 <T0 manifest>
--t1 <T1 manifest> --out <new directory> --runtime-root <runtime parent>
--shared-gate <shared gate>` creates **pending**, disabled files only. It copies
seven product-bearing roots from T1, retains the real lightning-empty evidence,
finds an exact same-context favorites Next href, and chooses two literal category
links from each ranked root's actually observed carousel. Root ref aliases and
guessed sibling paths are excluded. New category supply is unverified until T2.

The proposed limits are 200 requests, 20 PAGE requests, one hour, serial spacing
of at least three seconds, zero inter-category cooldown, 20 native requests per
ranked source and 20 dynamic requests per Deals/Haul source. With six ranked
sources, two dynamic sources and twelve exact PAGE URLs, the initial scope uses
at most 172 charged requests before any explicitly requested in-scope reverify.
The remaining budget is not permission to add URLs. The sample goal is 2,000
conservative URL review groups, not a quota guarantee or 30,000-SKU collection.

After separate exact-plan approval, `run`/`resume` supports stage T2 with the same
explicit `--live` switch and profile handling as T1. An expired approval, changed
plan, blocked gate, ambiguous reservation or concurrent collector blocks new
requests. Validated approval copies are persisted before the first request and
retained across resume. Original start time and cumulative budgets do not reset.
Initial cards are not a completed source-page scope. Interrupted native pages
are reopened only at the exact approved URL and charged to the same run; completed
pages are skipped. Per-source native/dynamic limits also accumulate across resume.

`report` produces the existing six exports plus `capacity_report_v4.json`.
Capacity acceptance re-reads the complete reservation/response directories,
their exact plan/start journals and approval bytes, validates request/final URLs,
request times, pacing, budgets, response bodies and central full-DOM field QA.
It requires observations from all five families, actual native/pagination growth
proofs and at least two dynamic receipts for Deals and Haul. An empty source,
unknown component or budget stop never becomes global COMPLETE.

The report separates raw observations, normalized links, conservative groups,
cross-source overlap, visible-rating coverage, monthly-bought status, per-source
request/PAGE timing, and observed supply lower bounds. The rate subtracts the
entire initial capture baseline. Linear 30,000 estimates are conditional, and
diagnostic acceptance does not prove 30,000 available products or authorize T3.
Prior-stage verification recomputes the capacity report, not just its boolean.

During one execution, prerequisites receive initial complete DOM review followed
by byte checks on every transitive evidence dependency and directory membership.
A change triggers full review again. Final stage/export validation always runs
independent central DOM review. This cache never bypasses current approval expiry,
plan scope, request budgets, pacing or Access Gate checks.

Offline tests: `python -m pytest tests/test_highspeed_v4_t2.py -q`.
Simulated transports are test-only; their generated records are never delivered
as actual T2 HTTP evidence. Official saved HTML remains the parser fixture source.

## Offline navigation review after a capacity shortfall

`python -m amazon_es_bestseller.highspeed.v4_cli review-navigation --t2 <T2 manifest>
--out <output parent>` first repeats formal T2 central DOM and capacity validation,
then inventories only literal navigation and Next anchors in the latest saved
PAGE for each exact source URL. It includes ranked sidebars and root carousel
category links, Favorites theme/refinement navigation and Next, and owned Deals
filters and Haul navigation. Product, advertisement, history and footer links are
excluded. No URLs are synthesized or fetched.

The new UUID directory contains `navigation_inventory.json`,
`pending_exact_urls.txt` and a SHA-256 `manifest.json`. Exact URL deduplication
preserves all unknown query parameters and every anchor provenance. Each record
retains the parent source, exact page, capture time, HTML SHA, literal href, label,
DOM anchor hash and index. Parent empty and UNKNOWN completeness states survive.
Statuses are ALREADY_OBSERVED, IGNORED_ROOT_ALIAS, PENDING_SOURCE_REVIEW,
PENDING_PAGINATION_REVIEW or UNVERIFIED_CONTEXT. Unsupported Favorites theme
destinations remain UNVERIFIED_CONTEXT rather than becoming approved search URLs.

This artifact is a disabled review queue, not a new execution plan. Pending URLs
do not demonstrate unseen product capacity, independent buyable SKU identity,
approval, or readiness for T3. All approval/live/T3 flags stay false and new
network requests remain zero. A supplementary T2 scope and budget still require
separate review; the expired prior run is not extended or reset.
