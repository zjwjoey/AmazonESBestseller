# Amazon Crawler V2 reference integration

## Review status

This is an isolated review branch based on the cached `origin/main` commit.
It is intended to finish as `READY_FOR_EXTERNAL_REVIEW`; it is not a claim
that the branch is ready to merge into `main`.

## What was added

- Category Graph V1: marketplace-aware placement IDs include the full category
  path, so the same Amazon category ID can safely have multiple parents.
  Traversal state is atomic, resumable, serial, and validates parent/path
  consistency. A latest-authoritative graph is written only after validation
  passes.
- Ranking Snapshot V2: audits server-rendered count, ACP metadata, hydration
  count, duplicate ASINs, duplicate/gapped ranks, access state, and authority.
  Duplicate ranking rows are counted before ASIN deduplication; the product
  identity remains the ASIN.
- Transport Adapter V1: a small protocol plus Playwright adapter over the
  existing browser session, optional curl-cffi experiment, shared failure
  taxonomy, Amazon.es locale evidence (`requested_locale`,
  `observed_language`, `language_mismatch`, `currency`, `postal_code`, and
  optional `marketplace_id`/`fingerprint`), and a browser/manual fallback
  contract. No automatic fallback or bypass is introduced.
- Product Parser V2: preserves ordered duplicate detail labels, variation and
  parent evidence, page identity evidence, and category provenance while
  reusing the existing detail parser.
- Spanish locale normalization: price, rating, and review-count parsing keeps
  decimal/thousands semantics explicit for `amazon.es`.

## Production call-path integration

- `ranking-snapshot --parser-version v2` reparses the saved `ranking_*.html`
  evidence through Ranking Snapshot V2.  The normal Playwright transport
  adapter is selected by default; `--transport legacy` is retained for
  compatibility.  ACP hydration remains an explicit callback, so a missing
  31--50 response is recorded as incomplete instead of being guessed.
- `detail-run --parser-version v2` uses Product Parser V2 for cache reuse,
  saved-HTML reparsing, and new detail pages.  The selected parser version is
  written into the execution manifest; V1 remains the default.
- `category-graph-validate --state <state.json>` validates the resumable
  placement graph offline and reports whether an authoritative graph may be
  published.  It never contacts Amazon.
- The curl-cffi adapter and browser fallback are deliberately opt-in
  boundaries.  They do not silently replace the primary Playwright path or
  recover from access restrictions automatically.

## Reference mapping

The design review covered the three requested public projects:

1. `omkarcloud/amazon-scraper`: session warm-up, locale cookies, ACP list
   metadata, and the need for explicit request/parser failures. Its compact
   label-to-value detail dictionary was not copied because this repository
   must preserve duplicate ordered attributes.
2. `browser-act/skills`: browser-assisted extraction and manual intervention
   were treated as an optional diagnostic boundary, not as an authority or a
   CAPTCHA solver.
3. `asinspotlight/amazon-product-categories`: category placement identity and
   resumable tree traversal informed the graph/state model. Its external API
   is not used.

## Safety and scope

- No live Amazon request was made by this branch.
- No formal 5,000-SKU workbook or production data was modified.
- No proxy rotation, CAPTCHA solving, cookie rotation, account rotation, IP
  rotation, stealth bypass, or third-party API was added.
- Existing Access Gate, saved HTML, checkpoint, snapshot authority, ASIN
  identity, and raw-detail contracts remain authoritative.
