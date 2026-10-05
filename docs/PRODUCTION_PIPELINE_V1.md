# Production Pipeline V1 — factual DoD matrix

Last evidence review: 2026-10-05. This is a code-and-test inventory, not a
claim that a live production run occurred. **Overall: NOT_READY_FOR_MERGE.** A
fresh local full offline pytest run ended with exit code 0 on 2026-10-05
(`full_pytest_20261005_174000.exit`). This does not replace separately
approved live Amazon and Provider evidence.

Status vocabulary: `IMPLEMENTED` = code exists; `OFFLINE_VERIFIED` = focused
tests passed in this worktree; `LIVE_TRANSPORT_STOPPED` = a bounded live
transport preflight ran, but no ranking/detail source collection was allowed
to start; `LIVE_NOT_EXECUTED` = the indicated Amazon/provider production step
has not run; `PENDING` = integration/evidence still required; `BLOCKED` = a
release prerequisite is not yet satisfied.

| # | Requirement / entrypoint | Evidence | Status |
|---:|---|---|---|
|1|ASIN canonical identity|models + source fields tests|IMPLEMENTED/OFFLINE_VERIFIED|
|2|ranking records retain context|ranking snapshot tests|IMPLEMENTED/OFFLINE_VERIFIED|
|3|detail records retain raw attributes|detail parser fixtures|IMPLEMENTED/OFFLINE_VERIFIED|
|4|Best Seller rank separated from detail BSR|source audit rules|IMPLEMENTED/OFFLINE_VERIFIED|
|5|Amazon URL/ASIN validation|source audit rules|IMPLEMENTED/OFFLINE_VERIFIED|
|6|parent/variation identity evidence|source audit + master|IMPLEMENTED/OFFLINE_VERIFIED|
|7|raw Spanish immutable|Spanish Master|IMPLEMENTED/OFFLINE_VERIFIED|
|8|human notes preserved|Master/export/release tests|IMPLEMENTED/OFFLINE_VERIFIED|
|9|ranking authority stage|release stage evidence|IMPLEMENTED/OFFLINE_VERIFIED|
|10|detail identity stage|release stage evidence|IMPLEMENTED/OFFLINE_VERIFIED|
|11|saved-HTML replay stage|release stage evidence|IMPLEMENTED/OFFLINE_VERIFIED|
|12|stage record-hash binding|`build_stage_evidence`|IMPLEMENTED/OFFLINE_VERIFIED|
|13|stage schema/parser binding|stage evidence tests|IMPLEMENTED/OFFLINE_VERIFIED|
|14|empty PASS rejected|release adversarial tests|IMPLEMENTED/OFFLINE_VERIFIED|
|15|source audit|`audit_source_fields`|IMPLEMENTED/OFFLINE_VERIFIED|
|16|source gate|`evaluate_source_gate`|IMPLEMENTED/OFFLINE_VERIFIED|
|17|Master replays audit from raw source|release tests|IMPLEMENTED/OFFLINE_VERIFIED|
|18|Master artifact hash|Spanish Master tests|IMPLEMENTED/OFFLINE_VERIFIED|
|19|source-record bindings|SourceGate/Master contract|IMPLEMENTED/OFFLINE_VERIFIED|
|20|field-closure audit|field closure artifact|IMPLEMENTED/OFFLINE_VERIFIED|
|21|P0/P1 closure block|release regression|IMPLEMENTED/OFFLINE_VERIFIED|
|22|translation field cache source hash|translation cache/service|IMPLEMENTED/OFFLINE_VERIFIED|
|23|cache schema/dictionary isolation|translation tests|IMPLEMENTED/OFFLINE_VERIFIED|
|24|translation manifest record count|release validation|IMPLEMENTED/OFFLINE_VERIFIED|
|25|real-provider provenance required|release regression|IMPLEMENTED/OFFLINE_VERIFIED|
|26|Provider 0 real execution|no credential/run evidence|LIVE_NOT_EXECUTED|
|27|translation request budget|translation budget module|IMPLEMENTED/OFFLINE_VERIFIED; live accounting not executed|
|28|budget includes retries|budget ledger tests|IMPLEMENTED/OFFLINE_VERIFIED|
|29|future Qwen translation cap: <=1500 unique SKU|BudgetLedger + injected provider path|IMPLEMENTED/OFFLINE_VERIFIED; live run not executed|
|30|future translation total <=5 CNY incl retries|BudgetLedger + verified price-card contract|IMPLEMENTED/OFFLINE_VERIFIED; live pricing/run evidence absent|
|31|dictionary candidate evidence|dictionary sync|IMPLEMENTED/OFFLINE_VERIFIED|
|32|two independent facts / conflict handling|dictionary sync tests|IMPLEMENTED/OFFLINE_VERIFIED|
|33|controlled context dictionary entries|dictionary sync tests|IMPLEMENTED/OFFLINE_VERIFIED|
|34|manifest version/hash unified|dictionary sync|IMPLEMENTED/OFFLINE_VERIFIED|
|35|rerender affected fields only|rerender module|IMPLEMENTED/OFFLINE_VERIFIED|
|36|rerender QA before READY|release gate|IMPLEMENTED/OFFLINE_VERIFIED|
|37|selective repair queue|repair queue + production workflow|IMPLEMENTED/OFFLINE_VERIFIED|
|38|Chinese candidate source/target binding|release validation|IMPLEMENTED/OFFLINE_VERIFIED|
|39|Chinese QA field coverage|release regression|IMPLEMENTED/OFFLINE_VERIFIED|
|40|Chinese QA context/dict/schema hash|release regression|IMPLEMENTED/OFFLINE_VERIFIED|
|41|Chinese gate QA payload hash|release validation|IMPLEMENTED/OFFLINE_VERIFIED|
|42|Spanish/Chinese ASIN order|release alignment|IMPLEMENTED/OFFLINE_VERIFIED|
|43|URL/image alignment|release regression|IMPLEMENTED/OFFLINE_VERIFIED|
|44|notes byte/value preservation|release regression|IMPLEMENTED/OFFLINE_VERIFIED|
|45|frozen three-sheet workbook|`export_workbook` validation|IMPLEMENTED/OFFLINE_VERIFIED|
|46|Spanish 25-column validation|release export check|IMPLEMENTED/OFFLINE_VERIFIED|
|47|Chinese 26-column validation|release export check|IMPLEMENTED/OFFLINE_VERIFIED|
|48|formal ReleaseGate|`evaluate_release_gate`|IMPLEMENTED/OFFLINE_VERIFIED|
|49|sealed artifact transport hash|release tests|IMPLEMENTED/OFFLINE_VERIFIED|
|50|self-sealed authority not sufficient|release regression|IMPLEMENTED/OFFLINE_VERIFIED|
|51|debug cannot become formal|release tests|IMPLEMENTED/OFFLINE_VERIFIED|
|52|force cannot become formal|release tests|IMPLEMENTED/OFFLINE_VERIFIED|
|53|nonformal labeled DRAFT|release tests|IMPLEMENTED/OFFLINE_VERIFIED|
|54|formal export re-runs gate|release tests|IMPLEMENTED/OFFLINE_VERIFIED|
|55|hash-bound resume/checkpoint|state/checkpoint modules + workflow test|IMPLEMENTED/OFFLINE_VERIFIED|
|56|atomic state/cache writes|state/cache tests|IMPLEMENTED/OFFLINE_VERIFIED|
|57|incremental refresh design|see `INCREMENTAL_REFRESH.md`|IMPLEMENTED/OFFLINE_VERIFIED|
|58|incremental refresh execution|no production run|LIVE_NOT_EXECUTED|
|59|V1 stable parser remains default|V1 code path|IMPLEMENTED/OFFLINE_VERIFIED|
|60|V2 candidate is offline canary only|production canary tests|IMPLEMENTED/OFFLINE_VERIFIED|
|61|V2 default promotion prohibited|canary tests|IMPLEMENTED/OFFLINE_VERIFIED|
|62|V2 one-source/two-page/five-detail cap|canary profile/tests|IMPLEMENTED/OFFLINE_VERIFIED|
|63|5500 task excludes V2 canary|canary tests|IMPLEMENTED/OFFLINE_VERIFIED|
|64|AccessGate challenge StopAll|access tests; live 202 shell classified normal before delivery gate|IMPLEMENTED/OFFLINE_VERIFIED; challenge stop not live-observed|
|65|no proxy/CAPTCHA/stealth bypass|AGENTS + code policy|IMPLEMENTED/OFFLINE_VERIFIED|
|66|live Amazon ranking/detail collection|bounded delivery preflight `amazon_es_bestseller_5500_delivery_preflight_20261005t165100z`: 3 observed homepage navigations, 0 ranking/pagination/detail; stopped before source collection|LIVE_TRANSPORT_STOPPED|
|67|no Provider API in this release|run evidence|LIVE_NOT_EXECUTED|
|68|15 reviewed source categories unchanged|existing configs; no new sources; no category source collection after reviewed plan|IMPLEMENTED/OFFLINE_VERIFIED; live source evidence pending|
|69|5500 collection completed|no completed collection manifest/run evidence|LIVE_NOT_EXECUTED|
|70|CI offline matrix declared 3.10/11/12|`.github/workflows/ci.yml`|IMPLEMENTED; remote not verified|
|71|merge decision|this matrix + final integration|**NOT_READY_FOR_MERGE**|

