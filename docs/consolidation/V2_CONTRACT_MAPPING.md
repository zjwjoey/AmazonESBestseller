# Main Consolidation V2 — Data Handoff Contract

## Purpose

V2 connects existing mainline records with the candidate, detail, audit and
translation stages. It reuses `models.py`, `identity.py`, ranking snapshots,
detail checkpoints and the Translation V2 production contract. This contract
does not create another product identity system.

## Candidate observation handoff

`handoff.CandidateObservation.from_ranking_record` adapts one immutable ranking
row. Its `candidate_id` groups an exact normalized product URL when the
mainline row already carries one. A separate `candidate_observation_id`
identifies the snapshot/source/page/slot/rank/raw URL appearance. If normalized
URL evidence is missing, the candidate ID falls back to that source-bound
observation key so uncertain links are not collapsed. A candidate ID is not an
ASIN and does not assert that the link is an independently buyable SKU. The
complete source ranking row is retained in `source_record` and copied into
`to_dict()` before explicit `candidate_*` fields are added.

| Handoff field | Mainline source | Meaning |
|---|---|---|
| `candidate_id` | exact normalized product URL, otherwise observation locator | URL-group key; never a confirmed product identity |
| `candidate_observation_id` | snapshot/source/page/slot/rank/raw URL | Stable key for one source-bound appearance |
| `candidate_original_product_url` | `ranking_product_url_raw` | Original link evidence |
| `candidate_normalized_product_url` | `ranking_product_url_normalized` | Existing normalized URL, if present |
| `candidate_source_type` | `ranking_source_type` | Ranking source/list kind |
| `candidate_source_page_url` | `ranking_source_url` | Source page, not the product URL |
| `candidate_snapshot_id`, `candidate_observed_at` | snapshot and observation fields | Evidence time boundary |
| `candidate_ranking_position*` | ranking position and raw value | Source ranking observation |
| `candidate_asin_observed` | `ranking_asin` then `asin` | Observed hint only; not confirmation |
| `candidate_identity_status` | explicit conservative default | Remains `UNCONFIRMED` until detail identity resolution |
| `candidate_link_identity_status` | `ranking_link_identity_status` | Link-to-ranking ASIN comparison only |
| `candidate_evidence_file` | evidence path field when supplied | Existing file reference; never fabricated |

The detail stage continues to use `identity.resolve_identity` and the formal
`detail-plan` → `detail-run` → `detail_executor` path. Requested/final URLs,
resolved and parent ASINs, variation evidence, checkpoint state and saved HTML
remain detail evidence. Parent ASIN alone never authorizes child-SKU merging.

The offline `candidate-snapshot` entry accepts checksummed saved HTML and
reuses `monitoring.ranking_identity.adapters` for ranked sources plus the
extracted V4 Customer Favorites DOM adapter. It emits an append-only
`candidate_snapshots/<snapshot_id>/` artifact with the original HTML, grouped
candidate records, every source observation, and a manifest. The artifact is
not a second task database: it does not create request state, checkpoints, or
an authoritative pointer. Source completeness remains `UNVERIFIED` until a
separate evidence review establishes it, and the artifact cannot be passed to
`detail-plan` as READY.

Rank positions carry source semantics. Only `BEST_SELLERS` populates
`bestseller_rank`; `NEW_RELEASES`, `MOVERS_SHAKERS`, `MOST_WISHED_FOR`, and
`MOST_GIFTED` retain their own list position and semantics. Customer Favorites
records do not acquire a synthetic ranking position. Candidate link
normalization keeps unknown query/path/variant bytes and does not confirm a
buyable SKU.

## Downstream contracts reused

- Source audit consumes explicit detail/source evidence, identity status,
  parser version and checkpoint references. Its adapter must remain read-only;
  missing provenance stays unverified.
- Translation uses `translation.production_contract`: aliases normalize at the
  boundary, raw Spanish is retained, candidate hashes bind source content,
  schema and field identity, and human notes remain source-row data.
- QA uses the existing quality result models and Field Closure gates.
- Excel export remains responsible for formatting the frozen Spanish/Chinese
  workbook contract; it does not infer product facts.

## Compatibility and limits

The adapter preserves unknown source fields and does not mutate the supplied
mapping. It does not make incomplete ranking sources complete, confirm ASIN
identity, authorize detail requests, or claim that a candidate is a buyable
product. Snapshot authority/completeness and detail access gates continue to be
enforced by their existing owners.
