# Follow-up to the 5,412-record offline field review

Base: `6c1cf342` (main). All evidence came from previously saved pages. No
Amazon requests or translation-provider calls were made.

## Findings

- 296 category findings reconcile to 85 hash-verified ranking pages and the
  corresponding detail breadcrumbs. The ranking department and detail
  navigation really differ. Preserve both paths and retain provenance review;
  do not splice a detail branch into the ranking category or claim a new PASS.
- All 114 invalid original prices reproduce an active parser bug: an
  `#apex_price` inside another variation's swatch was admitted as the current
  product's original price. After excluding variation-owned price candidates,
  the same 114 pages preserve their current prices and expose no eligible
  original price. Original price and discount therefore remain absent.
- Four products explicitly expose length units under a capacity label; five
  expose suspected `1 x 1 x 1` placeholder dimensions. The source anomalies
  remain in ordered raw attributes and full detail text. Spanish core-spec
  derivation previously promoted them and now rejects those candidates.

The unit filter also covers legacy details dictionaries. Generic capacity with
explicit mass (for example coffee-bean capacity) remains valid source evidence;
it is not converted into liquid volume. Real dimensions such as `1 x 1 x 10 cm`
remain eligible. A numeric selected value such as `142` uses an exact `142 g`
title span when available, rather than inventing a unit or hiding the title
measure. Numeric selected sizes without better title evidence remain intact.

## Evidence and regression

`tests/fixtures/html/remaining_review_20261024/` contains unmodified public price
DOM excerpts from B00006JSUA, exact raw attribute cases for the nine affected
products, and original HTML hashes/extraction provenance. Fixtures do not
depend on production files or browser credentials.

Regression cases cover wrong variation prices both below and above the current
price, preservation of a legitimate main BuyBox strike, missing-main-price
fallbacks, raw immutability, placeholder suffixes, unit validation, valid mass
capacity and legacy input parity. Audit and collector share the same strike
helper. Access Gate, identity resolution, checkpoint semantics, raw evidence,
human notes and the frozen Excel contract are unchanged.

Data corrections are written as independent copies with preimages and source
hashes. Production DetailState/checkpoints and prior workbooks are not rewritten.
Offline review does not constitute live production acceptance. Source unit,
dimension and category decisions remain visible for review.
