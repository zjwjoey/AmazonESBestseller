# Complex-capacity follow-up, 2026-10-25

Continue the exact 816-product / 840-field held queue from the preceding
Pre-Clean review. Collection, production deployment and translation remain out
of scope. Changes operate on the existing derived batch, not collection state.

## Corrected behavior

- Bind water/milk and clean/dirty-water tanks, explicitly named ordinal/spatial
  compartments and interchangeable containers to their own literal volumes.
  A complete repetition can reuse those proven parts; unknown extra amounts
  cannot inherit an earlier role. A drawer's unnamed alternative is accepted
  only with the complete two-drawer comparison and separately stated cohort.
- Validate count/component/total relationships with exact Decimal arithmetic
  and occurrence coverage. Distinguish per-item nominal capacity from an
  explicitly declared aggregate `total`. Unknown quantities cannot erase an
  independently proven positive subtotal that already exceeds the total.
  Never invent a missing total or borrow a count from a different noun.
- Preserve compact `3x320ml` and `3X320ml` volume tokens. Zero, leading-zero
  or fractional multipliers are held instead of silently losing the operand
  or borrowing the decimal suffix as an integer count.
- Prove explicit included sets and named contents, with package-count closure
  where supplied. Non-volume tableware pieces retain their stated counts
  without receiving invented volumes. Preserve raw `cc` while recognizing its
  exact millilitre equivalent for numeric checks.
- Keep explicit performance tests, cup portions, dilution tables, cartridge
  throughput, other-product comparisons, separately sold refills and recipe
  doses distinct from the vessel's nominal capacity. Every excluded occurrence
  carries its literal context. Known single-dose/serving bounds still apply.
- A narrowly formatted source title may confirm a complete two-part restatement
  in the body. The proof binds the exact source-title text and hash, and rejects
  extra quantities, conflicting title values, negated components and explicit
  same-container counterclaims. Both source fields remain unchanged.

These checks are not ASIN allowlists. Unsupported wording remains held. The
collector's raw attributes, product identity, raw Spanish, human notes, caches,
checkpoints and the frozen export contract are unchanged.

## Evidence and replay

All 816 held products' saved detail-page SHA-256 hashes were reverified. The
65 complex-capacity fields received individual source review; 60 had previously
been classified as possible rule fixes. The final rules resolve 59 of those
60 candidates. The remaining bag-count case does not explicitly bind the stated
two compartments to two bags, so the code does not infer that missing count.

Offline replay retains all 5,412 products:

| Outcome | Previous | Current |
| --- | ---: | ---: |
| Pre-Clean admitted | 4,596 | 4,646 |
| Held products | 816 | 766 |
| Held fields | 840 | 782 |

Fifty products are newly admitted. Fifty-nine original held fields resolve;
one previously unflagged field is correctly held. No previously admitted SKU
becomes held after correcting integration regressions. The additional field
belongs to a product already in the held queue: B01LYYYK65's saved page states
`8x100 ml` and `Total 768 ml`. Neither number is guessed or repaired.

Five known source-risk cases remain held: non-exact 3 × 933 ml versus 2,800 ml,
servings exceeding a 1,700 ml nominal volume, two 700 + 310 ml versus 1 litre
cases, and conflicting package count/size assertions. Nine prior source-unit
or placeholder anomalies also retain their independent holds.

## Validation boundary

Real saved-source fixtures reproduce each accepted rule. Negative tests cover
unknown/repeated amounts, wrong counts, invalid totals, negated or hypothetical
included sets, excessive doses and title/body conflicts. The batch was replayed
without any network transport or translation provider.

The exact current commit's targeted test and CI receipts determine code
acceptance; the prior e556cdc2 CI success is not reused for this follow-up.
Pre-Clean admission is not translated output, release-gate approval, merger to
main or production acceptance. Live data and the production checkout are not
updated by this review.
