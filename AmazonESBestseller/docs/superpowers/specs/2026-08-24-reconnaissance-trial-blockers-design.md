# Reconnaissance Trial Blockers Design

## Goal

Make the bounded Amazon.es reconnaissance trial safe to run and keep its GO/NO-GO conclusion grounded in normal, category-specific evidence.

## Scope

This change fixes only the five pre-trial blockers identified in review:

1. Stop on same-host login, redirect, or page-identity mismatches.
2. Require evidence from three distinct category trials before returning `GO`.
3. Exclude non-`NORMAL` detail pages from detail-field availability statistics.
4. Discover third-level nodes from already-saved, normal second-level pages and preserve their hierarchy.
5. Eliminate duplicate card records and calculate duplicate rate from records with an ASIN only.

It does not add requests, retries, concurrency, proxying, bypass behavior, expanded category limits, or deeper structured-data enrichment.

## Design

### Access identity

Each probe will compare the requested path with the final path and classify an unexpected same-host destination as `UNKNOWN`. Known login paths and Spanish/English sign-in markers will classify as `BLOCKED`. A non-normal result already halts the active batch and prevents later stages.

### Evidence gate

`GO` will require three normal root probes, three normal and distinct category probes, and at least one parsed record with both ASIN and rank from each category URL. The existing ASIN and rank availability thresholds remain. Any weaker result is `CONDITIONAL GO`; a failed root stage remains `NO-GO`.

### Detail evidence

Detail availability will be calculated only from normal detail events. Restricted pages remain in `access_events.csv` and the report's restricted-sample count, but are not a field-statistics numerator or denominator.

### Category hierarchy

The kitchen root remains the source of depth-2 nodes. Saved normal depth-2 pages are parsed offline for direct child links; those children are written as depth 3 with the selected depth-2 node as parent. Ancestor/root links and pagination are excluded. No third-level URL is visited.

### Record integrity

Card candidates will be reduced to non-nested containers before parsing, with a final per-page identity guard. Duplicate summary will use only records with an ASIN for duplicate counts and rate, while retaining the total ranking-record count for transparency.

## Acceptance criteria

- Kitchen-to-home or kitchen-to-sign-in redirects stop without category visits.
- Three normal category events with only root records never return `GO`.
- A blocked detail HTML sample cannot contribute to `detail_field_availability.csv`.
- A saved depth-2 page containing a child link produces a depth-3 tree row with the correct parent.
- Nested card fixtures produce one ranking record, and null-ASIN records do not inflate duplicate rate.
- All tests, compilation, and diff checks pass before commit.
