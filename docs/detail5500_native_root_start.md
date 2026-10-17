# Native browser root-start acknowledgement

Production task `detail_d717dd94be3f3c4eb10c6ebc6530` completed NORMAL 200 with
the correct product and full detail record, but stopped its three-lane scheduler
with `INITIAL_DOCUMENT_START_NOT_OBSERVED`. Its immutable receipt preserves the
missing Playwright root event. Native CDP evidence independently contains the
exact original GET, a 302, the linked same-request-ID redirect, and final 200.
The successful task and its old receipt are not rewritten or requested again.

The small fix adds a second start source: an actual `Network.requestWillBeSent`
root Document event. It must belong to a reserved current task, after goto was
requested, with exact original URL, GET, bound window main frame, task ID, real
finite native timestamp/wallTime and request ID. A response, orphan redirect,
foreign URL/frame, POST, missing timestamp or stopped dispatcher cannot release
the scheduler. Duplicate same-request-ID events are idempotent; another root ID
stops new work. A later Playwright route binds its original Request object but
cannot acknowledge a second start. Navigations after goto completion are refused,
including user reloads when the service worker hid the original route event.

Both raw CDP timestamps and Python callback-observed `document_started_at` are
kept with distinct evidence kinds. The latter remains a conservative scheduling
boundary, not a physical wire measurement. Service-worker/cache rows remain
excluded from actual HTTP overlap claims. No timestamp is derived from a later
response or backfilled into old evidence.

Parser, field schema, identity/family rules, profile, cookies, existing service
workers, route restrictions, three-lane bound, global three-second pacing,
cumulative budget/holds, no-success-replay, original deadlines and final full QA
are unchanged. No cache clearing, extra GET, verification action, retry, or Gate
recovery is introduced. Current approved collection may continue on its current
code; loading this new networking fix requires current offline regression and
successful full CI first, plus a safe drained or natural boundary.
