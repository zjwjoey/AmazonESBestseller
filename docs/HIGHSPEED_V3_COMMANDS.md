# HighSpeed Snapshot V3 offline workflows and staged preparation

Use Python 3.12 with explicit worktree `PYTHONPATH=src`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONIOENCODING=utf-8`; do not set `RUN_LIVE`. V3 development lives in `codex/highspeed-snapshot-v3`, production main remains untouched.

## Offline command sequence

```powershell
python -m amazon_es_bestseller.cli highspeed baseline-v3 --csv <historical-30026-csv> --candidate-jsonl <original-candidate-jsonl-if-available> --out <new-delivery-parent>
python -m amazon_es_bestseller.cli highspeed replay-v3 --historical-root <original-store> --historical-run-id continue30000_20261013T204238Z --mapping-config <absolute-reviewed-category-config> --out <nonexistent-separate-runtime-directory>
python -m amazon_es_bestseller.cli highspeed history-v3 --historical-root <original-store> --historical-run-id continue30000_20261013T204238Z --root <replay-output>/store --run-id <replay-manifest-run-id> --out <new-runtime-parent>
python -m amazon_es_bestseller.cli highspeed report-v3 --root <replay-output>/store --run-id <replay-manifest-run-id> --history-manifest <sealed-history-manifest> --mapping-config <absolute-reviewed-category-config> --out <new-delivery-parent>
python -m amazon_es_bestseller.cli highspeed prepare-v3 --historical-root <original-store> --historical-run-id continue30000_20261013T204238Z --mapping-config <absolute-reviewed-category-config> --pending-source-pack <new_sources.pending_review.json> --out <new-delivery-parent>
```

These commands never open a browser. `readonly_store` uses an explicit SQLite `mode=ro` connection and does not call `RunStore`'s constructor for historical data. Replay writes one exclusive new SQLite store and immutable page journals; it never reserves/copies network requests or modifies old raw records. Replay and full report/QA are separate operations, so a completed replay can be reported again into a new directory without reparsing/recollecting.

Historical pages are selected from the latest original page index and independently sealed journals. Ordinary captures require exact original LIVE receipt/source/run/body SHA/time bindings. Inherited pages undergo strict original parent-provenance validation and retain parent journal/receipt references and IDs. Repeated identical ACP bodies are canonicalized by exact body SHA/source page; the latest normal original receipt is selected explicitly, all historical receipt references are retained. This is not a new capture and cannot earn T1/T2/T3 credit. Old revision records and raw titles remain untouched, with old observation IDs and original parsed titles carried into new derived rows.

`validate_replay_receipt(row,source)` independently rereads hash-bound sealed capture receipts and original page journals, and checks run/type/source/original body/hash/capture time/exact card href, local slot, rank and scope. Inherited records also require the original parent seed proof. Rating observation time uses the actual corresponding representation's receipt timestamp; the older parser's page-time timestamp is separately preserved in `historical_observed_at`.

The report includes full candidate CSV/JSONL and all observations, all source memberships and rating history, 75-cell coverage, full DOM/receipt QA, mapping-status denominators, URL anomalies and offline timing. Exports are re-counted and hashed. Missing page replay evidence is a QA error, not a silent successful subset. True source emptiness/navigation, unresolved native contracts, unknown completeness and conflicting category mappings remain visible. Independent purchasable SKU and parent-child identity are not claimed.

`history-v3` archives all indexed original records and audits their declared legacy title parser, exact original card/rank/context and original normal capture receipt. ID references alone are insufficient: a reused base observation ID can refer to a different body revision. Missing body/card/actual capture tuples are independently reparsed and added by `report-v3` as historical source memberships and rating history. The intermediate replay store remains immutable. Final QA compares the canonical URL sets independently against every original indexed row; historical supplement rows never become new network observations. Final category mapping is a new read-only per-card evidence projection with the previous projection retained and a second independent mapping pass.

## Pending LIVE stage templates

`prepare-v3` creates new run IDs and separate T1/T2/T3 plan and authorization files, all `approved=false`, `live_enabled=false`, `sources_reviewed=false`. URLs are literal records from the old reviewed navigation plan or an explicit new pending navigation pack. Every new pack entry must independently match its saved body SHA and a typed source rediscovered from the exact literal anchor and label. This proves link existence, not current source availability or authorization. T1 retains five type roots plus up to two new WISH and two new GIFT deep sources; T2/T3 retain the exact merged source pack with bounded requests/pages. No sibling-list substitution or manufactured page URL occurs. Sources are balanced by explicit FIVE_TYPE_ROUND_ROBIN_V3 scheduling, serial 3-second request pacing, zero category cooldown, bounded requests/pages/raw storage and ten-hour wall-clock cap. Original historical HTML is not copied into a new LIVE result.

`Start-Reviewed-V3.ps1` refuses pending files before it invokes existing guarded `highspeed run`. Activation requires explicit user approval of exact sources, budget, current shared access gate, new store/profile, and stage. Set finite current aware review/expiry times and supply the actual reviewed navigation evidence. T2 requires independently valid T0/T1 artifact references; T3 requires T0/T1/T2. Fill `authorization.prerequisites` with those sealed references, not booleans or this historical field-recovery report. Normal run/resume still enforces authorization, original epoch, shared gate, serial reservations and finite budgets.

New navigation entries remain individually `enabled=false` and every authorization source's `evidence.reviewed=false`. A global `live_enabled` or `approved` toggle does not review or authorize those exact scopes. Activation must explicitly review each selected literal source, its saved navigation proof and bounded page URLs, update its per-source enablement/review evidence, and bind that exact scope into the current authorization. Do not silently enable all newly discovered navigation entries.

## Actual Windows paths for this offline delivery

The final pending directory is `F:\AmazonESBestseller\交付文件\20261014\HighSpeedSnapshotV3_dd3c9221\pending_v3_6e6c77c814144dcdb9883d42b15fc926`. Earlier pending directories are retained intermediate preparations; use this directory's sealed manifest. Fresh machine stores are declared under `F:\AmazonESBestseller\runtime\highspeed_v3_fresh_batches`, outside delivery. Preparation does not create those stores or open a browser.

The following command independently creates another unapproved preparation in a new GUID directory; it sends zero requests:

```powershell
$env:PYTHONPATH='C:\Users\Administrator\.codex\worktrees\highspeed-snapshot-v3\AmazonESBestseller\src'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
Remove-Item Env:RUN_LIVE -ErrorAction SilentlyContinue
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m amazon_es_bestseller.cli highspeed prepare-v3 --historical-root 'F:\AmazonESBestseller\runtime\night5000_20261013T155222Z\store' --historical-run-id 'continue30000_20261013T204238Z' --mapping-config 'C:\Users\Administrator\.codex\worktrees\highspeed-snapshot-v3\AmazonESBestseller\configs\highspeed\v3\category_mapping.reviewed.json' --pending-source-pack 'F:\AmazonESBestseller\交付文件\20261014\HighSpeedSnapshotV3_dd3c9221\pending_new_navigation_evidence\new_sources.pending_review.json' --runtime-root 'F:\AmazonESBestseller\runtime\highspeed_v3_fresh_batches' --out 'F:\AmazonESBestseller\交付文件\20261014\HighSpeedSnapshotV3_dd3c9221'
```

For reviewed activation, the delivered launcher takes the exact plan/auth and runtime paths from the sealed stage manifest. Current T1 store is `F:\AmazonESBestseller\runtime\highspeed_v3_fresh_batches\v3_t1_ead7481ebc84\store`, T2 is `F:\AmazonESBestseller\runtime\highspeed_v3_fresh_batches\v3_t2_a9d8225fbc78\store`, and T3 is `F:\AmazonESBestseller\runtime\highspeed_v3_fresh_batches\v3_t3_b70c91f6c0a6\store`. These are pending references, not running batches. Preserve the pending originals; activate separately reviewed copies only after exact stage approval.

T1 tests five real entries and current score bindings; it is needed even when three historical list roots yielded no cards. T2 measures real source supply/coverage/throughput. T3 starts a genuinely new capture batch only after explicit approval and strict preceding stage gates. Neither synthetic tests nor historical replay establish current supply or ten-hour feasibility. Access restrictions stop new requests; there is no CAPTCHA, proxy, cookie, account or IP bypass.
