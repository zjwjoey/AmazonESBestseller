# Main Consolidation V2 — Module Integration Status

Baseline rechecked on 2026-10-20: `origin/main` was `f62d3265c46cb809ecb98dc6e8f8ccdd492e6c68`. PR #10 then merged as `bb26de10a892748e1534d02231fc9918ee230e92`.

## Completed in V2

### PR-A — source-bound candidate handoff

- Source: V1 mainline ranking records plus V4 candidate URL-group semantics from `codex/v4-global-30000`.
- Added `CandidateObservation` and `candidate_id`/`candidate_observation_id` separation in `handoff.py`.
- Existing `identity.resolve_identity`, detail checkpoint, audit, Translation V2 and export contracts remain the owners of their existing facts.
- PR #10 is merged. Python 3.11 and 3.12 CI passed. Targeted Python 3.12 checks passed.

### PR-B — saved-evidence multi-source candidate snapshot (in progress)

- Source branches reviewed: `codex/v4-global-30000`, `codex/v4-serial-read-optimization`, `codex/v4-three-source-5001`, and `codex/highspeed-multisource-v4`.
- Extracted only the conservative V4 product-link normalizer and Customer Favorites DOM adapter. The highspeed runner, multi-source scheduler, authorization system and isolated storage backend are not imported.
- Ranked pages use the mainline Ranking Identity adapter. Supported source families are Best Sellers, New Releases, Movers & Shakers, Most Wished For, Most Gifted, and the reviewed Customer Favorites page subtype.
- `amazon-es --offline candidate-snapshot --manifest ... --out-dir ...` reads checksummed saved HTML, writes a no-overwrite snapshot artifact and retains the original page, SHA-256, all source observations and raw values.
- Candidate groups use exact normalized product-link evidence. Each source-page appearance remains a separate observation. Product identity stays `UNCONFIRMED`; Parent ASIN is not used to merge candidates.
- Best Sellers rank and other list positions keep distinct semantics. The offline fixture run produced 36 Best Sellers, 36 New Releases and 163 Customer Favorites observations (235 total); this is fixture coverage, not a 30,000-item collection.
- Snapshot and each source remain `UNVERIFIED`; `ready_for_detail_plan=false`. An observed product page or a nonempty parse is not treated as a complete source.
- Targeted V2 tests currently pass; PR and GitHub CI are pending.

## Reused mainline modules

- Ranking Identity candidate extraction and ASIN/URL validation.
- Existing `identity.resolve_identity` for confirmed detail identity.
- Existing `detail-plan` → `detail-run` → `detail_executor` for formal detail actions.
- Translation V2 source hashes, schema versions, field identity and raw Spanish preservation.
- Existing QA/Field Closure and frozen Excel exporter.

## Remaining V2 work

- Accept an explicitly reviewed candidate freeze in the existing detail-plan path without treating candidate ASIN hints as confirmed identity.
- Build the read-only Source Audit Evidence Adapter over mainline detail-run/state/saved HTML; audit remains blocked until its sidecar accepts only verified evidence.
- Extract only the compatible orchestration/commands facade and build the complete fixture/mock offline flow.
- Run the full offline regression, update README/AGENTS/architecture docs and report each status separately.

The V4 performance, browser dispatch, highspeed and multi-lane implementations remain experimental. No production collection, translation provider call, or production data operation was performed.
