# DictionarySync V2

DictionarySync V2 promotes a lexical mapping only when two independent facts
agree. An independent fact is a distinct `(ASIN, source_record_hash)` pair;
multiple reviewer IDs attached to the same saved product evidence do not count
as independent confirmation.

Every promotion QA PASS is bound to `source_hash`, Chinese target,
`field_type`, normalized controlled context, base dictionary version and
translation schema version. A legacy or bare PASS is a review candidate, not a
promotion.

Contexts are mappings using only `category`, `attribute_label`, `product_type`
and `field`. A missing/unknown context becomes a candidate. Promoted keys are
`field_type|context_key|normalized_source`; there is no global fallback for
contextual terms. Distinct legal contexts can therefore coexist, and differing
targets are reported as `CROSS_CONTEXT_AMBIGUITY`.

`sync_evidence` emits one manifest containing `dictionary_version`,
`dictionary_hash`, `previous_hash`, `change_log`, promoted dictionary and
promotion evidence. That manifest is the version passed to `TranslationService`.
Both the per-ASIN field cache key and translation-memory key include the
dictionary version, so a later dictionary cannot consume old cache entries.
Older cache files remain readable but are not a new-version hit.

Use `rerender_affected_fields` for a promotion follow-up. It only considers
explicit evidence ASINs and fields, requires the saved source-record/source
hashes to match, does not mutate Spanish evidence or human notes, and invokes
the supplied QA callback. Only an exact QA-bound PASS produces `READY`; other
affected fields enter `selective_repair` while already-good fields remain
unchanged.
