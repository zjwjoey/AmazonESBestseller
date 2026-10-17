# Isolated two-lane Detail5500 executor

User authorization: develop and offline-test an isolated two-lane module targeting
1,000 requests/hour, without stopping the current collector. Evaluate a switch
only after tests and current full CI pass. This is not an automatic deployment.

## Entry points and compatibility

- `amazon_es_bestseller.collection.detail_dual_runtime5500`: new independent entry.
- `detail_dual5500.TwoRequestScheduler`: exactly two capture lanes, no catch-up burst.
- Existing serial/three-lane entry points are unchanged and reject dual approvals.
- Reuses the current bridge, field parser, family policy, saved-evidence format,
  native browser timing recorder, final full-field audit and exporter. No reduced
  product-field projection or fixed attribute whitelist is introduced.

The isolated runtime deliberately starts from the reviewed three-lane runtime.
It changes the lane/window/parser count and dispatch target, not the access,
identity, rendering, reservation, persistence or final-QA rules. This small fork
avoids changing a running collector or introducing a generic scheduler refactor.

## Rate semantics

The target is 1,000 original approved DETAIL transactions per hour, not 1,000
successful or distinct products. One transaction may have observed redirect hops;
these are separately retained and do not become invented successful transactions.

Effective start interval is `max(original authorized interval, 3600/1000)`;
normally 3.6 seconds, globally, not per lane. A delayed task never earns burst
credit. Reservation and actual browser start acknowledgement are serialized;
response/render/parse waits do not hold the dispatch lock. At most two captures
are active or waiting for FIFO commit. Full HTML and the original three-second
render buffer are preserved. No artificial delay is added to make HTTP overlap.

The isolated dispatcher performs unchanged reservation validation *inside* the
pacing window, then waits only the remainder before actual dispatch. It never
subtracts validation from the minimum start gap. Both live Gates, request clock
and full-QA clock are checked again at the goto boundary. A stop during the wait
retains the unsent reserved token and its conservative accounting; it never drops
the receipt or starts its request. A new UNKNOWN without HTTP cannot overwrite a
pre-existing specific restriction.

Two lanes can sustain 1,000/hour only if their average lane occupancy is at most
7.2 seconds. Slower transport, full-field parsing, rendering, authorization and
disk I/O lower actual throughput. A virtual-clock 1,000-dispatch test proves
cadence and absence of bursts; it does not prove production capacity.

## Explicit deployment gate

New authorization must bind the unchanged run/root/plan, exact base authorization
SHA, current clean code and successful current full CI. Its separate mode sidecar
must use scope `TWO_IN_FLIGHT_DETAIL_1000_TARGET_SAME_FROZEN_RUN`, explicit user
instruction, `network_parallelism=2`, target 1000, target interval 3.6, original
profile, and no successful replay. Authorization requires local lanes 2, visible
windows 2, visibility true and target 1000. The base pacing field is not rewritten.

Original cumulative caps, setup/manual holds, start time, request/final deadlines,
attempt limits, approved recovery references, quarantine chain and T1/T2 cohort
are inherited exactly. Successful checkpoints are verified and skipped; reviewed
identity failures are skipped as failures; unresolved other failures block startup.
Foreign RESERVED receipts or checkpoint conflicts block. A single controller,
collector and shared-live lock prevent coexistence with the current executor.

Any 403/429/Challenge/CAPTCHA/access denial/UNKNOWN/non-NORMAL Gate stops all new
claims; already reserved work retains its evidence and drains. There is no retry,
new recovery approval, Gate clearing, verification click, account/profile/IP
rotation, UI-setting request or reserve-candidate substitution in this module.

Full QA reserve continues to use real T2 audit measurement:
`max(3600, measured_seconds/100*5500*1.2+900)` = 17611.398 seconds for this run,
plus 60 seconds to drain. Unresolved identity failures still block FINAL success.

## Verification and switch assessment

1. Scheduler tests: two overlapping captures, 3.6-second global gaps, 1,000 virtual
   dispatches, no bursts after delays, unique claims, budget exhaustion and drain.
2. Runtime tests: real saved HTML, entire canonical record equality, FIFO commits,
   access/identity failures, all raw evidence retained, unchanged QA reserve,
   amended-mode binding, no replay and original-entry rejection.
3. `scripts/verify_detail5500_dual_local_http.py`: two real visible Chromium windows,
   isolated temporary profile/store, local server only, original CDP timing
   snapshots and independently measured server HTTP peak. Slow and fast transport
   must distinguish actual peaks 2 and 1. Test-only localhost route override is
   not exposed by the production CLI. Invalid local fixture parser delays, where
   present, are explicit and never production speed evidence.
4. `scripts/benchmark_detail5500_dual_saved_pages.py`: same nine saved real pages,
   current three-lane versus isolated dual execution, simulated recent transport
   delay, real parser processes and full authorization checks; no Amazon requests
   or production checkpoint writes. Every raw canonical record must agree except
   the new capture timestamp. Report throughput honestly even if dual is slower.
5. Full existing offline CI on Python 3.11 and 3.12 for the exact new commit.

Only assess a switch after these pass. Require a credible gain after accounting
for handoff cost, remaining queue and full QA. If offline throughput misses the
target or is worse than the current executor, do not switch merely because CI is
green. Any later production test uses only pending original tasks, reports actual
requests and successful details separately, and measures a continuous 30-minute
window with native network overlap, complete evidence and unchanged old-success
SHA. Never replay successful tasks to benchmark, force a network overlap, or stop
healthy collection just to install this development module.
