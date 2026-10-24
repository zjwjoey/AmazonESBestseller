# Exact typed facts and explicit capacity contexts

Follow-up to merged PR #27. This change compares source facts for audit only;
it never writes converted, selected or inferred values into a source record.

## Admission rules

- Identical counts with explicit count labels, exact mass/unit equivalents,
  and exact ordered dimension vectors may carry non-blocking
  `EQUIVALENT_SOURCE_VALUES`. Circular/redonda/redondo form a narrow shape
  alias class. Both literal rows remain, including any additional mass.
- Unknown labels, incomplete vectors, different ordered values, differing
  masses, and ambiguous decimal/thousands punctuation still require review.
  There is no tolerance-based dimension rounding and no axis reordering.
- Volume per minute/second/hour is a rate, not a scalar vessel capacity.
- Explicit compatible-bag sizes and graduation marks are separate assertions.
  Literal upper/lower containers, filtered water, a jug and explicitly sized
  component bowls can be compared within their own stated roles. An unassigned
  value restores the conservative combined comparison.
- A stated range remains an interval in the audit. A scalar outside it or
  inconsistent values inside the same object role remain blocked. No point
  value is invented from the interval. Unqualified hyphen notation such as
  `24-946 ml` is not assumed to be an interval; it may encode quantity and capacity.

## Saved evidence and validation

The 1,343 remaining target pages passed their recorded SHA-256 checks. The 235
exact typed-equivalence groups bind to previously verified actual DOM cells;
both source values and their original positions remain available. Eighteen
saved-source fixture cases include a heat gun's flow rates and actual
container/range assertions. Synthetic negatives retain unexplained scalar
conflicts, wrong masses, incomplete dimensions and ambiguous number formats.

The 52 added regressions and related tests total 205 passes, no failures or
skips. Full 5,412-record replay preserves every ASIN and source text. It routes
4,295 products as Pre-Clean usable and 1,117 for review, clearing 226 of the
previous 1,343. Field slots are 51,590 retained, 1,164 under review and 6,778
originally absent. None of the previously clear fields acquired a blocker.
All 305 legacy source/business holds remain in the derived screening view.

Remaining reasons overlap: 716 products have different literal attribute
values/meanings, 296 need a category-source business choice, 120 have unresolved
capacity/object context, nine retain page anomalies, and one retains an
identity token in description. These are not additive error counts or proof
that a page is wrong. They are preserved for field-specific decisions.

Only independent derived reports/lists are written. Original collection data,
HTML, checkpoints, notes, local edits, old reports and translation caches remain
unchanged. No collection, translation job or production deployment is started.
Pre-Clean admission is not final source QA, translation or production acceptance.
