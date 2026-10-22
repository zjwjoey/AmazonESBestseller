# Detail → source audit → translation → export

All stages below are offline and read-only toward collection inputs. They write
new reports or derived artifacts; they do not update source Master data, saved
HTML, checkpoints, receipts, notes, or an existing translation cache.

## Explicit stage

quality-audit and source-evidence-audit default to source checks.
audit-fields keeps its historical translation/display default; specify
--stage source when calling it immediately after collection.

- source: compare RAW/canonical facts and verify available source structure.
  Missing Chinese output is not a source defect. Core specification is a later
  derived field and is marked not applicable at this stage.
- translation: apply the supplied translation overlay read-only, then check
  Chinese fields and source/target completeness.
- export: perform the display checks; --workbook additionally compares the
  actual workbook. Normal export entry points explicitly use this stage.

Source absence stays a coverage gap, not an inferred parser error. A breadcrumb
must actually contain a level before that level can be required. Numeric facts
are compared against their RAW values, and capacity comparisons include units
and equivalent conversions.

## Scope and results

The eight Quality Gate checks remain independent and share the existing
Field Closure implementation. With --check or --asin, passing the selected
checks gives PARTIAL_REVIEW, not whole-stage RESEARCH_READY.
The report records missing checks and scope completeness. Diagnostic CLI
commands can still return zero when the selected checks pass; exit code alone
is not a production release decision. Consumers must inspect gate.research_ready.
READY for the source stage does not mean translation or export has been accepted.

total_skus is the selected input set; input_skus preserves the full input
count. Explicit SKU counts are separate from legacy issue-row counters.
Field metrics report reviewed, blank and erroneous unique (ASIN, display-column)
cells, both overall and per column, with denominators. An empty/unreviewed set
has no fabricated zero error rate. Multiple explanations do not inflate the
number of erroneous cells.

## Progress and evidence reuse

Console progress is flushed and rate-limited. Optional --progress-json
atomically writes an observer file outside the source directories. It cannot
overwrite an input file. Reports record actual start/end times and monotonic
elapsed time, independently from progress notifications.

One audit invocation shares a saved-HTML directory index and bounded body cache
between replay and Field Closure. Unselected ASIN-named pages are not read.
Arbitrarily named saved pages must still be inspected to determine their ASIN.
The cache does not persist across audit runs, hashes loaded bytes, and refuses
an input change during the audit. It does not claim that a previous run's check
can be reused after a parser/schema change.

The legacy REQUEST_EVIDENCE count means request metadata is present. New
content_hash_verified, verified_request_count and unverified_request_metadata_count
explicitly distinguish actual saved-content verification. The adapter never
creates receipts or a formal Source Audit PASS.

## Launching the intended code

Use scripts/Invoke-PostDetailAudit.ps1 from the checkout you intend to review,
with -Python pointing to the intended Python executable, -Command audit-fields,
--products/--details/--rankings pointing to the read-only input files,
--stage source, --out pointing to a new report and --progress-json pointing
outside the source directories.

The launcher verifies and prints the imported module location and restricts
execution to the three offline audit commands. It does not change the globally
installed Python package or start collection/paid translation.
