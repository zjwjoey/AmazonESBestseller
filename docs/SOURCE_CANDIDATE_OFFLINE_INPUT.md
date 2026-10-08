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
