# Snapshot fixes integration scope

This change integrates the already developed, isolated snapshot implementation
into main so the recent fixes have their required modules. It introduces no new
architecture and does not deploy to a production checkout or start collection.

## Baselines

- Mainline base: `cbc15da` (Main Consolidation V2 closure retained).
- Snapshot source: `c5cca41`, built on the existing experimental snapshot chain.
- Prior implementation baseline: `2ef8a6f`.
- Recent task/fix commits: `86d3c7b`, `d505e77`, `9102908`, `c3fece5`,
  `4254495`, `19ee79a`, `1ac784d`, `c5cca41`.

Main did not contain `highspeed` or `multisource`; selecting just the final
repair commits would produce missing imports and unusable code. The diff thus
includes these namespaces, their shared browser/transport evidence primitives,
explicit CLI adapters, offline tests, byte-preserving fixtures, documentation,
and unapproved planning templates. Most added lines are saved test HTML.
The existing mainline candidate-to-detail, audit, Translation V2 and Excel flow
is preserved, with its integration tests retained.

## Recent behavior repaired

- Receipt-bound PAGE task with a verified Spain destination and first-level
  category evidence; ASIN hints cannot promote a candidate to a saleable SKU.
- Exact query-order aliases and saved-response replay without repeating
  completed pages, resetting cumulative budgets or dropping access stops.
- Favorite-source scheduling and explicitly approved same-run page budget
  amendments, without changing already sealed task plans.
- Proven official empty ranking pages retain empty/unknown completeness
  semantics; unknown or restricted pages are not silently accepted.
- Independent snapshot review/quantity acceptance, explicitly separate from
  legacy stage proofs and production acceptance.
- Stop an already complete resume before another page request, invalidate old
  review success on restart, and use the actual task target/clock in monitoring.

## Boundaries and verification

Runtime databases, 20k batch output, browser profiles, approvals, checkpoints,
translation caches and delivery files are excluded from Git. Existing saved
data stays in its current run directories. No database migration is run.

The existing completed batch was checked read-only: 20,018 conservative candidate
groups, 935 requests, completed DOM review of 24,952 observations; its eight
business data table fingerprints were unchanged. These are prior batch results,
not a new collection or a production acceptance claim.

All CI/default tests remain offline; `RUN_LIVE` must equal the exact string `1`
to opt into live tests. Both supported CI Python versions must pass on this PR's
final revision before merge. The single- and multi-source/native runner code is
kept as explicit experimental tooling, not automatically enabled production work.
