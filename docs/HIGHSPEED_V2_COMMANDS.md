# High-Speed V2 commands

V2 remains isolated from production and P0. The checked-in T1/T2/T3 templates are pending source-capacity and human review, with `live_enabled=false` and empty exact source scopes. None starts live collection. Requests remain serial with 3-second pacing and no category cooldown. The inherited shared Access Gate must be NORMAL; creating a new V2 store never clears it.

Run from this worktree in PowerShell. The helper sets this checkout's explicit `src`, prints the actual imported module path, and restores environment variables after execution.

On Windows, use a short ordinary runtime path. V2 rejects a store root longer than 170 UTF-16 characters or a run journal path without room for atomic temporary filenames, with `SHORTER_ROOT_REQUIRED` before network/DB writes. Benchmark output also creates nested stores; choose a short directory such as `F:\AmazonESBestseller\runtime\v2_bench`, then copy user-facing reports to the delivery directory. No Windows registry setting is changed.

```powershell
$v2Root = 'F:\AmazonESBestseller\runtime\highspeed_v2_offline_example\store'
$v2Out = 'F:\AmazonESBestseller\runtime\highspeed_v2_offline_example\reports'
./scripts/Invoke-HighSpeed.ps1 --offline highspeed --help
./scripts/Invoke-HighSpeed.ps1 --offline highspeed plan --stage T0 --run-id t0_replay --out plan.pending.json
```

For offline replay, fill the plan's sources with actual saved page URLs and `offline_pages` mapping each exact URL to its local HTML. Historical replay additionally requires `offline_metadata` per URL containing the original aware `observed_at`, `page_url`, and `html_sha256`; no original timestamp is invented. Use `origin=OFFLINE_FIXTURE` when source capture time is unknown. Plans require finite run, day, page, raw-observation, storage and duration budgets, distinct source IDs, and exact nonduplicate page URLs.

```powershell
./scripts/Invoke-HighSpeed.ps1 --offline highspeed run --root $v2Root --plan plan.offline.reviewed.json
./scripts/Invoke-HighSpeed.ps1 --offline highspeed resume --root $v2Root --plan plan.offline.reviewed.json
./scripts/Invoke-HighSpeed.ps1 --offline highspeed status --root $v2Root --run-id t0_replay
./scripts/Invoke-HighSpeed.ps1 --offline highspeed qa --root $v2Root --run-id t0_replay
./scripts/Invoke-HighSpeed.ps1 --offline highspeed report --root $v2Root --run-id t0_replay --out $v2Out
./scripts/Invoke-HighSpeed.ps1 --offline highspeed gate-export --root $v2Root --run-id t0_replay --out $v2Out
./scripts/Invoke-HighSpeed.ps1 --offline highspeed renormalize --root $v2Root --run-id t0_replay --out $v2Out
./scripts/Invoke-HighSpeed.ps1 --offline highspeed review-identity --root $v2Root --run-id t0_replay --review identity.review.json
./scripts/Invoke-HighSpeed.ps1 --offline highspeed discover --html saved.html --page-url 'https://www.amazon.es/gp/bestsellers' --source-type BEST_SELLERS --observed-at '2026-10-13T05:49:28Z' --origin HISTORICAL_REPLAY --out discovered.pending.json
./scripts/Invoke-HighSpeed.ps1 --offline highspeed benchmark --pages saved-pages.json --out $v2Out
```

`saved-pages.json` is an array of actual saved-HTML specs (`html_path`, `page_url`, `source`, `observed_at`, optional saved `acp_responses`). Benchmark also exercises explicitly generated fixtures; fixture throughput is not measured Amazon throughput or proof of 30,000 independently purchasable SKUs. Reports preserve normalized URL identities, suspicious variants and strict identity uncertainty separately.

Live execution is a separate human-approved action. Review each stage independently, the exact observed source URLs, source mapping proof paths/hashes, finite budgets, expiry, and the existing shared gate path. T2 requires verified T0/T1 artifacts; T3 requires all T0/T1/T2, plus explicit T3 approval. Gate export rechecks immutable receipt bodies and artifact URL pools and cannot convert an empty, partial, unverified, failed or unrelated page into an accepted stage.

Approval must bind the exact absolute `store_root` and `shared_access_gate_path`; changing stores or using a fresh gate cannot reuse another approval. The store lock and shared gate-directory live lock are held across browser setup and all requests. Daily usage and the latest request timestamp remain shared across runs in one store.

