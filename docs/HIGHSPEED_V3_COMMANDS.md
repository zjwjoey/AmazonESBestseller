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

The final pending directory is `F:\AmazonESBestseller\交付文件\20261014\HighSpeedSnapshotV3_dd3c9221\pending_v3_a554d66487e24fe980bb5b02d8a914a0`. Earlier pending directories are retained intermediate preparations; use this directory's sealed manifest. Fresh machine stores are declared under `F:\AmazonESBestseller\runtime\highspeed_v3_fresh_batches`, outside delivery. Preparation does not create those stores or open a browser.

The following command independently creates another unapproved preparation in a new GUID directory; it sends zero requests:

```powershell
$env:PYTHONPATH='C:\Users\Administrator\.codex\worktrees\highspeed-snapshot-v3\AmazonESBestseller\src'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
Remove-Item Env:RUN_LIVE -ErrorAction SilentlyContinue
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m amazon_es_bestseller.cli highspeed prepare-v3 --historical-root 'F:\AmazonESBestseller\runtime\night5000_20261013T155222Z\store' --historical-run-id 'continue30000_20261013T204238Z' --mapping-config 'C:\Users\Administrator\.codex\worktrees\highspeed-snapshot-v3\AmazonESBestseller\configs\highspeed\v3\category_mapping.reviewed.json' --pending-source-pack 'F:\AmazonESBestseller\交付文件\20261014\HighSpeedSnapshotV3_dd3c9221\pending_new_navigation_evidence\new_sources.pending_review.json' --runtime-root 'F:\AmazonESBestseller\runtime\highspeed_v3_fresh_batches' --out 'F:\AmazonESBestseller\交付文件\20261014\HighSpeedSnapshotV3_dd3c9221'
```

For reviewed activation, the delivered launcher takes the exact plan/auth and runtime paths from the sealed stage manifest. Current T1 store is `F:\AmazonESBestseller\runtime\highspeed_v3_fresh_batches\v3_t1_2a0f2908aef9\store`, T2 is `F:\AmazonESBestseller\runtime\highspeed_v3_fresh_batches\v3_t2_ae061b1dc2e0\store`, and T3 is `F:\AmazonESBestseller\runtime\highspeed_v3_fresh_batches\v3_t3_76fc1e13b045\store`. These are pending references, not running batches. Preserve the pending originals; activate separately reviewed copies only after exact stage approval.

T1 tests five real entries and current score bindings; it is needed even when three historical list roots yielded no cards. T2 measures real source supply/coverage/throughput. T3 starts a genuinely new capture batch only after explicit approval and strict preceding stage gates. Neither synthetic tests nor historical replay establish current supply or ten-hour feasibility. Access restrictions stop new requests; there is no CAPTCHA, proxy, cookie, account or IP bypass.

## Copyable reviewed activation, inspection and same-run recovery

These commands are preparation instructions, not permission to start LIVE. The pending files intentionally cannot run. After explicit exact-stage review, save separately approved copies under the stage's runtime parent as `plan.approved.json` and `authorization.approved.json`. Their source enablement, current aware authorization expiry, gate, prerequisites and scope hashes must agree. Never merely toggle the pending approval flags. Change only `$v3Stage` to choose another explicitly approved stage; T2/T3 are not implicitly approved by T1.

```powershell
$v3Python='C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
$v3Worktree='C:\Users\Administrator\.codex\worktrees\highspeed-snapshot-v3\AmazonESBestseller'
$v3Pending='F:\AmazonESBestseller\交付文件\20261014\HighSpeedSnapshotV3_dd3c9221\pending_v3_a554d66487e24fe980bb5b02d8a914a0'
$v3Stage='T1'
$v3ManifestEnvelope=Get-Content -LiteralPath (Join-Path $v3Pending 'manifest.json') -Raw | ConvertFrom-Json
$v3Manifest=$v3ManifestEnvelope.payload
$v3Selected=$v3Manifest.stages.$v3Stage
$v3Store=$v3Selected.store_root
$v3Runtime=Split-Path -Parent $v3Store
$v3Plan=Join-Path $v3Runtime 'plan.approved.json'
$v3Authorization=Join-Path $v3Runtime 'authorization.approved.json'
$v3Profile=Join-Path $v3Runtime 'browser_profile'
$env:PYTHONPATH=Join-Path $v3Worktree 'src'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
Remove-Item Env:RUN_LIVE -ErrorAction SilentlyContinue
& (Join-Path $v3Pending 'Start-Reviewed-V3.ps1') -PlanPath $v3Plan -AuthorizationPath $v3Authorization -StoreRoot $v3Store -PythonPath $v3Python -WorktreePath $v3Worktree
```

