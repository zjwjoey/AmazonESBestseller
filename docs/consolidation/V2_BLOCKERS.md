# Main Consolidation V2 — Blockers and Follow-up

Updated 2026-10-20.

| Module | Status | Reason | Next safe action |
|---|---|---|---|
| Candidate snapshot → detail plan | PARTIAL | `candidate-snapshot` produces a checksummed, unverified candidate artifact; current detail plan still requires an authoritative identity snapshot and does not read URL candidates with unresolved identity. Candidate group/observation IDs now pass through detail planning only after that identity gate. | Add an evidence-backed selection/identity handoff; never promote candidate ASIN hints alone to detail task identity. |
| Detail5500 public capability | REUSED | Mainline `detail-plan` → `detail-run` → `detail_executor` already owns ASIN scheduling, cache/checkpoint reuse, saved-HTML reparsing, per-item outcomes and access-stop behavior; multi-lane executors remain experimental. Candidate provenance propagation is in PR-C. | Continue only with small compatibility gaps shown by a failing cross-module test. |
| Source Audit | BLOCKED | Existing sidecar expects `detail_bridge5500`, `multisource.storage` and sealed source evidence. Mainline exposes detail-run manifests, `DetailState`/checkpoints and saved HTML under a different contract. | Implement a read-only Evidence Adapter from those mainline artifacts. Preserve missing provenance as unverified; never fabricate a receipt or PASS. |
| Unified orchestration | NOT STARTED | Main CLI already has module commands, but no verified single offline end-to-end command connects candidate freeze through export. Production closure branches also change runtime/checkpoint contracts. | Add a thin command facade over existing handlers after the contracts are tested; no wholesale production-branch cherry-pick. |
| Full offline business loop | NOT STARTED | Candidate snapshot is now replayable, but detail planning, source audit, translation mock/cache, QA and Excel export have not yet been composed through one cross-module test. | Continue PR-C onward using saved evidence and fake provider only. |
| V4 live collection runner | EXPERIMENTAL | V4 branches include independent scheduler, authorization and storage behavior and are too broad to declare mainline-compatible as a unit. | Keep the parser/replay extraction bounded; reconsider runner integration only through existing transport, Access Gate and mainline snapshot contracts. |

The V2 final status must remain `PARTIALLY_CONSOLIDATED` while a critical item is blocked or the complete offline flow has not passed.
