# Incremental Refresh — planned, not yet a production claim

Status: **OFFLINE_VERIFIED / LIVE_NOT_EXECUTED** as of 2026-10-05.

The production workflow now executes Initial and Incremental snapshot changes
from saved evidence, preserving human notes by ASIN and emitting `NEW`,
`REMOVED`, `RANK_UP`, `RANK_DOWN`, `UNCHANGED`, `CONTEXT_ADDED`, and
`CONTEXT_REMOVED`. It reparses old detail schemas from saved HTML before any
fetch and fails closed on an offline network action. It has no evidence of a
complete live refresh through Amazon collection, Provider 0 translation,
Chinese QA, ReleaseGate, and frozen workbook export.

Required future flow:

1. Read immutable ranking/detail evidence and validate schema/parser/source
   hashes before accepting a resume.
2. Reuse unchanged ASIN evidence only when the raw-record binding still
   matches; never treat title or row order as identity.
3. Reparse saved HTML before any new request for stale detail schema records;
   a live fetch requires an explicit reviewed V1 detail transport.
4. Send changed translated fields through hash/schema/dictionary-version
   isolated cache/TM lookup; do not use a pre-version cache hit as READY.
5. Run affected-field rerender then field-level Chinese QA. Pending selective
   repair keeps the SKU out of READY.
6. Rebuild source audit, Spanish Master, stage evidence and ReleaseGate.
7. Export only through formal `export_ready`; preserve human `notes` exactly.

It must remain conservative: no unreviewed source expansion (the reviewed plan
remains 15 categories), no proxy/CAPTCHA/stealth bypass, and AccessGate
challenge/403/429/Robot Check stops new work. Future requested scale is at
most 1,500 unique SKU and translation spend at most CNY 5 including retries;
actual provider pricing and run evidence are still required before that limit
can be asserted as enforced production behavior. The 1,500-ASIN cap applies
to the future Qwen translation scope, not to the separate collection task.