The launcher explicitly uses the fixed `browser_profile` under the selected stage's runtime parent. The following equivalent direct `run` command uses that same profile; retain it for recovery. If an existing normal profile is explicitly reviewed instead, set `$v3Profile` to that exact path and use the direct command. Do not launch both commands or a second collector.

```powershell
& $v3Python -m amazon_es_bestseller.cli highspeed run --root $v3Store --run-id $v3Selected.run_id --plan $v3Plan --authorization $v3Authorization --profile-dir $v3Profile --headful --live
```

Inspect actual Windows commands/creation time before recovery; a PID file does not prove liveness. Status and QA below are local commands, not Amazon probes. Run final QA/report after the collector exits; output goes to a new GUID directory and historical source material is preserved. The command is named `qa`, not `verify`, in the actual CLI.

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId,CreationDate,CommandLine
& $v3Python -m amazon_es_bestseller.cli highspeed status --root $v3Store --run-id $v3Selected.run_id
& $v3Python -m amazon_es_bestseller.cli highspeed qa --root $v3Store --run-id $v3Selected.run_id
$v3ReportOut=Join-Path 'F:\AmazonESBestseller\交付文件\20261014\HighSpeedSnapshotV3_dd3c9221' ('fresh_stage_QA_'+[guid]::NewGuid().ToString('N'))
& $v3Python -m amazon_es_bestseller.cli highspeed report-v3 --root $v3Store --run-id $v3Selected.run_id --mapping-config (Join-Path $v3Worktree 'configs\highspeed\v3\category_mapping.reviewed.json') --out $v3ReportOut
```

Resume only after no collector exists, the same run's checkpoint is verified, both local and shared gates are NORMAL, and the exact approved authorization/budgets are still valid. This reuses captured evidence and preserves original start time and cumulative limits. An expired authorization or exhausted budget requires a newly explicit review; do not reset epochs or raise limits automatically.

```powershell
& $v3Python -m amazon_es_bestseller.cli highspeed resume --root $v3Store --run-id $v3Selected.run_id --plan $v3Plan --authorization $v3Authorization --profile-dir $v3Profile --headful --live
```

NETWORK_ERROR, unknown access state or explicit restrictions stop new requests. Preserve logs, receipts and gate fingerprint. Neither `status`, offline QA nor a successful unrelated browser page authorizes gate recovery. CAPTCHA/403/429/Robot Check/access denied require explicit handling and a bounded current recovery approval; no cookie/proxy/IP/account rotation or automatic clearing.

## Supply estimate and safe code rollback

`source_capacity_estimate.json` in the newest pending preparation records historical distinct URL supply, list overlaps, latest page-state counts and the first new deep source's position within the page-only budget prefix. Historical 30,026 unique links do not prove current fresh supply; all 876 newly discovered deep sources have unknown current product capacity. The current unique-capacity upper bound is unknown, the verified lower bound is zero, and target sufficiency remains UNVERIFIED. ACP or earlier failures can prevent a nominal page-prefix source being reached. T1/T2 results must replace this uncertainty with actual fresh card/URL/whole-QA timing evidence before T3 review.

Rollback affects code only. Wait until the exact collector exits or stop it deliberately with preserved checkpoint under the approved operational policy; never change parser code while a worker is running. Keep this V3 worktree, dirty files, stage runtime, raw HTML, receipts, journals and human reviews. Create another independent worktree at the verified V2 baseline rather than resetting this worktree or production main:

```powershell
git -C $v3Worktree status --short
$v3RollbackPath=Join-Path 'F:\AmazonESBestseller\runtime' ('v3_code_rollback_'+[guid]::NewGuid().ToString('N'))
git -C $v3Worktree worktree add --detach $v3RollbackPath 8a7fcd831db5b1c1803d2e656a085428456eaf04
```

This creates a code checkout and performs no LIVE request. A V3 store/normalizer contract must not be resumed with the V2 engine: preserve it for offline review or restore the matching reviewed V3 commit in another checkout. Historical V2 data can be inspected with its matching V2 version; do not migrate a store by silently replacing metadata. No `reset --hard`, recursive cleanup, main checkout change, evidence overwrite or access-gate reset is part of rollback.