Historical evidence (August 200-SKU and 496-SKU material) remains historical
evidence only; it does not prove this October V1 release. The old source-plan
count remains 15 categories. No unreviewed source was added.

## Command and scheduler responsibility gap

This is intentionally a separate matrix from the delivery DoD: it reports
where code lives today, rather than implying that a module move is a production
validation result.

| Boundary | Current evidence | Status / remaining gap |
|---|---|---|
|`production-run` CLI|`cli.cmd_production_run` delegates to `commands.run`; the handler builds only reviewed V1 adapters|IMPLEMENTED/OFFLINE_VERIFIED; live collection remains blocked at delivery verification|
|`task-collect` CLI|`cli.cmd_task_collect` remains a compatibility dispatcher to `commands.task_collection`; `collection.task.run_task` remains the explicit legacy/monkeypatch facade|IMPLEMENTED/OFFLINE_VERIFIED in this worktree|
|Other legacy CLI commands|`cli.py` remains about 1,900 lines and still contains `collect`, `batch-collect`, `stable-research`, translation, QA, and export orchestration|PARTIAL; no claim that the CLI is globally thin|
|Reviewed task plan validation|`orchestration.plan` owns source snapshot, URL/role, pagination, quota and scheduler-contract checks; `collection.task.validate_task_plan` re-exports it|IMPLEMENTED/OFFLINE_VERIFIED; validation remains entirely offline|
|Task scheduler and worker lifecycle|`orchestration.scheduler` owns serial/three-slot scheduling, cooldown, retry, shared StopAll and resume dispatch; `orchestration.worker` owns one-category serial ranking/detail collection|IMPLEMENTED/OFFLINE_VERIFIED; `collection.task` preserves the old worker monkeypatch seam without owning lifecycle logic|
|Task state and checkpoints|`orchestration.state` owns run/category state, claims, pending details and completed-source resume semantics; `orchestration.checkpoint` adapts the existing `runtime_state.VersionedCheckpointStore`|IMPLEMENTED/OFFLINE_VERIFIED; existing checkpoint filenames and legacy-state migration are preserved|
|Task manifests and summaries|`orchestration.manifest` owns deduplicated ranking/detail outputs and category summaries; scheduler owns only final completion-gate decision/report assembly|IMPLEMENTED/OFFLINE_VERIFIED; no raw evidence or Excel ownership moved|
|Production V1 orchestration|`TaskConfig`, `ProductionRun`, `ProductionWorkflow`, and JSON history repository are distinct modules with offline initial/incremental/resume tests|IMPLEMENTED/OFFLINE_VERIFIED; real Amazon source and real provider evidence are still required|

