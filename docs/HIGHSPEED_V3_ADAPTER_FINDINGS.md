# V3 adapter evidence and limits

All work here is offline. Historical fixtures are not V3 LIVE captures. V2 remains the default; V3 requires every approved source to carry `parser_profile: V3`. The V2 version and `_title_from_card` implementation remain unchanged for immutable historical validation. V3 emits `link_first_adapters_v3.card_bound`.

## Per-entry findings

| List | Saved source evidence | Product cards | Finding |
|---|---|---:|---|
| BEST_SELLERS | `p0_native_carousels.html` + existing saved price-collision cards | 6 in two saved carousel wrappers | Literal product href/rank and two independent category scopes. Each carousel metadata declares 50 opaque request IDs; those IDs are not candidates. Native continuation remains unobserved/incomplete. |
| NEW_RELEASES | `real_v3_new_releases_root.html` | 36 | Real root carousel product cards parse. This proves historical parser handling, not all categories or current website availability. |
| MOVERS_SHAKERS | `real_v3_movers_shakers_root.html` | 0 | Actual saved right column empty; no affirmed same-list category navigation here. `PARSER_EMPTY_UNVERIFIED`, not EMPTY and not successfully collected. |
| MOST_WISHED_FOR | `real_v3_most_wished_for_root.html`; saved DVD official-empty fixture | 0 | Root has navigation and an empty right column; DVD has affirmative official empty component. Distinct NAVIGATION_ONLY vs SOURCE_EMPTY; neither COMPLETE. |
| MOST_GIFTED | `real_v3_most_gifted_root.html`; saved devices official-empty fixture | 0 | Root has navigation; devices official empty. Correct content H1 overrides generic gift banner. Neither proves live product availability. |

Each new real root fixture includes SHA, original evidence path, source URL and historical origin. No current network request was made. The source audit establishes that all 91 configured non-BEST sources were visited: GIFT/WISH each have only 30 department roots, MOVE only one root, versus BEST 1611 deeper sources. Missing supply is not merely unscheduled work and cannot be attributed solely to DOM change. Source discovery depth, affirmative empty/navigation evidence, and unobserved native contracts remain separate causes. There are no saved real nonempty MOVE/WISH/GIFT fixtures in this evidence set, so synthetic negative/binding tests do not establish their LIVE success.

## Concrete defects and fixes

- V2 could select a title under a different product anchor in one broad card container. V3 requires the exact original product href, with explicit single-target semantic attributes permitted. It never synthesizes a title from a URL. Truncated text is kept literal and marked TRUNCATED; literal image alt is IMAGE_ALT, numeric semantic book titles are permitted. Prices, ratings, review labels and wrong-target anchors are excluded.
- Explicit sponsored-ad DOM markers are excluded from ranked observations in V3. Ordinary books, digital products, unrated and low-rated products are not excluded.
- Native root carousels use `data-a-carousel-options.ajax.id_list`, not only grid client-recs metadata. V3 records each independent carousel scope and opaque original metadata, and requires native request discovery for its missing cards. No speculative ACP endpoint is manufactured, no metadata ASIN becomes a card, and incomplete roots remain PARTIAL.
- Grid client-recs selection prioritizes the actual grid instead of the first unrelated page component.
- Carousel ACP fragments require `ranking_context_binding` with the original page SHA, source ID and a scope actually found in its saved SERVER cards. Unbound fragments are flagged and excluded rather than incorrectly assigned to the root scope. Binding proof retains page evidence path, original SHA and ACP SHA for offline title validation. Transport must produce this binding from a validated native request, never infer it from an unrelated response.
- `page_content_state` distinguishes PRODUCT_CARDS, SOURCE_EMPTY, NAVIGATION_ONLY, NATIVE_CONTINUATION_UNRESOLVED, PARSER_EMPTY_UNVERIFIED, WRONG_SOURCE and ACCESS_RESTRICTED. Official emptiness requires exact source-specific direct H4, an empty client-recs list and endOfList, with no card/product/expected-count/ACP evidence. This annotation never changes FAILED/UNKNOWN into COMPLETE or authorizes skipping a failure by itself.

## Compatibility and remaining validation

V3 title review binds the profile/version in the sealed manifest; mixed profiles are rejected. V2 immutable raw titles and old review artifacts remain unchanged. Consumer continuation/quality code must dispatch strict original parser verification by version. Ratings are extracted through the card-local ratings API using actual href, representation and local DOM slot. Full V3 ratings tests are owned by that module.

T1 still must establish current real sources and native continuation contracts for each list. T2 must measure supply and completeness. No T1/T2/T3 pass or 10-hour throughput is claimed from these offline tests. The runner must not map SOURCE_EMPTY/nav/unverified to successful completeness, and all shared access restrictions still stop requests.

P0 receipt audit found nine ACP request receipts, all for actual grid nextPage contracts (including preserved historical HTTP400 and NETWORK_ERROR). No saved successful or observed native carousel POST contract is present. Carousel T1 remains required; no grid request is re-labelled as carousel success.

## Executable native preparation protocol

V3 runner passes the exact source ID into each independently pending carousel descriptor. Transport reopens the saved parent HTML, verifies its SHA and unique opaque carousel-options text, and resolves an actual wrapper ID/path/ACP params and category scope. The browser then checks the same attributes in current DOM and clicks only that wrapper's actual next control. The capture route continues aborting unmetered ACP traffic. Only an actual POST matching the exact owner path, ACP params and parent page receives `ranking_context_binding` and immutable capture evidence. A metered scheduler request reuses its endpoint and body byte-for-byte; there is no speculative endpoint or carousel batch-body derivation.

Missing owner evidence, missing next control, unobserved contract and ambiguous contracts retain source incompleteness. V3 attempts each independent group once within bounded interaction discovery, preserves failure reasons, and records UNKNOWN completeness rather than accepting the first missing group as proof of all-group success. Actual HTTP/access failures still stop the shared gate. Existing V2 grid preparation is unchanged.

Runner propagates the binding into immutable ACP receipts and parser response envelopes for resume and offline verification. Source iteration calls the plan-selected scheduling policy without mutating approved source records, and candidate-count query time is recorded as DEDUP. Original started time and wall-clock bounds are unchanged. Tests exercise real saved owner DOM and mocked route/scheduler protocols; they do not prove a current carousel contract or T1 pass. Native response availability and subsequent browser-driven carousel batches still require bounded T1 verification.
