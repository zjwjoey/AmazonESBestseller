# Quick Selection and DetailBridge5500 V0

This bounded adapter is based on the deployed `94e0561` snapshot branch, which
already contains `collection.detail`, `detail_v2`, `detail_executor`,
`monitoring.detail_planner` and `DetailState`. It does not modify main or replay
ranking collection. It is not the experimental three-lane executor.

## Offline preparation

`python -m amazon_es_bestseller.collection.quick5500_cli prepare --help`

The latest snapshot actually has 26,425 conservative groups, not 30,000. Its
original receipt audit and formal quantity acceptance failed. Preparation must
preserve these failures. The new input certificate binds the independently
verified original file hashes and DOM audit, then checks each original normal
receipt/reservation and original authorization at capture time. A frame with an
unapproved response or invalid native capture is quarantined. Every selected
observation and every source membership must be in the new certificate.

The deterministic rating-first sort uses valid rating, valid evaluation count,
cross-family occurrence, the percentile of observed positions within the same
source context, then visible monthly-bought lower bound. Ties use normalized
URL and candidate ID. Missing/conflicting ratings go to review, never to the
low-rating exclusion. Category exclusions require explicit nonphysical source
routes; product titles do not determine exclusion. Existing conservative groups
prevent double counting. ASIN and parent-ASIN do not collapse candidate tasks.

Preparation creates 5,500 selections and up to 1,000 reserves, a frozen real-URL
detail plan, input/exclusion/review evidence, cache reconciliation and an
**unapproved** independent detail budget proposal. The old snapshot's failed QA
markers are never changed. Task book, original snapshot, selection and plan
hashes are retained. Output folders must be new and isolated.

## Detail execution

`python -m amazon_es_bestseller.collection.detail5500_runtime --help`

No browser or Amazon request is opened by preparation. The worker requires a
separate approved `DETAIL_5500_INDEPENDENT` authorization, the exact frozen plan,
runtime root, fixed start/deadline, current clean commit, offline acceptance
reference and current successful full CI evidence. Old ranking budgets cannot
authorize this worker.

The bridge reuses BrowserSession's navigation/waits, the existing V2 parser,
access detector, atomic/checkpoint utilities and DetailState in isolated
candidate-specific slots. Unknown ASIN links remain URL-keyed; only actual active
product evidence supplies the ASIN. Parent/variant relationships cannot make an
unexpected active child a success. All raw ordered attributes are retained.
Page-wide monthly and unrelated variation-state fallbacks are narrowed in this
adapter without rewriting historical parser outputs. Real input/final URLs
replace the legacy parser's synthesized product URL.

Requests are serial with a minimum 3-second start interval. The existing shared
live lock and access gate apply. Images/media/fonts and XHR/fetch are blocked;
there is no image downloading, translation, detail API probing or location
change. An unverified Spain destination stops for review rather than fabricating
a price. Each task saves HTML, receipt, parsed record, URL identity and sealed
checkpoint immediately. Crash reservations and failed tasks block automatic
replay; valid successful candidates are hash-checked and skipped on resume.

T1 is the first 30 frozen tasks, source-diversified where the selected pool
permits; T2 is the cumulative first 100, reusing successful T1 work. Independent
stage replay checks saved HTML, URL/active-ASIN binding, receipt, every parsed
field, field coverage, request pacing and evidence hashes. Only successful
stage acceptance permits expansion. Final full replay uses the same standard.
Missing source fields stay missing and do not acquire guessed values.

403/429 and all challenge/unknown access states stop new network requests and
preserve both local and shared gates. There is no automatic gate clearing,
account/profile rotation, CAPTCHA handling, or stealth. Additional retries and
reserve replacements are not silently performed. The spare request cap is not
permission to add product tasks.

## Completion vocabulary

Keep code/CI, frozen selections, real T1, real T2, formal queue start and all
5,500 details complete as separate states. Existing saved HTML in offline tests
is not a new live success. A waiting authorization/CI or preparation process is
not a running collector. Background deployment and its one monitor are created
only after the independent detail authorization is explicit.
