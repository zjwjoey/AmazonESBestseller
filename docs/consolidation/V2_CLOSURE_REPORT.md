# Main Consolidation V2 Closure

## Acceptance status

Status: `READY_FOR_PR_CI` (local offline acceptance passed; remote CI and merge are pending).

This closure uses `origin/main` at `a65d60dece81c28c6e6c8f97e59c3154219dd6d2` and was developed in the independent `main-consolidation-v2` worktree. No production database, task state, checkpoint, saved HTML, translation cache, or historical output under `F:\AmazonESBestseller` was modified. No Amazon collection or real translation API was run.

## Candidate and detail identity handoff

`candidate-snapshot` still labels ranking candidates and source coverage `UNVERIFIED`. The new `candidate-identity-verify` command loads the persisted candidate artifact only after validating the candidate JSON and each saved ranking HTML hash. It verifies the ranking card ASIN against its product-link ASIN, parses the saved ASIN detail page, and uses the shared identity resolver. The resulting `CANDIDATE_IDENTITY_SELECTION` is explicitly neither `AUTHORITATIVE` nor `IDENTITY_COMPLETE`; it is eligible for `detail-plan` only when candidate IDs, observation IDs, identity evidence, and both source/detail HTML hashes are present and still valid.

The formal plan remains keyed by final ASIN. `detail-run` rechecks the saved-detail hash, writes parser-derived facts to normal `DetailState`, and retains candidate and observation provenance in the outcome manifest. The real-evidence offline test links candidate `link_…` → `ranking_observation_…` → `B013ERLQDK`. Its source ranking page remains correctly incomplete (`UNVERIFIED`); no completeness or authority flag is promoted.

## Source Audit adaptation

`source-evidence-audit` is available in mainline. It reads the detail execution manifest, checkpoints, `DetailState`, and saved HTML without writes to the run. It grades records as actual request/response evidence, hash-verified saved evidence, or insufficient evidence. If ranking records are supplied, it delegates selected checks to the existing Quality Gate, including ranking identity, access evidence, detail identity, offline replay, detail structure, category provenance, and Field Closure.

The adapter creates no Receipt and never claims a formal Source Audit PASS. The original branch's sealed receipt verification, repair/re-audit, and translation-preparation workflows retain their independent experimental status because their request/task-bound receipt contract is not present in standard mainline detail-run artifacts.

## Offline business chain

An integration test runs the actual CLI and modules in a temporary directory:

`candidate-snapshot` → `candidate-identity-verify` → `detail-plan` → offline `detail-run` → `source-evidence-audit` and mainline Quality Gate → Translation V2 production stages with the fake provider and cache → translation QA → QA/Field Closure-gated debug Excel export.

The produced workbook retains the three-sheet contract, the Chinese 26-column header, and identical ASIN sets in Spanish and Chinese sheets. The export is marked debug/not-for-release because the test intentionally uses a fake provider. Negative cases confirm that unverified candidates and missing or mismatched detail evidence do not enter a detail plan.

## Scope and remaining acceptance

The V4/highspeed live runners and Detail5500 single/dual/three/five-lane executors remain experimental. This closure does not start a live collection, Qwen translation, large-scale performance effort, or production release. Existing reports of slow snapshot parsing, network-stop recovery, and large-batch Source Audit runtime remain follow-up optimization work.

The full local offline pytest suite passed after the Windows lock cleanup compatibility fix. The cross-process budget test had previously failed intermittently because Windows can temporarily deny unlink while another process reads the lock metadata; cleanup now leaves the stale owner record for the existing PID-based recovery path. PR CI and merge status will be added after remote checks complete.

| State | Result |
|---|---|
| Developed | Candidate identity selection, evidence adapter/CLI, detail provenance persistence, offline E2E test |
| Code-integrated locally | Yes, on the dedicated consolidation worktree |
| Offline tests | Full suite passed; targeted integration/adapter tests passed |
| Merged to `main` | Pending PR review and GitHub CI |
| Real production acceptance | Not run by design |
| Known P0 blocker | None identified in the tested offline closure |
