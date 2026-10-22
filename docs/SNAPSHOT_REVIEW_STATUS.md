# Current PAGE snapshot review status

`CURRENT_THREE_SOURCE_PAGE_SNAPSHOT_V1` uses its own snapshot gate. It does
not require the historical eight-entry/native-loading stage to pass.

| Field | Meaning |
| --- | --- |
| `snapshot_review.status` | `PASSED`, `FAILED`, or `PENDING` for the complete saved-page DOM/receipt replay. |
| `snapshot_acceptance` / `snapshot_metrics.acceptance` | Final snapshot acceptance: the review and all configured quantity minima must pass together. |
| `quantity_target_met` | Quantity only; does not establish review success. |
| `quality_gate_scope` | Explicitly identifies the current PAGE snapshot contract. |
| `quality_gate_passed` | The current snapshot review **and** quantity targets passed. Interpret with its scope. |
| `legacy_stage_acceptance_claimed` | Always false for this task contract. |
| `SKU_identity_status` | `NOT_VERIFIED`; candidate counts do not confirm independently buyable SKUs. |
| `production_acceptance` | `NOT_EVALUATED`; snapshot success is not production acceptance. |
| `source_complete` | Remains false; hitting a bounded target does not exhaust Amazon sources. |

The runner, `highspeed status`, `snapshot_progress.json`, and task monitor expose these fields. A passed
review can coexist with a quantity shortfall; a stopped run without complete
review coverage remains pending. An audit error cannot become a pass.

On resume, the current review becomes pending in both the run metadata and
progress projection before page work starts. Quantity accounting is restored
before the first stop check, so an already satisfied target cannot trigger an
extra page request. The final progress projection uses the actual stopped state.

The monitor uses the configured global target and persisted clock duration,
including an approved extension, for its progress estimates. Current PAGE-only
tasks can pass the initial observation with normal PAGE requests; an ACP request
is not required. The existing access and process-health checks still apply while
running. Normal stopped runs do not generate missing-process or stalled-request
alerts merely because collection has ended.

## Correcting a previously completed run

Use `amazon-es --offline highspeed snapshot-review-status --root <store>
--run-id <run-id> --out <new-report.json>`. This command acquires the run lock,
requires a stopped current-contract run and its final `V4_CENTRAL_DOM_REVIEW`
event, checks review coverage, and recomputes quantity accounting from saved
observations. A capture or resume after that review invalidates the correction.

It creates a new before/after report and a correction event, then updates only
derived display metadata. It starts no browser, makes no network requests,
and leaves the original QA, HTML, receipts, observations, plan, checkpoint and
completion time intact. It neither reruns collection nor creates a legacy-stage
or production approval. If the underlying evidence has changed, run a complete
offline review instead of reusing the old result.
