# Source Audit V1 → Translation Preparation V1

Independent offline package: `amazon_es_bestseller.source_audit`. It does not
start a browser, collector, translator, worker, scheduler or API client. It
never writes the original selection, plan, checkpoint, HTML, receipt, cache,
database, workbook, Access Gate or main branch.

## Development baseline and isolation

- Development branch: `feature/source-audit-translation-prep-v1`.
- Isolated managed worktree:
  `C:/Users/Administrator/.codex/worktrees/source-audit-translation-prep-v1/AmazonESBestseller`.
- Base commit: `6b14f4aee070cd80411715015bbc5dedcf6c807f`, from the actual
  `codex/quick-selection-detail-5500-v0` branch, checked against `git ls-remote`.
- Producer launch/auth evidence currently records
  `0696ffc5bd9909fbc0339b21678592e438f24a13`; its checkout is later `6b14f4a`
  with uncommitted collector changes. The launch SHA is NOT proof of current
  loaded Python bytes. No dirty producer code is copied or changed here.
- Main is separately dirty and older than remote main. No checkout, reset,
  cherry-pick or merge into either main or the producer is performed.
- At initial observation worker_status was STOPPED_REVIEW_REQUIRED after
  Spain postcode initialization failed, with 0 successes / 1 identity review.
  This is historical observation, not an instruction to resume or a claim about
  the future collector state. This task never stops/restarts that producer.

## PowerShell usage

Run from this isolated worktree (Python 3.11/3.12). Install the repository's
existing dependencies if needed; no new network/runtime dependency was added.
Use a **short output root on Windows**, e.g. the F-drive root below, because
legacy immutable publication uses temporary suffixes and long-path Windows
support is not guaranteed. All output directories must be new, or an exact
idempotent rerun of the same code/input. Never use an input subdirectory.

```powershell
$env:PYTHONPATH = 'src;.'
$env:RUN_LIVE = ''
$py = 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
$outRoot = 'F:\AmazonESBestseller\outputs\source_audit_v1_20261016'

# Take a consistent COPY. This is not collector freeze/lock authorization.
& $py -m amazon_es_bestseller.source_audit.cli snapshot --input '<Detail5500 delivery directory>' --out "$outRoot\frozen_copy"
& $py -m amazon_es_bestseller.source_audit.cli inspect --input "$outRoot\frozen_copy" --out "$outRoot\inspection"
& $py -m amazon_es_bestseller.source_audit.cli audit --input "$outRoot\frozen_copy" --out "$outRoot\audit" --expected-count 5500
& $py -m amazon_es_bestseller.source_audit.cli repair-plan --audit "$outRoot\audit" --out "$outRoot\repair_plan"
& $py -m amazon_es_bestseller.source_audit.cli repair-copy --audit "$outRoot\audit" --out "$outRoot\derived_repair_copy"
& $py -m amazon_es_bestseller.source_audit.cli reaudit-repair --repair "$outRoot\derived_repair_copy" --out "$outRoot\derived_reaudit"
& $py -m amazon_es_bestseller.source_audit.cli prepare-translation --audit "$outRoot\derived_reaudit" --out "$outRoot\derived_translation_preparation"
& $py -m amazon_es_bestseller.source_audit.cli prepare-translation --audit "$outRoot\audit" --out "$outRoot\translation_preparation"
& $py -m amazon_es_bestseller.source_audit.cli report --audit "$outRoot\audit"
```

These are usage templates, not claims that formal 5,500 audit has executed.
Preparation consumes audited READY fields, not an unreviewed repaired
copy. `repair-copy` outputs NOT_REAUDITED independent derived records and a
per-field application ledger. It does not auto-approve repairs. Brand, selected
variation and parent relation differences remain manual review, even when a
reparse candidate exists. All source facts remain accessible.

`reaudit-repair` (rule source-audit-v1.2) verifies the original audit and frozen
snapshot, reconstructs the exact allowed patch set, checks every ledger and
derived record, then replays derived values against the original HTML. Original
checkpoints, parsed_detail and exports are still compared against the ORIGINAL
record; they are never rewritten to make a repair pass. Its output is a new
source_audit bundle accepted by prepare-translation, with repair/original audit/
snapshot hashes. The repair chain is revalidated again before preparation.
Unapproved manual values stay held. Nested repair chains are explicitly rejected.

Version 1.2 checks all declared exports and parsed detail files, display URL
identity, variation metadata, normalized numbers against HTML-derived raw evidence,
and unambiguous same-dimension capacity/weight/power/voltage conflicts. Multi-values
are not guessed. Business values requiring review remain in raw_record plus
business_field_statuses but are omitted from trusted passthrough_fields.
All text units are rebound to source fields/positions or verified derived evidence,
with regenerated IDs, semantic keys and protection terms. Old-rule audits require
re-audit; unmodified frozen snapshots remain usable. This is preparation, not
authorization to translate or apply data to production.

To replay the checked-in saved real HTML without Amazon:

```powershell
& $py tools/source_audit_saved_sample.py --out "$outRoot\historical_input"
& $py -m amazon_es_bestseller.source_audit.cli snapshot --input "$outRoot\historical_input" --out "$outRoot\historical_snapshot" --data-kind HISTORICAL_REAL
& $py -m amazon_es_bestseller.source_audit.cli audit --input "$outRoot\historical_snapshot" --out "$outRoot\historical_audit" --expected-count 1
& $py -m amazon_es_bestseller.source_audit.cli repair-plan --audit "$outRoot\historical_audit" --out "$outRoot\historical_repair_plan"
& $py -m amazon_es_bestseller.source_audit.cli prepare-translation --audit "$outRoot\historical_audit" --out "$outRoot\historical_translation_preparation"
```

