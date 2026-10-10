# Translation V2 P0 Reconciliation Foundation

This is an offline evidence reader and accounting tool. It does not replace
TranslationService, ProviderPool or TranslationCache. The only cache API used
against historical inputs is its static key contract. In particular it never
constructs TranslationCache on a real file: load() can rename corrupt evidence.

## Entry points

```powershell
$env:PYTHONPATH = 'src'
python tools/prepare_translation_reconciliation.py --original-output-root <original-outputs> --out <new-preparation-directory>
python -m amazon_es_bestseller.translation.reconciliation reconcile --config <new-preparation-directory>/config.json --out <new-report-directory>
python -m amazon_es_bestseller.translation.reconciliation benchmark --out <new-synthetic-diagnostics-directory>
```

All output directories must be new and outside protected original evidence.
The four-run preparation recipe uses only explicit run directories and their
hash-bound dependencies. Additional runs need an explicit reviewed config;
there is no directory-wide newest-file search or automatic live task discovery.
Configuration contains names/paths only, never credential values.

`reconcile` takes `allowed_roots`, `protected_roots`, explicit ordered `batches`,
an optional `cache`, optional hash-bound `selection`, `derived_qa`,
`supporting_evidence`, `cohorts` and `runtime_observation`. A batch points to its
directory and optionally its exact manifest binding. A resume may use the root
run context from its configuration. Results are selected by reviewed run lineage,
not modification time. Frozen parent results and observed current cache data
retain separate columns and evidence paths.

## Completion contract

| State | Meaning |
| --- | --- |
| SOURCE_MISSING | Prepared source is absent; no value is inferred. |
| SOURCE_REVIEW_BLOCKED | Source review or item-bound owner exclusion prevents translation. |
| PRECLEAN_BLOCKED | The prepared field denies provider admission. |
| NOT_STARTED | No attributable text, claim, entry or send is observed. |
| CLAIMED_NOT_ENTERED | Durable claim exists without provider entry. |
| ENTERED_NOT_SENT | Provider entry exists without an HTTP send. |
| SENT_PENDING_OR_UNKNOWN | HTTP send exists but no usable settled text is available. |
| TEXT_AVAILABLE_UNREVIEWED | Text exists but its namespace or QA is not currently certified. |
| QA_FAILED | Saved automatic QA failed; this is not proof of a semantic mistranslation. |
| PARTIAL | At least one required parent/item has text, while required coverage remains incomplete. |
| QA_PASS | Saved QA passed; current-code review or closed structural coverage may still be absent. |
| COMPLETE | Same source/item and rendered text, current-rule bound QA, closed result and full required item coverage. |
| EVIDENCE_CONFLICT | Source identity, item coverage or evidence binding is inconsistent. |
| TRANSPORT_FAILED | A non-200 response is observed and no usable translated text exists. |
| UNKNOWN | A pending envelope lacks evidence sufficient to classify its send stage. |

COMPLETE is never RELEASE_READY. Identity/dictionary fields retain their recorded
QA state; they are not automatically re-certified by this tool. Structured fields
require every expected service item, with exact index, label and source text.
Parent text alone cannot close a structured field. Owner-excluded raw items are
separate non-required blocked trace rows, not successful translations.

Latest QA is opt-in evidence, not a re-QA execution. To recognize it, supply
`latest_qa_rule_version` and `current_qa_rules_hash`. The hash-bound review artifact
must carry the same `summary.rule_version`, `summary.qa_rules_hash`, matching
rendered-text hash and an explicit reviewed ASIN. The current code hash is produced
by `current_qa_rules_hash()`. Historical reviews without this binding stay in
derived-review columns. Original saved QA is never overwritten.

## Request and provenance accounting

Provider entries, semantic dispatches and HTTP attempts are counted separately.
The stable six-component memory identity is reused from TranslationCache.
Beneficiary ASINs come only from exact prepared source/unit keys, not request
counts. The provider-entry ASIN remains a separate owner association. Missing
owners stay UNKNOWN. Repeated HTTP attempts with no disambiguating response ID
remain ambiguous, rather than assigning responses by a guessed order.

