# Current entry points and historical executor retirement

The 2026-10-29 remote review separates branch references from functionality.
Deleting an archived branch reference does not erase its commit, archive tag,
local checkout, saved evidence or original runtime recovery code.

## Batch 1: current main interfaces

| Retired ordinary entry | Current behavior | Preserved capability |
|---|---|---|
| `translate-ds` | Removed from public parser/help; old invocations return the existing `TRANSLATION_API_REMOVED` compatibility stop before input/cache/provider I/O | Offline cache readers and formal staged `translation-production` |
| `highspeed run/resume` with a LIVE-stage plan | Rejects before opening a store/browser; T0 fixture execution remains available | PAGE snapshot entry, shared HighSpeedRunner, parsing, gates, saved reports/replay |
| Legacy `multisource` LIVE CLI | Rejects before starting collection; offline planning/replay and identity review remain | Source adapters, journals, evidence and governed identity |
| Generic non-PAGE V4 protocol execution | Requires explicit `--experimental-protocol-diagnostics`; all original plan/authorization/budget/Spain/access gates still apply | Offline prepare/report/check and bounded protocol diagnosis |

The third review retirement family covers the last two rows. The protocol flag
is not a source/task approval or bypass. Ordinary PAGE plans do not require it.
A legacy `check` without that opt-in cannot report execution as authorized.

## Batch 2: historical branches only

Old Translation P0/production-closure dedicated real-call/release executors and
Detail5500 single/dual/old-three/five-lane scripts leave active maintenance.
They are not copied back to main. Archive references and a verified offline Git
bundle retain their original implementations and recovery cases.

Source Audit receipt/repair/translation-prep, paid-response crash accounting and
the Production V1 stage-artifact graph remain independent archived references.
Their useful contracts are not declared duplicates or deleted. Future extraction
needs compatibility evidence against current main, not a whole-branch merge.

## Supported paths and acceptance

Keep `detail-plan` / `detail-run` and the separately approved `detail-five-run`,
current PAGE snapshot configuration/report/resume and its reviewed three-window
adapter, raw dynamic attributes, ASIN authority, confirmed/null Parent ASIN,
human notes, Spain/access gates, fixed task clocks/budgets, staged translation,
QA/Field Closure, and the frozen three-sheet/26-column business export.
Configurable targets 1..100,000 and durations 2..168 hours remain new-plan bounds;
the 100k template is not proof of website supply or production completion.

Offline Canary is described in [CANARY_OFFLINE.md](CANARY_OFFLINE.md).
Query/correction behavior is described in [READ_ONLY_QUERIES.md](READ_ONLY_QUERIES.md).
Developed, offline-tested, merged-main, deployed and production-accepted states
remain distinct. This consolidation does not deploy or restart a running task.
An existing runtime uses its pinned code and state; no hot update is performed.