This extraction is a behavior-preserving offline refactor, not live collection
evidence. Any next legacy-command adapter must preserve the current scheduler
semantics, checkpoint compatibility, and old CLI monkeypatch seam; it is not
justified as a broad rewrite while live source evidence is blocked.

## Controlled V1 operator boundary

`production-run` is the only Production V1 orchestrator.  A source-only
invocation remains DRAFT and cannot emit a bilingual READY export:

```text
amazon-es --offline production-run --run-dir RUN --run-id ID --config TASK.json --profile source-only
```

Live collection remains off by default.  It requires both a reviewed task JSON
with `network_mode: "live"` and `live_transport.enabled: true`, plus an
explicit `--allow-live-transport`.  The CLI then verifies the frozen reviewed
plan hash, calls the existing Scope/Access Gate, and uses only the existing V1
`BrowserSession` / Spain-delivery / snapshot / serial-detail adapters.  It
does not construct V2 canary parsing, proxy rotation, CAPTCHA handling, or an
unreviewed source route.

The reviewed 5,500 plan is
`configs/tasks/amazon_es_bestseller_5500_202610_plan.json`.  Its derivation
record freezes the parent-plan and category-tree hashes, 15 categories, 55
primary + 31 reserve URLs, and two pages per URL.  It changes only the
per-category unique-ASIN quotas from the reviewed 5,000 plan to a 5,500 total.
It is a plan, not evidence that 5,500 products were collected.

