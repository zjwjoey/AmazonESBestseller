# Bounded dual pilot on the existing frozen run

The user authorized deployment at the currently measured pace. 1,000 requests
per hour remains an aspirational dispatch cadence, not an acceptance prerequisite
or a promise of successful distinct products. Keep the 3.6-second non-bursting
dual cadence, all original cumulative caps, holds, deadlines, gates and full QA.

Production dual runs require a SHA-bound `dual_stage_reference` in the original
run directory. Its exact task objects must be the first original pending tasks
at handoff. Cumulative caps 100, 500 and 1,000 mean increments 100, 400 and 500.
The scheduler gets only that cohort; admission separately rejects foreign or
duplicate tasks. Completed tasks and reviewed failures cannot be reclaimed.

Each successful scheduler completion drains every captured/parser/write task,
writes an immutable `dual_stage_<limit>.drained.json`, and closes its browser.
It does not call FINAL, write a final run manifest, or start the next stage.
The drained result explicitly has `passed=false` until an independent saved-HTML
and receipt/checkpoint/full-record audit accepts the pilot. Later stages require
an immutable SHA-bound prior independent acceptance for this run and plan.
Failures stop admission and preserve evidence. They do not expand the cohort or
turn the transaction cap into a success quota. Stage restart is not automatic.

Keep the current three-lane collector running during development and CI. A
single-owner safe drained boundary, original profile, success SHA baseline and
cumulative budget are required for handoff. Never run both collectors against
the shared profile or expand the total parallelism to five.
