# Offline Canary evidence review

Only the bounded scope, ranking five-tuple and stable detail replay comparisons
are extracted from `chore/v2-canary-readiness` at `8c302c58`. Its browser runner,
repeated live page-1 visits and production stage orchestration are not imported.

`amazon-es --offline canary plan --source-url <reviewed-Best-Sellers-URL>` creates
an offline scope proposal. Bounds are one source, one or two pages, at most five
detail ASINs, and zero network requests. There is no live execution flag.

`amazon-es --offline canary review --manifest <manifest.json> --out <external.json>`
replays saved evidence using current ranking/detail parsers and Ranking Identity.
The manifest contract is `CANARY_OFFLINE_REVIEW_V1`:

- `source_url`: the single reviewed Amazon.es Best Sellers path.
- `pages`: ordered page 1/2 objects with `url`, `page_number`, `http_status`,
  `observed_at`, reviewed `expected_count` (1..50), `html` and `observed_records`.
- `details`: optional objects with `asin`, exact `final_url`, `http_status`,
  `html` and `observed_detail`. ASINs must appear in the replayed ranking evidence.
- Every file reference is exactly `{ "path": "...", "sha256": "..." }`.
  Relative paths resolve against the manifest. Sealed JSON checksums are verified.
- Optional paired `task_plan`/`task_clock` references check the existing run/plan
  binding only. This is not a clock-expiry or execution-admission decision.

Access status and Spain destination are rechecked on every saved HTML page using
the current detector/header parser. Missing/unknown destination, challenge,
403/429, incomplete/conflicting identity, rank gaps/duplicate slots, changed
parent/ordered attributes, tuple mismatch and evidence tampering cannot pass.
No ACP callback, browser, provider or collector is constructed. Missing ACP
evidence results in an incomplete offline check, never a new request.

Reports are new immutable artifacts outside the evidence roots. An offline pass
is scoped to the supplied evidence: `live_authorized=false`,
`ready_for_detail_plan=false`, `source_completeness_claimed=false`, and production
acceptance remains `NOT_EVALUATED`. Existing raw evidence, plans, clocks and
checkpoints are neither promoted nor rewritten. Current browser/access/Spain
preflight is still required by the separately approved collection entry point.
