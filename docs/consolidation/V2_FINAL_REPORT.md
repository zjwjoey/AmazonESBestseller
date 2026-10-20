# Main Consolidation V2 — Interim Final Report

Date: 2026-10-20

Status: **PARTIALLY_CONSOLIDATED**

Current main at report time: `2f2a73c704acccd40c431270bcd2aec037eb9537`.

## Merged work

- PR #10: source-bound candidate handoff contract, including distinct
  candidate-group and candidate-observation identifiers.
- PR #11: offline saved-HTML multi-source candidate snapshots, using existing
  mainline Ranking Identity parsing and the bounded V4 Customer Favorites
  adapter. The live V4 runner and its parallel scheduler/storage were not
  imported.
- PR #12: candidate group/observation provenance carried through the formal
  ASIN-keyed `detail-plan` and per-run `detail-run` manifest.
- All three PRs passed GitHub Offline CI on Python 3.11 and 3.12. The local full
  offline suite reached 100%; focused tests passed before each PR.

## Mainline architecture and boundary

Main retains collection, Ranking Identity, detail planning/execution, Translation
V2 production, QA/Field Closure, and the frozen Excel contract. Candidate
snapshots are now available as a formal offline CLI command but remain
`UNVERIFIED` and cannot directly authorize details. The formal detail entry
remains `detail-plan` → `detail-run`, keyed by confirmed ASIN. V4/highspeed and
Detail5500 multi-lane runners remain experiments.

Source Audit still depends on sealed evidence and task-bound receipts from the
Detail5500/Multisource stack. Mainline detail-run does not provide those proofs;
no compatibility receipt was fabricated. Source Audit therefore remains
`BLOCKED` pending a truthful read-only evidence adapter.

No single command yet orchestrates candidate selection through translated Excel
export, and the full offline end-to-end business chain has not been accepted.
See `V2_END_TO_END_TEST_REPORT.md` and `V2_BLOCKERS.md` for the exact boundary.

## Experimental inventory and deferred performance work

The V1 branch inventory covers the original 47 remote branch heads. Of those,
V2 promotes only the bounded candidate snapshot/parser capability and detail
provenance contract. Highspeed/V4 live runners, alternate storage/schedulers,
and Detail5500 dual/triple/four/five-lane executors remain unmerged experiments;
no experiment was enabled by these PRs. Their count is not reported as a single
number because several remote branches are successive revisions of the same
experimental families, not independent product capabilities.

Deferred to a performance-focused phase: V4 snapshot throughput, handling
network-stop/recovery latency, Source Audit large-batch runtime, and measured
Detail5500 multi-lane safety/observability. Those topics do not justify
weakening identity, access-stop, evidence or QA gates.

## Production and follow-up status

No production collection or translation API was used. No production task,
database, checkpoint, saved HTML or translation cache was modified. This main
has a reviewed baseline and can be used for further development of the merged
modules; it is not yet a complete replacement for the full candidate-to-export
business workflow. Keep V2 marked `PARTIALLY_CONSOLIDATED` until Source Audit,
orchestration and the cross-module offline acceptance test are resolved.