Original request QA comes from immutable `memory_results`, once per observed
HTTP attempt. It is not obtained by adding entries/memory/results/memory_results
or counting every QA_RESULT_SAVED event. Derivative reviews are independent
evidence. HTTP non-200 rows never enter the HTTP-200 QA denominator.

Provenance distinguishes QWEN_DIRECT, TM_REUSED, CACHE_REUSED, DICTIONARY,
DETERMINISTIC_RULE, IDENTITY_PRESERVED, LEGACY_REVIEWED_REFERENCE and UNKNOWN.
The direct label requires an observed, source-linked send for the originating
ASIN. Legacy agent references are never Qwen requests or human gold.

## Evidence protection and reports

Each input receipt stores path, UTC read interval, size, SHA-256 and before/after
file signatures. A second hash observation follows accounting. A changed file,
partial JSONL tail or known living task makes the observation ACTIVE_PROVISIONAL.
Unchanged observations are STABLE_OBSERVED_NONATOMIC, never an atomic snapshot.
Missing/corrupt/hash-conflicting evidence is reported explicitly and is not
recovered, renamed or rewritten. Persistent lock existence is recorded with
owner UNKNOWN; the tool never acquires/releases a production claim lock.

Output contains manifest.json, reconciliation_summary.json, asin_completion.csv,
parent_field_ledger.csv, structured_item_ledger.csv, semantic_request_ledger.csv,
provider_http_ledger.csv, evidence_conflicts.jsonl, reconciliation_report.md and
cache_shutdown_diagnostics.md. The reviewed recipe also produces separate
saved_state, provisional_state and merged_coverage cohort ledgers.

Each CSV row links to the evidence manifest and records code HEAD and observation
status. Manifest records code-file hashes, input receipts, report hashes and limits.
Reports contain text hashes/availability, not copies of response bodies or Chinese
product text. Credential-like strings and credential/header keys are redacted.
Runtime observations and synthetic measurements are ignored Git artifacts.

## Stop and cache findings; changes deferred

The existing cache claim/settle paths load the whole JSON file and save all four
deep-copied dictionaries with fsync/atomic replacement. Cost grows with cache
size. Scalar processing can save a field claim, semantic claim, semantic settlement,
field settlement and record checkpoint; structured processing claims/settles every
item. The parallel service submits all records before its completion loop.

The runner stops HTTP at its final transport boundary, but neither the service
record iterator nor structured item loop stops admitting new pending claims.
The fake stopped-provider benchmark reproduces this without network activity and
records loads, saves, bytes read/written, elapsed time and traced peak memory.
Measurements are diagnostic observations, not live drain-time promises.

Next runtime hardening should stop NEW work admission before new claims while
settling already-entered attempts; retain uncertain sends without automatic resend;
use a reviewed append-only attempt/response journal and evaluate checkpoint/store
performance in a separate migration. Do not batch away per-response durability or
replace the production cache format in this foundation task.

## Verification

`tests/test_translation_reconciliation.py` covers selection/resume deduplication,
one-to-many semantic reuse, HTTP/dispatch separation, layered QA, structured child
coverage, owner exclusions, legacy references, changed/stable-live observations,
claim/entry/send distinction, manual stop reasons, malformed evidence, dry-run
exclusion, read-only/network boundaries, same-render current QA, credential
redaction, original empty-source envelopes and stratified cohort selections.

Focused tests run before the full Windows offline suite. CI uses the existing
offline Python 3.10/3.11/3.12 matrix. No production/main merge, live Qwen/Amazon
request, source promotion, formal Chinese Master or Excel is part of this task.

### Windows runtime blocker: isolated follow-up repair (2026-10-10)