The checked-in diagnostic sample
`configs/tasks/amazon_es_bestseller_5500_202610_sample.json` names one
existing reviewed primary URL, preserves its two-page rule, caps collection at
two ranking requests and five selected detail ASINs, and uses the source-only
profile. The raw ranking snapshot is retained, while downstream detail/master
processing is limited to those five ASINs. Its `sample.diagnostic` marker
means its artifacts are never evidence that the 5,500-SKU task is complete or
releaseable.

Before any Qwen call, finish a source-only run and create a small, reviewed
batch bound to its immutable `artifacts/spanish-master.json`:

```text
amazon-es production-translation-selection --master-artifact RUN/artifacts/spanish-master.json --asins selected-asins.json --out selection.json
```

The manifest records the exact parent artifact hash and contains a unique
sorted ASIN set of at most 1,500.  Configure its path as
`translation.selection_manifest`, then continue the same run with
`--resume --from-stage translation-input`.  Selection is fingerprinted from
translation onward but deliberately excluded from earlier source-stage
fingerprints, so this continuation does not recollect saved evidence.  The
full Spanish master remains a source-only DRAFT; formal Spanish/Chinese output
can contain only the selected subset.

Qwen construction additionally requires `--allow-qwen-translation`,
`translation.enabled: true`, an approved DashScope HTTPS endpoint, credentials
already available in the environment, and a current official CNY price-card
record.  Calls pass only through `BudgetedProvider`, whose durable ledger
enforces 5 CNY total and 1,500 unique ASINs including repair retries.  This
worktree performed no Amazon or Qwen request.

For the default Beijing endpoint (`dashscope.aliyuncs.com`), the operator must
record the account's CNY billing confirmation, endpoint host, official-price
page SHA-256, verification timestamp, and the current official model rates
before enabling Qwen.  The parent review on 2026-10-05 identified the official
Qwen-MT Flash CNY page as
`https://help.aliyun.com/zh/model-studio/qwen-mt-flash`, with Beijing input
0.700 and output 1.950 CNY per million tokens; that read-only lookup is not a
substitute for a source-byte hash and account-currency confirmation.  The
runtime rejects either omission, refuses non-CNY billing, caps input/output at
8,192 tokens each within the 16,384-token context, and treats
`finish_reason=length` as an incomplete failed field.  No live price-card is
committed here, so Qwen remains disabled for actual calls.

The repair stage reruns canonical QA and never copies the failed candidate. A
dictionary rerender failure remains manual; provider-retry-eligible fields
receive at most two newly budgeted provider attempts, then unresolved fields
remain manual. Re-QA runs after every accepted candidate.

## Older branch reports: do not merge wholesale

`4698957` changed only `translation/production.py` (+4) and
`tests/test_translation_production.py` (+20): its namespace-isolation test is
conceptually still useful, but this branch's translation state/release
contracts supersede any old report wording. Keep it as a targeted test review,
not a merge/rebase candidate.

`8c302c58` changed only root `canary.py` (+4/-2) and `tests/test_canary.py`
(+4/-1), accepting documented Bestseller URL forms. Its URL acceptance is
represented by the V2 offline canary's scoped Amazon.es validation; its old
live/root-canary architecture is not imported. Keep the old branch and report;
do not delete it or close related review work merely because this matrix exists.
