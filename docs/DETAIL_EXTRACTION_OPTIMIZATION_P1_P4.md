# Detail extraction optimization P1–P4

## Scope

This change improves offline detail parsing and failure handling. It does not
make Amazon requests, modify collected source files, or start translation.

## Changes

- P1 preserves product breadcrumb candidates and their source links, and records
  BSR-linked category evidence separately. BSR category evidence does not fill
  the product breadcrumb or ranking category.
- P2 treats page-owned ASIN signals as identity evidence. ASINs found in page-wide
  recommendations and links remain diagnostic evidence and cannot create a
  false identity conflict; conflicting page-owned signals remain blocked.
- P3 isolates recognized per-item parser, timeout, and transient network
  failures while retaining checkpoints so later items can proceed. Access
  restrictions still stop the run. Evidence/checkpoint storage failures stop
  the task scheduler without retrying the category; no incomplete final
  manifest is emitted as complete.
- P4 shares one parsed DOM between the base and V2 detail parser.

## Validation

The final scoped regression run passed **131 tests** across detail parsing,
collection, candidate handoff, identity, variation, category provenance,
executor planning, and task scheduling.
The complete offline repository suite also passed: **2,423 tests**.
An offline 44-page saved-HTML comparison produced identical complete record and
normalized-output hashes between the previous three-DOM call path and the new
one-DOM path (`1259d778d3700b83cf3bfaafd9952606e775ac5434082712d09848882c0a5fd6`).

On the same 44 saved pages (124,704,071 input bytes), parse/extract time changed
from 91.169264s to 62.583835s (31.4% lower); P50 changed from 2109.571ms to
1450.459ms and P95 from 2404.497ms to 1638.777ms. Observed RSS growth changed
from 82.60MB to 61.21MB. These are offline measurements, not live collection
throughput guarantees.

GitHub CI is still required before merge.

## Scope limits and remaining work

The observed saved-page gap addressed here is a missing scoped desktop breadcrumb
layout and explicit BSR category-link evidence. Unknown raw product attributes,
feature bullets, description, price, variation, and image extraction remain on
their existing parser paths; this change does not claim to fix every possible
field omission. Missing data in the source page remains missing rather than
being inferred. GitHub CI is still required before merge. This branch has not
been deployed to the production collector.