ACP endpoints contain page-specific dynamic tokens. A reviewed source may explicitly set `allow_native_acp=true` instead of inventing an endpoint URL in advance. This permits only an actually captured and aborted browser-native POST from the exact approved parent page to its same-origin `/acp/.../nextPage` endpoint. The immutable native contract, payload hash and headers are verified before transmission. Later batches must rebuild from that original opaque contract and the saved page metadata; unrelated XHR endpoints and guessed bodies remain forbidden. Existing explicitly reviewed `acp_urls` scopes also remain supported.

```powershell
# Only after explicit approval and actual review; these files are not supplied as approved.
./scripts/Invoke-HighSpeed.ps1 highspeed run --root $v2Root --plan T1.plan.approved.json --authorization T1.authorization.approved.json --live
./scripts/Invoke-HighSpeed.ps1 highspeed resume --root $v2Root --plan T1.plan.approved.json --authorization T1.authorization.approved.json --live
./scripts/Invoke-HighSpeed.ps1 --offline highspeed gate-export --root $v2Root --run-id T1_reviewed --authorization T1.authorization.approved.json --out $v2Out
```

Do not combine `--offline` with live stages. All access restrictions and network failures stop new requests and retain evidence. The CLI never clears gates automatically or schedules automatic retries. Navigation-only verified root pages may continue to the next already approved URL but retain FAILED QA and never contribute product capacity. No discovery URL is automatically followed.

`gate-recover --root <store> --authorization <explicit-human-handling.json>` is an offline, manually requested local recovery operation. Its artifact must identify the exact prior gate, store, human handling, approval identity and validity window. It never changes the inherited shared project gate and never resumes network requests.


A task-specific controller may use `highspeed.recovery.recheck_network_error` only with an explicit, current delegation bound to the exact NETWORK_ERROR gate fingerprint, store, run and approved source. It performs one charged source load and at most one refresh, preserves prior gate and response evidence, and clears the gate only after HTTP 200/NORMAL plus verified source context and real product cards. It records agent handling honestly. Restrictions and unexplained UNKNOWN are never cleared by this recovery.

When a NORMAL source page contains real cards but a bounded native-continuation observation yields `ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED`, V2 preserves the prefix as PARTIAL/UNKNOWN and proceeds to the next frozen approved source. Resume skips that terminal partial page; it never marks completeness or stage acceptance as passed. Other preparation failures preserve the raw prefix and stop. Only NORMAL pages with verified list type and source context contribute accepted target candidates.

The same exact native-contract miss on an HTTP 200/NORMAL, source-verified zero-card page with actual ACP metadata preserves FAILED/UNKNOWN and zero capacity while advancing the frozen snapshot queue. Resume rechecks the saved receipt, HTML hash, source binding and zero-card parser evidence before skipping a previously recorded miss; it does not refresh that page again. An empty page without this evidence, a different contract error, or UNKNOWN access still stops collection.

A saved MOST_WISHED_FOR, MOST_GIFTED or BEST_SELLERS page may also prove source emptiness through its corresponding exact official empty-category message in the right-column grid, an empty client list and end-of-list marker, with no product links, card evidence, expected count or pending ACP. Statement whitespace is collapsed; the bestseller wording omits the `de los` used by the other two types. This preserves FAILED/UNKNOWN and zero capacity, records `SOURCE_EMPTY`, and allows the frozen queue to continue. The official right-column card-title heading takes priority over a generic gift-ideas banner; unrelated footer headings do not establish list type. Cached decisions are rechecked against the original HTML hash and fresh source evidence on resume, including the previously misclassified gift page; an unproven empty grid remains a parser failure.

For a task using an existing normal browser profile, pass the same `--profile-dir <absolute-path>` to run and resume. A process ID file alone is not evidence that the worker remains alive; check the Windows process and current SQLite run state before starting any resume.

An explicitly authorized cumulative 30,000-link continuation may seed a successor in the same isolated store with `highspeed.continuation.seed_successor(store, parent_run_id, successor_plan)`. The successor must use stage `T1`, `continuation_mode=USER_AUTHORIZED_30000_CONTINUATION`, and the matching `parent_run_id`; this does not certify a formal T3 stage. Seeding verifies the immutable parent journals, original PAGE/ACP receipts, body hashes, exact source scope and indexed row eligibility. Derived observations retain their original capture times and parent receipt identities as `HISTORICAL_REPLAY` with `is_derivation=true`; parent records remain unchanged and seeding does not charge new requests. An unfinished native continuation requires a newly charged PAGE context rather than reuse of a parent native contract. Reports separate inherited, cumulative and newly added candidates; throughput and ETA exclude inherited candidates, while missing source capacity and prior completeness failures remain unknown or failed.
