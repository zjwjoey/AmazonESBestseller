"""Passive, browser-origin document timings. Never dispatches HTTP requests.

Python callback arrival times are deliberately not network interval endpoints.
Only Chromium's response ResourceTiming and loadingFinished timestamps qualify.
"""
from __future__ import annotations

import copy
import math
import uuid


class NetworkTimingRecorder:
    def __init__(self):
        self.session_id = uuid.uuid4().hex
        self.current = {}
        self.sessions = []

    async def attach(self, cdp, lane, lookup):
        frame = (await cdp.send('Page.getFrameTree'))['frameTree']['frame']['id']
        cdp.on('Network.requestWillBeSent', lambda p:self.request(lane, frame, lookup(), p))
        cdp.on('Network.responseReceived', lambda p:self.response(lane, p))
        cdp.on('Network.loadingFinished', lambda p:self.finished(lane, p))
        cdp.on('Network.loadingFailed', lambda p:self.failed(lane, p))
        cdp.on('Network.requestServedFromCache', lambda p:self.cached(lane, p))
        # Network.enable subscribes to events; no navigation, reload, fetch,
        # cache disabling, request interception or authentication changes.
        await cdp.send('Network.enable')
        self.sessions.append(cdp)

    def request(self, lane, main_frame, token, p):
        request = p.get('request', {})
        if (not token or not token.get('navigation_requested') or p.get('type') != 'Document'
                or p.get('frameId') != main_frame or request.get('method') != 'GET'):
            return
        key = (lane, p.get('requestId'))
        prior = self.current.get(key)
        redirect = p.get('redirectResponse')
        if redirect:
            if not prior or prior['owner'] is not token or redirect.get('url') != prior['row']['url']:
                return
            self._response(prior['row'], redirect)
        elif request.get('url') != token['task']['request_url']:
            return
        row = dict(task_id=token['task']['task_id'], lane=lane, request_id=p.get('requestId'),
                   hop_index=prior['row']['hop_index']+1 if redirect else 0,
                   url=request.get('url'), method='GET', frame_id=main_frame,
                   browser_request_event_monotonic_seconds=p.get('timestamp'),
                   browser_request_wall_time_seconds=p.get('wallTime'),
                   served_from_cache_event=False)
        token.setdefault('browser_network_records', []).append(row)
        self.current[key] = dict(owner=token, row=row)

    @staticmethod
    def _response(row, response):
        # Do not retain response/request headers, cookies or credentials.
        row.update(status_code=response.get('status'), protocol=response.get('protocol'),
                   cache={k:response.get(k) for k in
                          ('fromDiskCache', 'fromServiceWorker', 'fromPrefetchCache')})
        if isinstance(response.get('timing'), dict):
            row['response_timing'] = copy.deepcopy(response['timing'])

    def response(self, lane, p):
        current = self.current.get((lane, p.get('requestId')))
        if current and p.get('response', {}).get('url') == current['row']['url']:
            self._response(current['row'], p['response'])
            current['row']['browser_response_event_monotonic_seconds'] = p.get('timestamp')

    def finished(self, lane, p):
        current = self.current.get((lane, p.get('requestId')))
        if current:
            current['row']['loading_finished_monotonic_seconds'] = p.get('timestamp')

    def failed(self, lane, p):
        current = self.current.get((lane, p.get('requestId')))
        if current:
            current['row']['loading_failed'] = dict(timestamp=p.get('timestamp'),
                                                   error_text=p.get('errorText'))

    def cached(self, lane, p):
        current = self.current.get((lane, p.get('requestId')))
        if current:current['row']['served_from_cache_event'] = True

    def snapshot(self, token):
        return dict(schema='CHROMIUM_CDP_DOCUMENT_NETWORK_TIMING_V1', session_id=self.session_id,
                    time_domain='CHROMIUM_MONOTONIC_SECONDS',
                    requests=copy.deepcopy(token.get('browser_network_records', [])))

    async def close(self):
        for session in self.sessions:
            try:await session.detach()
            except Exception:pass  # Never mask the collector's original result.


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _peak(intervals):
    if not intervals:return None
    events = [(start, 1) for start, end in intervals] + [(end, -1) for start, end in intervals]
    active = maximum = 0
    for _, delta in sorted(events):  # Ends precede starts at an identical instant.
        active += delta
        maximum = max(maximum, active)
    return maximum


def network_overlap(snapshots):
    sessions = {s['session_id'] for s in snapshots if s.get('requests')}
    if len(sessions) > 1:raise ValueError('DIFFERENT_BROWSER_CLOCK_SESSION')
    intervals = []
    seen = {}
    duplicates = 0
    unknown = excluded = raw_count = 0
    for snapshot in snapshots:
        for row in snapshot.get('requests', []):
            key = (snapshot['session_id'], row['lane'], row['request_id'], row.get('hop_index', 0))
            if key in seen:
                if row != seen[key]:raise ValueError('CONFLICTING_BROWSER_REQUEST_TIMING')
                duplicates += 1
                continue
            seen[key] = row
            raw_count += 1
            cache = row.get('cache') or {}
            if row.get('served_from_cache_event') or any(cache.get(k) is True for k in
                    ('fromDiskCache', 'fromServiceWorker', 'fromPrefetchCache')):
                excluded += 1
                continue
            t = row.get('response_timing') or {}
            values = [t.get(k) for k in ('requestTime', 'sendStart', 'sendEnd', 'receiveHeadersStart', 'receiveHeadersEnd')]
            if (not all(_number(v) and v >= 0 for v in values)
                    or cache.get('fromDiskCache') is not False or cache.get('fromServiceWorker') is not False
                    or 'loading_failed' in row):
                unknown += 1
                continue
            base, send, sent, headers, headers_end = values
            if not send <= sent <= headers <= headers_end:
                unknown += 1
                continue
            start, first, complete = base+send/1000, base+headers/1000, base+headers_end/1000
            if not start < first <= complete:
                unknown += 1
                continue
            end = row.get('loading_finished_monotonic_seconds')
            if not _number(end) or end < complete:end = None
            intervals.append(dict(task_id=row['task_id'], lane=row['lane'], request_id=row['request_id'],
                                  url=row['url'], send_monotonic_seconds=start,
                                  first_byte_monotonic_seconds=first, headers_end_monotonic_seconds=complete,
                                  body_end_monotonic_seconds=end))
    first = [(r['send_monotonic_seconds'], r['first_byte_monotonic_seconds']) for r in intervals]
    body = [(r['send_monotonic_seconds'], r['body_end_monotonic_seconds']) for r in intervals
            if r['body_end_monotonic_seconds'] is not None]
    return dict(raw_document_requests=raw_count, measured_http_requests=len(intervals),
                identical_duplicate_records_ignored=duplicates,
                unknown_http_requests=unknown, excluded_cache_requests=excluded,
                coverage_complete=bool(intervals) and unknown == 0,
                body_coverage_complete=bool(intervals) and unknown == 0 and len(body) == len(intervals),
                request_to_first_byte_peak=_peak(first), request_to_body_end_peak=_peak(body),
                peaks_are_observed_lower_bounds_when_coverage_incomplete=True,
                source='BROWSER_CDP_RESOURCE_TIMING_NOT_PYTHON_CALLBACK_CLOCK', intervals=intervals)