The sample HTML is real archived Amazon evidence, SHA256
`1bec15aee1623a960289bef61bd253ca0574f3c7eb0cf9eca03971999238c557`.
The offline receipt envelope is explicitly **synthetic**, not a newly paid or
historical verified network request receipt. Its original provenance is copied.
Snapshot type HISTORICAL_REAL must not be misrepresented as a formal producer
delivery. Tests additionally use SYNTHETIC_TEST for edge cases.

## Input compatibility and trust boundaries

The adapter discovers actual Detail5500 files: selected_5500.json,
detail_plan_5500.json, sealed tasks/*/checkpoint.json, parsed_detail.json,
response metadata, HTML, receipts, details_5500_raw.jsonl, details_5500.csv,
source_input_certificate.json and optional detail_run_manifest.json plus
FINAL_acceptance.json. It also accepts details.jsonl/details.json/details.csv
historical rows with explicit candidate/selection/request/evidence identity.
Bare ASIN-only or title-only spreadsheets cannot become verified source input.
CSV values use the existing exporter serialization contract; type formatting
does not create false CSV-versus-checkpoint mismatches.

`snapshot` inventories allowlisted data, copies referenced saved HTML/metadata,
captures the referenced source manifest, then rechecks inventory and all hashes.
Concurrent mutation fails closed before manifest publication. This is
CONSISTENT_HASH_VERIFIED_COPY_NONATOMIC, not an OS atomic freeze. Afterward all
audits read ONLY the copied manifest-bound evidence; original absolute paths
resolve through frozen aliases, never back into active files. Missing external
evidence remains unverified. No database is opened.

Every successful candidate requires exact task/selection/request/final URL,
HTML, sealed receipt/plan and active-ASIN replay. Reused cache requires exact
metadata/hash and the same offline parser. The legacy general identity gate
is adapted only AFTER strict bridge identity succeeds; parent-family matching
cannot authorize a changed active child. Section-local raw attribute positions
are preserved and checked without applying the legacy global-position assumption.

## Reports, repair and preparation

- source_audit_summary.json: original collection states versus valid details,
  identity passes, product readiness, manual review and followup suggestions.
- source_audit_issues.csv/.jsonl: candidate/selection/ASIN, field, severity,
  current value, evidence, issue/status/reason, repair eligibility and review.
- source_field_coverage.csv and identity_review_queue.csv.
- source_repair_plan.json and source_repair_candidates.jsonl.
- source_repaired_products.jsonl and source_repair_application_ledger.jsonl:
  independent optional new version; originals never edited.
- translation_ready_products.jsonl: full raw record and passthrough business
  facts retained; not just the reduced model text.
- translation_units.jsonl, translation_preparation_summary.json,
  translation_contract_validation.json, glossary_validation.json,
  translation_field_contract.md and translation_preparation_manifest.json.

Each manifest binds code SHA, actual working code hashes, input manifest,
source evidence and output hashes. Only identical reruns can share an output
directory. Repairs are separate proposals and never increase READY silently.

Read [translation_field_contract.md](translation_field_contract.md) before
building a consumer. Existing flat category dictionary matches are hints only;
label/value identity, parent category path, source hash and glossary content
are included in semantic reuse. Legacy cache loaders are never instantiated;
an optional copied, independently reviewed semantic cache has an explicit
strict protocol. Historical caches require a separately audited adapter.

## Known limits and formal handoff

This is not final 5,500-detail production acceptance. A synthetic 5,500-pending
test proves task-volume accounting, not 5,500 real HTML success. Formal flag
requires a DETAIL5500 snapshot, all 5,500 active identities, consistent selection
and plan, plus a hash-verified producer FINAL acceptance. Missing optional
descriptions may be PARTIAL_READY; identity failures cannot. Numeric price
warnings do not block valid independent translation text. Heuristic content
warnings are evidence for review, not a claim of human-quality linguistic review.
No new parser is duplicated, no dictionary mass rewrite, no translation performed.

The original collection conversation must confirm producer completion/freeze,
review this branch in a separate environment, run the audit on a frozen COPY,
resolve source problems, then discuss translation with the user. Do not merge
main or inject this package into a running collector automatically. User-facing
acceptance and handoff reports go under
`F:/AmazonESBestseller/交付文件/20261016/<new-task-directory>`; machine artifacts
stay in their run directories and are referenced or copied, never moved.

## Evidence-binding hardening (rule version source-audit-v1.1)

When response metadata is referenced, its byte hash, requested URL, final URL,
status code and access state must agree with the sealed receipt. Formal
DETAIL5500 successful live records require this metadata; historical replay
envelopes remain explicitly non-production. Producer FINAL evidence must bind
every planned checkpoint hash, exact success count, run ID and stage, not just
a Boolean passed flag. JSON/JSONL/CSV fallback input freezes its external
HTML/receipt references as well as per-task checkpoint input. Every derived
command rejects output directories overlapping the underlying source snapshot,
not merely the audit-report folder. Old-rule audit reports require re-audit
before preparation or repair; frozen source copies themselves remain reusable.
