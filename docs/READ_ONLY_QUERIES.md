# Query and metadata correction boundaries

`highspeed status`, `qa`, `report`, `gate-export` and `snapshot-review-status`
open an isolated DB+WAL copy through `ReadOnlyRunStore`. Missing stores fail
without creating a directory or database. A source changing during capture is
rejected; callers may retry later. No SQLite connection opens the source index.
Reports and gate artifacts must be written outside the source directory.

`snapshot-review-status --out ...` previews a stopped run's display correction
from an existing final audit. It does not publish the correction or append events.
The separate `snapshot-review-correct --apply --out ...` explicitly publishes
that correction, retaining the existing stopped-run, final-audit and run-lock gates.
Neither command starts collection or grants production acceptance.

Execution, recovery, identity review and renormalization retain explicit mutation
semantics. This change does not deploy code or touch a running task's original
plan, clock, data, access gate, checkpoint or browser profile.
