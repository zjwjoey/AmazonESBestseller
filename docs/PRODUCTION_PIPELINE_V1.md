# Production Pipeline V1 — factual DoD matrix

Last evidence review: 2026-10-05. This is a code-and-test inventory, not a
claim that a live production run occurred. **Overall: NOT_READY_FOR_MERGE.** A
fresh local full offline pytest run ended with exit code 0 on 2026-10-05. This
does not replace separately approved live Amazon and Provider evidence.

Status vocabulary: `IMPLEMENTED` = code exists; `OFFLINE_VERIFIED` = focused
tests passed in this worktree; `LIVE_NOT_EXECUTED` = no Amazon/provider call
was made for this release; `PENDING` = integration/evidence still required;
`BLOCKED` = a release prerequisite is not yet satisfied.

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
|64|AccessGate challenge StopAll|access tests|IMPLEMENTED/OFFLINE_VERIFIED|
|65|no proxy/CAPTCHA/stealth bypass|AGENTS + code policy|IMPLEMENTED/OFFLINE_VERIFIED|
|66|no live Amazon in this release|run evidence|LIVE_NOT_EXECUTED|
|67|no Provider API in this release|run evidence|LIVE_NOT_EXECUTED|
|68|15 reviewed source categories unchanged|existing configs; no new sources|IMPLEMENTED; not re-run|
|69|5500 collection not performed|no manifest/run evidence|LIVE_NOT_EXECUTED|
|70|CI offline matrix declared 3.10/11/12|`.github/workflows/ci.yml`|IMPLEMENTED; remote not verified|
|71|merge decision|this matrix + final integration|**NOT_READY_FOR_MERGE**|

Historical evidence (August 200-SKU and 496-SKU material) remains historical
evidence only; it does not prove this October V1 release. The old source-plan
count remains 15 categories. No unreviewed source was added.

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
