# HighSpeed Snapshot V3 development and historical baseline

Development branch: `codex/highspeed-snapshot-v3`. Verified remote baseline:
`8a7fcd831db5b1c1803d2e656a085428456eaf04` on
`origin/codex/multisource-highspeed-v2`. Production main and its local edits are
outside this development worktree. V3 is explicitly enabled; V2 defaults remain.

## Development A: authoritative historical input

The original nine-column CSV was read, not regenerated as a fixture. Its SHA256
is `f14443653c442e91bd49160a24a586ee5c0ec85580e41b9ce11ed2e1b601daf1`.
It contains 30,026 link-candidate representatives, not confirmed purchasable
SKUs. BEST_SELLERS contributes 28,930 rows / 785 source pages; NEW_RELEASES
contributes 1,096 / 27. The other three types contribute zero rows. There are
24,541 unmapped representatives, no empty research titles and no repeated CSV
URL or target-group rows. All 30,026 URLs still contain ranking ref/session
tracking paths; absence of identical strings is not proof of SKU uniqueness.

Alignment with the immutable full candidate JSONL was checked on every row.
All 30,026 representatives have accessible original local HTML with matching
SHA256; there are 2,806 distinct evidence references. The existing review labels
include 4,910 POSSIBLE_DUPLICATE and 25,116 UNIQUE_LINK_CANDIDATE rows. Rating,
review-count and complete price fields are absent from this CSV. These facts do
not establish what is currently displayed on Amazon.

The historical store contains 37,495 indexed observations, including 136
offline parser revisions; these are not additional network captures. The five
source types were actually attempted. GIFT and WISH each have 30 zero-card
visited sources and MOVERS one. Root navigation, official empty components,
unobserved native continuation and parser failure require separate diagnoses;
the zero results cannot be explained solely by scheduler order.

## Requirements and module ownership

* B: five-type page/card adapters, observed navigation source registry,
  carousel-native owner binding, explicit empty/navigation/unresolved states,
  and opt-in fair ordering. Never invent a URL or endpoint to fill coverage.
* C: card-bound raw and normalized ratings/reviews, independent DOM validation,
  five missing/error/conflict states, and immutable observation histories.
* D: reviewed node/navigation category policies with unknown/conflict retention;
  safe tracking-only normalization with variant/unknown identity data retained.
* E: bounded context caches, transactional persistence, native-preparation and
  dedup timing, original wall clock and resume sample epochs. No concurrent live
  requests or fixed category cooldown in the authorized V3 test configuration.
* F: whole historical dataset replay in a new store, strict original receipt
  references, full contract/membership export and independent field evidence QA.
* G: pending exact-source T1/T2/T3 plans, finite budgets, commands, prerequisites,
  access gate and rollback instructions. New live batches require authorization.

Candidate identity is link-first for this task. ASIN and parent evidence may be
retained but do not replace the link key or merge variants. Ratings, books,
digital goods, missing categories and low review scores do not filter candidates.
Product-selection audit remains NOT_RUN; no 5,500 ranking, detail collection,
Chinese translation, Action matching or image download is part of this work.

## Completion evidence

The deliverable must contain the baseline, 75-cell source coverage matrix,
per-entry root causes, rating/review denominators and proof, mapping repair and
conflict counts, URL/identity anomalies, performance comparison, tests and CI,
standard candidate CSV/JSONL contract, stage plans, executable PowerShell and
safe recovery/rollback. Historical fixtures and replay must be labeled as such.
Five real card supplies, real native-carousel contracts and fresh 30,000-link
throughput remain unproven until their authorized live stages provide evidence.
Prior files, HTML, journals, receipts, notes and Access Gate history stay intact.
