# Three-source 5,001 campaign

The separate `V4_THREE_SOURCE_5001` contract targets BEST_SELLERS,
NEW_RELEASES and CUSTOMER_FAVORITES, each at least 5,001 qualified conservative
review groups. Global overlap never fills a source shortfall. Its aggregate
15,003 legacy target field means the sum of three source minima, not a global
uniqueness promise. Independent buyable SKU identity remains unverified.

The original V4 eight-seed T1 and five-family T2 contracts remain intact. The
additional literal Favorites root without referral parameters belongs to this
task; it is not silently added to the old eight-seed diagnostic acceptance.
No task document or offline queue grants LIVE authorization.

`three_navigation` discovers owned sidebar children, theme navigation, facets,
results entries and consecutive Next links separately. Every anchor retains
literal href, parent page/source, DOM index/hash, HTML SHA and capture time.
Ranking page 2 must be an actual `.a-last` anchor, preserve category and unknown
filters, and increment `pg` by one. Favorites uses the existing strict srs/filter
chain. No depth, leaf, Browse Node, page number or request URL is invented.
Unvisited nodes and unresolved hierarchy stay visible in the persisted graph.

`three_contract` validates independent targets, exact three-family scope,
finite per-family requests/PAGE/native caps and matching global totals. A
discovered source carries verified navigation ancestry to a task root; its Next
links extend that source rather than pretending to be new categories. Approval
must separately match the campaign contract, all targets and family budgets.

`three_metrics` reports raw observations, raw links, normalized URLs, qualified
source URLs, source review groups, global review groups and unverified SKU
identity separately. Only current-run LIVE observations with title, card proof,
valid normalization and no binding doubt qualify. Historical/foreign-run rows
never increase counts. Field conflicts, duplicates and missingness remain.
Unknown variant/query bytes stay separate URL candidates but cannot inflate the
coarse identity group. Current lack of SKU verification means zero verified
identities, not zero buyable products.

Development proceeds offline in `codex/v4-three-source-5001`. Before LIVE the
specific finite URL set, request/PAGE/native budgets, duration, stop policy,
plan SHA and independent authorization must be reviewed. Further discovered
URLs remain pending until another exact-scope approval. Historical T2 findings
are planning evidence, never a new campaign seed or fulfilled 5,001 quota.

## Execution and recovery

`v4_cli run/resume/check` accepts T3 only with the exact three-source contract;
ordinary T3 plans remain rejected. The dedicated subclass reuses V4 page/native
capture, metering, serial pacing, shared run/gate locks, immutable start and
reservations. Per-family request/PAGE/native caps are checked before reservation
and accumulate across resume. A family budget stop does not consume another
family's allocation; access restrictions and unknown safety stop the whole run.
Interrupted native pages require a charged reopen of the same approved URL;
finished scope pages use the existing checkpoint. Discovered navigation is
persisted as pending evidence and never appended to the executable work list.

One campaign may contain several separately approved fresh batches. A batch may
cover a subset of the three families, but all three campaign targets stay 5,001.
Its task SHA, campaign ID and original creation time are approval-bound. Neither
request nor approval may predate campaign creation. A new batch never resets an
interrupted run's budgets: it needs new exact-scope approval. Old T2 and 30,026
records are not eligible campaign inputs.

Request safety uses SQL-only budget counters; candidate summaries are outside
the per-request hot path. An execution-local source-proof cache performs full
initial DOM validation, then hashes every dependency, including historical
reservation/receipt/body. Changed evidence repeats full validation. Final QA
always independently re-reads full DOM and receipts.
Mutable shared Access Gate state is checked separately before requests; it is
not an immutable prior-stage DOM dependency. An explicitly verified Gate
recovery must not trigger another full replay of unchanged T0/T1/T2 evidence.

## Historical navigation, preparation and reports

```text
python -m amazon_es_bestseller.highspeed.v4_cli review-three-archive --root <old store> --out <new inventory JSON>
python -m amazon_es_bestseller.highspeed.v4_cli prepare-three-source --t2 <formal T2 manifest> --archive <inventory JSON> --selection <selection JSON> --task <task text> --out <new parent> --runtime-root <runtime parent> --shared-gate <existing Gate>
python -m amazon_es_bestseller.highspeed.v4_cli report --root <approved fresh store> --run-id <same run> --out <new parent>
python -m amazon_es_bestseller.highspeed.v4_cli aggregate-three-source --batch <batch1 manifest> --batch <batch2 manifest> --out <new parent>
```

Archive inspection uses SQLite `mode=ro`, actual sealed HTTP200/NORMAL PAGE
receipts and their matching reservations/body. The current parser must recognize
actual ranked products before that old page becomes a navigation context. Old
products are discarded from this import. Old run/time/body SHA remain evidence,
not proof of current destination supply. Unknown hierarchy remains a forest.

Selection kind is `THREE_SOURCE_5001_SELECTION`, with a nonempty `reason`, exact
unique `seed_urls`, `prior_source_ids`, `historical_source_urls`,
`navigation_source_urls`, ordered `pagination_urls`, `source_budgets`, and
`limits`. Family budgets use `requests`, `PAGE`, `native`; totals must equal
global limits. `limits` contains exactly `max_duration_hours`,
`max_native_requests_per_source`, `max_raw_observations_per_run`,
`history_observation_limit`. `--campaign <prior same-task plan>` retains campaign
identity, not its data or request budget. Each generated plan is disabled and
authorization is unapproved without dates. Pending and enabled-preview SHAs are
both reported; no enabled file or browser is produced.

Full DOM, original reservation/approval/request/body checks precede the twelve
requested exports. CSV/JSONL counts, URLs, conservative groups, memberships and
SHA are independently reread. Grid coverage uses actual declared rank values,
including page-two 51--100; missing ACP slots stay PARTIAL. Favorites terminal
status requires affirmative disabled-Next evidence. Missing links alone cannot
prove completeness. NEW future/preorder/stock signals are derived from the same
card with explicit dates/raw evidence; the global future-release banner is not
an item-level assertion. No signal means unknown release status, not confirmed
ordinary available stock or independent buyability.

Ranked secondary pages use `multisource_v4.4.owned_ranked_pagination` only after
verifying the literal ordered Next chain. Source authorization remains keyed by
source ID; the hashed page key is only a checkpoint. The original v4.3 failed
page-two context remains replayable for history, while new captures retain the
actual page number and ranks. `ie=UTF8` added by an owned Next is not a category
change, and unknown filters or forged Next proofs are still rejected.

Older offline root fixtures may omit `page_urls`; their exact
`actual_source_url` alone supplies page number 1. A different page URL still
fails unless it belongs to an explicit ordered page list. Dynamic native
descriptor validation is reused only within an execution review session after
its full proof succeeds. Each reuse rehashes the native contract, parent DOM,
owner document, root receipt and preceding receipt/body where present; changed
source/scope/run/descriptor inputs also force full validation. Independent final
QA outside a session continues to replay all DOM proofs.

The campaign merger re-audits each supplied batch's original store read-only,
DOM, receipts and exports. Different tasks/campaigns, duplicate batch IDs,
modified files, failed audits and old-stage reports block aggregation. Repeated
products across batches remain raw history but add no source/global groups.
Quantity, scoped completeness and independent SKU identity have separate results.
Below any source's minimum the exact outcome remains
`NOT_ACCEPTED — THREE_SOURCE_5001_TARGET_NOT_MET`.
