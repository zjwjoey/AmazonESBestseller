"""Bounded T1 entry/protocol diagnostics using existing durable request engine.

T2/T3 remain closed until actual new-source loading protocol proofs exist.
UNKNOWN completeness is preserved even when every approved root was visited.
"""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import hashlib
from pathlib import Path
import time

from ..multisource.storage import file_lock
from .runner import HighSpeedRunner, _next_acp_pending
from .budgets import utc_text, aware_time, budget_reason
from .v4_authorization import validate_v4_authorization
from .v4_adapters import parse_v4_page
from .v4_sources import RANKED
from .v4_quality import audit_v4


class V4T1Runner:
    def __init__(self, store, *, clock=None, sleep=None):
        self.store = store
        self.engine = HighSpeedRunner(store, clock=clock, sleep=sleep)

    def run(self, plan, *, authorization, transport, resume=False, preheld_lock=False, reverify_source_ids=()):
        if plan.get('stage') != 'T1':
            raise ValueError('V4_T2_T3_REQUIRE_VERIFIED_DYNAMIC_PROTOCOLS')
        e = self.engine; run_id = plan['run_id']; entry = utc_text(e.clock())
        if self.store.db.execute('SELECT 1 FROM runs WHERE run_id<>? LIMIT 1', (run_id,)).fetchone():
            raise ValueError('V4_FRESH_STORE_NO_HISTORICAL_RUNS_REQUIRED')
        validate_v4_authorization(plan, authorization, e._gate(plan), now=e.clock(), store_root=self.store.root)
        if (not set(reverify_source_ids).issubset({s['source_id'] for s in plan['sources']})
                or reverify_source_ids and not resume):
            raise ValueError('V4_REVERIFY_SAME_APPROVED_SOURCE_AND_RUN_REQUIRED')
        with ExitStack() as locks:
            locks.enter_context(nullcontext() if preheld_lock else self.store.run_lock(run_id))
            if not preheld_lock:
                locks.enter_context(file_lock(Path(plan['shared_access_gate_path']).parent / 'highspeed_live.lock'))
            validate_v4_authorization(plan, authorization, e._gate(plan), now=e.clock(), store_root=self.store.root)
            run = self.store.get_run(run_id) if resume else self.store.begin_run(plan, started_at=entry)
            if run['plan'] != plan:
                raise ValueError('IMMUTABLE_RUN_PLAN_MISMATCH')
            if any(r['status'] == 'RESERVED' for r in self.store.requests(run_id)):
                raise ValueError('AMBIGUOUS_UNFINISHED_REQUESTS_NO_AUTO_RETRY')
            started = run['started_at']; last = self.store.latest_request_time()
            last = aware_time(last) if last else None
            committed = {p['source_id'] for p in self.store.pages(run_id)} - set(reverify_source_ids)
            self.store.update_run(run_id, status='RUNNING')
            stop = None
            for source in plan['sources']:
                if source['source_id'] in committed:
                    continue
                try:
                    reason = budget_reason(plan, self.store.summary(run_id), now=e.clock(), started_at=started)
                    if reason:
                        raise ValueError(reason)
                    url = source['actual_source_url']; key = source['source_id']
                    existing = [r for r in self.store.iter_responses(run_id) if r.get('kind') == 'PAGE'
                                and r.get('request_url') == url and r.get('access_state') == 'NORMAL']
                    if key in reverify_source_ids:
                        existing = []
                        self.store.append_event(run_id, {'kind': 'V4_T1_IN_SCOPE_REVERIFICATION', 'source_id': key,
                            'page_url': url, 'reason': 'USER_AUTHORIZED_OFFLINE_FIX_THEN_BOUNDED_REVERIFY'})
                    cached = bool(existing)
                    if existing:
                        receipt = existing[-1]
                        response = self.store.get_response(receipt['request_id'])
                    else:
                        response, receipt, last = e._request(plan, authorization, transport, source, url,
                                                            'PAGE', started, last, page_key=key)
                    acp = []
                    for old in self.store.iter_responses(run_id):
                        if (old.get('kind') == 'ACP' and old.get('page_key') == key and old.get('access_state') == 'NORMAL'
                                and aware_time(old['observed_at']) >= aware_time(receipt['observed_at'])
                                and (old.get('ranking_context_binding') or {}).get('page_evidence_sha256') == receipt['sha256']):
                            acp.append(self._acp(old))
                    def parse():
                        return parse_v4_page(response['text'], source=source, page_url=url, run_id=run_id,
                                  snapshot_id=plan['snapshot_id'], observed_at=receipt['observed_at'],
                                  evidence_path=receipt['html_path'], http_status=receipt.get('status_code'),
                                  origin='LIVE_CAPTURE', acp_responses=acp)
                    parsed = parse()
                    def commit():
                        p = {**parsed['page'], 'run_id': run_id, 'snapshot_id': plan['snapshot_id'],
                             'page_key': key, 'source': source, 'capture_receipt_reference': e._receipt_ref(receipt),
                             'source_type_verified': bool(parsed['observations']), 'source_context_valid': True,
                             'execution_status': 'APPROVED_FRAME_RECORDED_NOT_COMPLETE',
                             'acp_captures': [{k: v for k, v in a.items() if k != 'body'} for a in acp]}
                        self.store.commit_page(run_id, key, p, parsed['observations'])
                    commit()  # Save initial cards before any continuation can fail.
                    attempted = set()
                    if (source['source_type'] in RANKED and not cached
                            and authorization['sources'][key].get('allow_native_acp') is True):
                        while parsed['acp_pending']:
                            # Reserve one request for each still-unvisited exact root.
                            remaining_pages = sum(s['source_id'] not in committed and s['source_id'] != key
                                                  for s in plan['sources'])
                            if plan['max_requests_per_run'] - self.store.summary(run_id)['requests'] <= remaining_pages:
                                break
                            descriptor = _next_acp_pending(parsed['acp_pending'], set(), v3=True)
                            if not descriptor:
                                break
                            validate_v4_authorization(plan, authorization, e._gate(plan), now=e.clock(), store_root=self.store.root)
                            descriptor = transport.prepare_ajax_descriptor({**descriptor, 'source_id': key}, referer=url)
                            native_key = (descriptor['url'], descriptor['method'], descriptor.get('payload'))
                            if native_key in attempted:
                                self.store.append_event(run_id, {'kind': 'ACP_NO_PROGRESS', 'source_id': key})
                                break
                            attempted.add(native_key)
                            _, saved, last = e._request(plan, authorization, transport, source, descriptor['url'],
                                              'ACP', started, last, descriptor=descriptor, page_key=key)
                            acp.append(self._acp(saved)); parsed = parse(); commit()
                    self.store.append_event(run_id, {'kind': 'V4_DISCOVERED_NAVIGATION', 'source_id': key,
                        'links': parsed['discovered_sources'], 'note': 'Discovery only, not visited or approved.'})
                    committed.add(key)
                    # A failed/unknown frame stops. It never becomes source-empty.
                    if not parsed['observations']:
                        raise ValueError('V4_SOURCE_FRAME_UNVERIFIED_SAVED_FOR_OFFLINE_DIAGNOSIS')
                except (ValueError, OSError) as exc:
                    stop = str(exc)
                    self.store.append_event(run_id, {'kind': 'STOP', 'reason': stop, 'observed_at': utc_text(e.clock())})
                    break
            pages = self.store.pages(run_id); rows = list(self.store.iter_observations(run_id))
            qa_started = time.perf_counter()
            quality = audit_v4(pages, rows, plan['sources'], require_live=True)
            self.store.append_event(run_id, {'kind': 'V4_CENTRAL_DOM_REVIEW',
                    'duration_seconds': time.perf_counter() - qa_started, 'errors': quality['errors']})
            stop = stop or 'V4_T1_INITIAL_SCOPE_EXHAUSTED_PROTOCOL_CAPACITY_STILL_UNVERIFIED'
            self.store.update_run(run_id, status='STOPPED', stop_reason=stop, qa=quality,
                    quality_gate_passed=False, finished_at=utc_text(e.clock()))
            return {'run': self.store.get_run(run_id), 'summary': self.store.summary(run_id), 'quality': quality}

    @staticmethod
    def _acp(receipt):
        return {**receipt, 'body': Path(receipt['html_path']).read_text(encoding='utf8'),
                'evidence_path': receipt['html_path'], 'evidence_sha256': receipt['sha256']}
