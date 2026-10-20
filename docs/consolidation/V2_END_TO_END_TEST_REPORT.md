# Main Consolidation V2 — Offline Integration Acceptance

Date: 2026-10-20  
Formal baseline: `origin/main` after PR #12, `2f2a73c704acccd40c431270bcd2aec037eb9537`.

## Executed checks

- PR-A handoff contract and PR-B saved-HTML candidate snapshot tests passed on Python 3.12. The candidate fixture replay produced 235 observations across Best Sellers, New Releases and Customer Favorites. It remains an unverified fixture result, not a collection target completion.
- PR-C detail planner, detail executor and CLI planner suites passed: 30 tests. These verify ASIN-keyed detail planning and candidate provenance in run outcomes.
- The complete repository offline pytest suite ran locally to 100% before PR-B merge. After PR-C, GitHub Offline CI passed for Python 3.11 and 3.12 in both push and pull-request workflows.
- CLI help smoke and Python source compilation passed in those CI workflows.
- The source branch's V4 adapter/workflow suite passed 24 tests and its multi-source source/identity/snapshot suites passed 160 tests. These are source-branch compatibility evidence, not tests of the V2 integrated package.

## Business loop result

The requested full chain is **not yet an accepted end-to-end test**. V2 has not
run one test that composes candidate extraction, reviewed identity/selection,
frozen detail planning, saved detail replay, Source Audit, Translation V2 fake
provider/cache, QA and Excel export through their cross-module interfaces.

The candidate snapshot is deliberately not accepted directly by `detail-plan`:
it reports `UNVERIFIED`, and its ASIN observations are not confirmed product
identity. PR-C preserves candidate IDs only after an authoritative identity
snapshot has supplied the ASIN. The remaining explicit blockers are listed in
`V2_BLOCKERS.md`.

No Amazon collection, real Qwen/API call, production database/state change,
frozen-task change, or historical data operation was performed.

## Status by acceptance level

| Capability | Developed | Integrated into code | Offline tested | Merged to main | Production accepted |
|---|---:|---:|---:|---:|---:|
| Candidate handoff DTO | Yes | Yes | Yes | Yes (PR #10) | No |
| Saved-evidence multi-source snapshot | Yes | Yes | Yes | Yes (PR #11) | No |
| Candidate provenance through detail planning/run | Yes | Yes | Yes | Yes (PR #12) | No |
| Formal detail execution | Existing mainline | Yes | Existing offline tests + PR-C tests | Yes | Not assessed here |
| Source Audit adapter for mainline detail evidence | No | No | No | No | No |
| Single-command candidate-to-export orchestration | No | No | No | No | No |
| Full cross-module offline business loop | No | No | No | No | No |

V2 status: `PARTIALLY_CONSOLIDATED`.
