# V3 candidate snapshot contract

Contract: `candidate_snapshot_v3.3`. Parser: `link_first_adapters_v3.card_bound`.
URL resolver: `link_url_v3.1.lexical_tracking`. Category rules:
`reviewed_category_mapping_v3.2`. The production Excel contract is unchanged.

## Candidate identity

The candidate key is derived from the full safe canonical product URL. The
original href remains immutable. Confirmed navigation tracking suffixes and
explicit tracking query parameters can be removed with recorded lexical proof.
Variant parameters, unknown query bytes, opaque product routes and unreviewed
path suffixes remain distinct. Coarse route groups describe possible duplicates;
they never collapse V3 target candidates. Title similarity and Parent ASIN are
not grouping rules. URL equality does not prove an independent purchasable SKU.
Different canonical URLs sharing a coarse route key remain separate candidates
with `POSSIBLE_DUPLICATE` / `PENDING_IDENTITY_REVIEW`; unknown parameters,
fragments and unreviewed path suffixes likewise retain that review state.

## Record fields

Each candidate exports `candidate_id`, `product_title_es`, `product_title_status`,
`raw_product_url`, `normalized_product_url`, `url_identity_status`, `dedup_status`,
`rating_raw`, `rating_value`, `rating_status`, `rating_source`, `rating_evidence`,
`rating_observed_at`, `review_count_raw`, `review_count`, `review_count_status`,
`review_count_evidence`, `source_type`, `source_url`, `ranking_position`,
`rank_semantics`, `original_amazon_category`, `category_path`, `browse_node_id`,
`research_category`, `category_mapping_status`, `first_observed_at`,
`latest_observed_at`, `observation_count`, `source_memberships`, `snapshot_id`,
`batch_id`, `parser_version`, and `evidence_reference`.

Nested memberships preserve every observed ranking context, raw href, local
card slot, rank, page URL, source, original title proof and capture time. Nested
rating history preserves all paired rating/review observations and their card
binding evidence. `raw_observations` retains full original observation fields.
CSV nested objects use JSON text; JSONL contains the same structured values.

## Missing values and conflicts

Ratings use `VALID`, `NOT_DISPLAYED`, `PARSE_FAILED`, `SOURCE_CONFLICT`, or
`UNVERIFIED`. Missing values stay null, never zero. A valid rating is 0–5 and a
review count is a nonnegative integer. Both derive from the same exact card.
The versioned aggregation rule chooses one paired observation and preserves
all conflicts and historical values. A rating from one source must not be
silently paired with a review count from another. A truncated title stays
truncated; image-alt recovery is explicit and no AI title completion occurs.

Selection rule `LATEST_VALID_OBSERVATION_WITH_PAIRED_REVIEW_FALLBACK_v2`
selects the latest valid rating with its same-observation review fields. When
no valid rating exists, the latest valid review observation can supply the
paired display; its original missing or failed rating status and raw text
remain. Conflicting same-time pairs are unresolved and not guessed.

Category mappings must cite reviewed source navigation evidence. Unknown
and source-conflict categories stay visible. Books, digital content, low
ratings and unrated cards are not filtered in snapshot collection.

## Capture and QA boundaries

`HISTORICAL_REPLAY` records have `is_derivation=true`, original capture run and
time, sealed receipt and journal references. They consume no new network
budget and cannot satisfy fresh LIVE T3. Every filled field is independently
reparsed from its exact saved card during central QA, beyond hash checks.
Page completeness is independently rebuilt from the SERVER representation
and actual bound ACP fragments. A standalone ACP fragment cannot prove a
complete source. Unknown and failed pages remain unknown or failed.

Future selection hooks are `selection_audit_status=NOT_RUN` and
`selection_rules_version=null`. This development does not execute selection,
detail collection, translation, image downloads or Action matching.