The foundation originally reported `budget._FileLock._alive` calling
`os.kill(pid, 0)`. On Windows zero is CTRL_C_EVENT, not a POSIX existence check.
The original full-suite interruptions remain historical evidence; the diagnostic
remainder was never a full Windows pass.

The authorized follow-up replaces only this lock-owner probe. Windows opens a
SYNCHRONIZE-only, non-inheritable process handle, uses WaitForSingleObject with a
zero timeout and closes the handle. It sends no signal and requests no process
mutation rights. Only a missing valid PID or a confirmed terminated process can
be classified dead. Invalid PIDs, access denial, unavailable APIs, failed waits
or uncertain handle cleanup retain the lock. POSIX only classifies
ProcessLookupError as confirmed absence; permission/other failures stay held.
The boolean True means ALIVE_OR_UNKNOWN, not certified liveness.

The former strict-xfail is now a normal Windows regression. Fake Win32 tests
cover error paths on every CI platform; native Windows tests cover the current
process and an already-exited synthetic child with its process handle retained.
No live task is signalled, restarted or unlocked. Tests use only temporary locks.
This repair does not change budget limits, ledger/cache formats, retries, provider
rates, stop/admission semantics, translations or QA. Passing this repair does not
authorize production release or live translation resumption.

API references: [Python os.kill](https://docs.python.org/3.12/library/os.html#os.kill),
[OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess),
[WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject).

Follow-up Windows verification on 2026-10-10, Python 3.12.10:

- Budget/probe focused: 35 passed (3.42s), including the formerly interrupting
  cross-process test.
- Reconciliation/cache/context/pool/structured/budget focused: 120 passed (35.42s).
- Full offline Windows suite: 1487 passed, 6 skipped (924.70s), zero failures or
  xfails. Skips are 2 explicitly live collection tests and 4 generated-artifact
  fixtures absent from the source checkout; no budget test was deselected.
- Ruff, compileall and diff checks passed. Local receipts are in the isolated
  worktree's ignored `outputs/budget_windows_liveness_20261010_v1/validation/`,
  including `red.log`, `budget_focused.junit.xml`, `focused.junit.xml` and
  `full_windows.junit.xml`. The original interrupted observations remain intact.

This evidence closes the Windows probe blocker only. Stop/admission and cache
write amplification remain the next reviewed runtime-hardening slice.

### Stop/admission hardening: isolated follow-up (2026-10-10)

This slice stops *new admission*, not already-entered response settlement.
It remains offline/candidate-only; no original runner, process, stop marker,
Spanish source, translation text, QA rules, production cache or export is changed.

- `TranslationService(..., stop_requested=callable)` accepts a read-only stop
  probe. Probe errors fail closed. Structured-schema views retain the probe.
  A `PoolProviderAdapter` automatically exposes pool stop/health to the service;
  a serial service using an ordinary provider needs an explicitly bound probe.
- Parallel record execution maintains at most `pool.max_workers` admitted
  futures, rather than submitting every selected SKU in advance. Serial record,
  field and structured-child boundaries also check admission. A stop drains
  admitted workers; it does not cancel a response after its send.
- Untouched work is `not_started`, not a persisted `pending` provider claim.
  The returned `admission` section records selected/admitted (`started`) count,
  stop observation and untouched ASINs. `summary.total` continues to count
  returned record results, not the entire selected input. A worker admitted just
  before stop may return `not_started`; `started` is admission, not HTTP count.
- Already-published claims are retained as pending/manual review, never removed
  or labeled untouched. Provider exceptions/ambiguous transport outcomes remain
  `TRANSPORT_OUTCOME_UNKNOWN`; repeat runs and `repair_failed` do not replay them.
  Stop is not authority to release or resume a claim.
- Interrupted structured parents are not stored as terminal field renders.
  Settled child TM remains durable and a subsequent explicitly authorized run
  can reconstruct the parent without repeating completed children. Untouched
  siblings retain source text and item identity, not invented Chinese values.
- The formal structured execution wrapper propagates `admission` and keeps
  unscheduled translation tasks `not_started`, separate from source-blocked
  owner-review items. Identity facts remain deterministic source evidence.
- Durable pool halt is rechecked at admission, provider-lock acquisition and
  Qwen's actual send boundary, including retries and independent per-alias
  pacing. The send gate composes existing callbacks rather than replacing them.
  A detected durable halt is latched for that pool instance.
- With `stop_on_rate_limit=True`, the Qwen adapter returns the first HTTP429 to
  the pool instead of internally retrying it. The pool persists the stop and
  forbids failover. The default known-response retry/failover policy without
  that explicit stop policy remains unchanged.

The stop check and a filesystem change are not a cross-process transaction:
work crossing a claim/send boundary concurrently with stop remains an admitted
or uncertain attempt and is held conservatively. No new production cache format,
database, journal migration, rates, provider route count or QA gate is introduced.
QA report `pass` only means no reported QA issues; it is not COMPLETE or RELEASE_READY.
The unchanged original 850 runner does not acquire these semantics by editing this
isolated checkout. Applying this code to that run requires separate approval and
a reviewed evidence-bound recovery plan; no automatic live restart is performed.

Offline regression fixtures are in `tests/test_translation_stop_admission.py`.
Fresh local evidence is under `outputs/stop_admission_hardening_20261010_v1/`;
previous foundation and Windows-budget receipts remain historical evidence.

### Cache I/O hardening: isolated follow-up (2026-10-10)

This is a bounded offline optimization of VERSION 5 JSON, not a store migration.
Spanish evidence, translations, QA/dictionaries, provider pacing, production
processes and existing cache files are untouched. Every changed response still
flushes and fsyncs a temporary full JSON before atomic replacement. Unknown
attempts remain pending/held, never automatically released or replayed.

- `save()` now uses the same OS-owned lock as claim/settle. Nested operations
  reuse that lock. A stale instance adopts independent external changes and
  merges its own changed keys; overlapping updates fail with
  `CACHE_CONCURRENT_UPDATE_CONFLICT` without changing disk evidence. External
  unreadable evidence fails closed instead of being overwritten by a stale save.
- Unchanged serialized bytes avoid temp-file creation, fsync and replacement.
  Serialization references the four maps under the instance lock rather than
  copying every nested envelope. Individual public reads return deep snapshots.
  Sequential direct map edits remain detected by serialization; concurrent
  unsynchronized external mutation of those maps is not a supported API.
- Waiters use file metadata only as a polling hint, reloading changed snapshots.
  Real ownership/admission always reads bytes under the OS lock, even if size
  and timestamps appear unchanged. Pending timeout does not permit another send.
- Successful scalar results settle translation memory once, before publishing
  the in-process memo and settling the field. This removes a redundant full-cache
  read/write without postponing response durability.
- Full JSON reads/serialization remain O(cache size); the retained immutable byte
  baseline has a memory cost. No promise of production throughput is derived
  from small synthetic measurements. A journal/database migration needs separate
  approval and evidence, not a whole-batch durability shortcut.

All cooperating writers must use the new OS lock protocol. Do not deploy mixed
old/new writers against one shared cache. This isolated branch does not update,
stop, restart or unlock any existing production runner. Deployment/recovery is a
separate reviewed step, not authorized by successful offline tests.

Fixtures: `tests/test_translation_cache_performance.py` includes concurrent
independent processes, stale claims, conflicting writes, unknown outcomes,
fsync/replace failure, no-op persistence and VERSION 5 byte compatibility.
Fresh evidence is under `outputs/cache_performance_hardening_20261010_v1/`.
The original baseline and intentionally failing regressions are preserved.
Performance report V2 separates save calls from actual replacements and actual
JSON bytes read; persistent lock-file I/O is not included in those byte counters.
The opaque stopped fake (without an admission probe) is a negative control;
the explicit-stop control is separate. Neither sends HTTP or measures a live run.
