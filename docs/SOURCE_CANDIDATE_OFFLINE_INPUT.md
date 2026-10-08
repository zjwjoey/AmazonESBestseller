# Offline source candidate input

An existing Production V1 task can import a candidate produced by
`write_spanish_source_candidate()` through its normal `normalize` stage:

```json
{
  "task_id": "offline-source-candidate",
  "network_mode": "offline",
  "source": {
    "ranking_evidence": "ranking.json",
    "detail_html_dirs": ["saved_detail_html"],
    "source_candidate_manifest": "reviewed_candidate/manifest.json"
  },
  "translation": {"provider_mode": "fake"}
}
```

Paths are relative to the task configuration directory. Existing offline
ranking/detail evidence remains required. Use the existing command:

```powershell
python -m amazon_es_bestseller --offline production-run --config task.json --run-dir candidate_run --run-id candidate_run --profile source-only
```

The configuration fingerprints the candidate manifest. The loader verifies its
schema, master file hash, canonical record hash, exact candidate ASIN scope and
membership in the task's ranking evidence. A candidate with owner attribute
exclusions must provide the producer-written independent parent artifact; its
file/canonical hashes, exact scope and record/attribute/locator bindings are
checked. Inline parent records are not a configuration input.

`normalize` uses the existing eligible attribute view while retaining raw
Spanish evidence and exclusion policy. The original candidate SourceGate must
be verified and ready; a blocked builder/current gate cannot enter through
this option. `source-audit` then recomputes the current SourceGate, and the
verified parent reference flows through Spanish Master and translation input
to formal binder, execution and overlay validation.

This option is offline only and does not grant release or Excel approval.
The source-only example performs no provider requests. Formal structured
translation is exercised in tests with an injected offline fixture provider;
the example's fake provider is not a formal provider. Tasks without this option
continue to normalize their existing ranking/detail evidence.

## Reviewed L3 derivation

`production.category_l3_derivation` supports the subsequent offline derived
L3 repair. `load_reviewed_l3_artifact` requires the reviewed file SHA256 and
verifies its original master, manifest, saved details, proposals and support
files. `rebind_l3_decisions` checks the original record binding, current
title/L1/L2, same-ASIN detail identity and exact breadcrumb index 2. It emits a
new parent binding and retains conflicts, missing evidence and nonempty L3.

`apply_l3_decisions` changes only `category_l3`, with old/proposed/evidence and
parent-to-derived hashes in a separate ledger. `build_derived_l3_candidate`
re-audits the eligible records through the existing source audit, builder
decision and approved attribute-exclusion helpers. Its gate is recomputed;
the raw parent authority and raw audit remain bound to the original evidence.
Write the result to a new directory using `write_spanish_source_candidate`.
The derived manifest is then consumed by the same offline task input above.
Translation input recomputes record and L3 field hashes from the new facts;
existing translation/TM candidates need revalidation before use.
