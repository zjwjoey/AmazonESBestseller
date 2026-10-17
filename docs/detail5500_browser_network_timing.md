# Browser-origin HTTP overlap evidence

The prior `document_started_at` and `document_response_at` are Python callback
arrival clocks. They remain immutable diagnostics and are not network timing
proof. No historical receipt is rewritten or retroactively assigned a timing.

The passive CDP observer is attached to the existing three pages before queue
requests begin. It enables Network events, reads the main frame ID, and records
only owned original document GETs and their observed redirect hops. It performs
no navigation, reload, extra fetch, interception, cache policy change, identity
change, or request retry. The original dispatcher, locks, cumulative budget,
three-second global initial-start minimum, access gate, parser and final QA are
unchanged.

Each receipt retains the raw browser `ResourceTiming` values, request ID, hop,
lane, protocol, status, cache/service-worker flags, browser request monotonic and
wall times, and browser loading-finished timestamp. Response/request headers,
cookies and credentials are not retained by this diagnostic.

Two distinct HTTP intervals are computed in Chromium's shared monotonic clock:

* Request send to first response byte: `requestTime + sendStart/1000` through
  `requestTime + receiveHeadersStart/1000`.
* Request send to completed response body: same start through the browser
  `Network.loadingFinished.timestamp`.

Rendering, Python callback arrival, local parsing and FIFO checkpoint commit do
not extend these network intervals. Complete-body timing is separately unknown
when the completion event is missing. Cached/service-worker responses are
excluded from live network counts. Missing, nonfinite, negative, inconsistent
or failed-request timings remain unknown. No callback or response timestamp is
used to invent a missing browser send time. Different browser sessions cannot
be silently merged; duplicate observations cannot inflate the peak.

An observed peak with incomplete coverage is only a lower bound. The hourly
HTTP fields remain null unless browser timing coverage is complete; callback
overlap is explicitly labeled separately. Original URLs, per-hop evidence and
unknown counts remain available for independent replay.

References: [CDP Network ResourceTiming](https://chromedevtools.github.io/devtools-protocol/tot/Network/#type-ResourceTiming),
[CDP loadingFinished](https://chromedevtools.github.io/devtools-protocol/tot/Network/#event-loadingFinished).

Production evidence must come solely from the original queue's normal requests.
Localhost smoke tests validate both a real HTTP peak of three and a real HTTP
peak of one; neither is a claim about Amazon's production network concurrency.
