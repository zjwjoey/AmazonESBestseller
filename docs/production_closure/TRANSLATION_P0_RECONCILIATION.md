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
