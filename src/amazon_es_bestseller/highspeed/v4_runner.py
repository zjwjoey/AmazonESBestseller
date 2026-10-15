"""Shared bounded V4 serial engine with separately approved stage subclasses.

UNKNOWN completeness is preserved even when every approved root was visited.
The three-source campaign does not enable unmarked/generic T3 execution.
"""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import hashlib
from pathlib import Path
import time

from ..multisource.storage import file_lock
from .runner import HighSpeedRunner, _next_acp_pending, continuation_unavailable
from .budgets import utc_text, aware_time, budget_reason
from .v4_authorization import validate_v4_authorization
from .v4_adapters import parse_v4_page
from .v4_sources import RANKED
from .v4_quality import audit_v4


class V4T1Runner:
    stage = 'T1'
    def __init__(self, store, *, clock=None, sleep=None):
        self.store = store
        self.engine = HighSpeedRunner(store, clock=clock, sleep=sleep)

    def run(self, plan, *, authorization, transport, resume=False, preheld_lock=False, reverify_source_ids=()):
        if plan.get('stage') != self.stage:
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
            self._record_approval(plan,authorization)
            started = run['started_at']; last = self.store.latest_request_time()
            last = aware_time(last) if last else None
            committed = {p['page_url'] for p in self.store.pages(run_id)
                         if p['source_id'] not in reverify_source_ids and (self.stage not in {'T2','T3'} or
                             p.get('execution_status')=='APPROVED_SOURCE_PAGE_SCOPE_EXECUTED')}
            self.store.update_run(run_id, status='RUNNING')
            stop = None
            work=[(s,u) for s in plan['sources'] for u in s['page_urls']]
            for source,url in work:
                if url in committed:
                    continue
                try:
                    reason = budget_reason(plan, self.store.budget_counters(run_id), now=e.clock(), started_at=started)
                    if reason:
                        raise ValueError(reason)
                    key = source['source_id'] if url==source['actual_source_url'] else source['source_id']+'_'+hashlib.sha256(url.encode()).hexdigest()[:12]
                    sid=source['source_id']
                    existing = [r for r in self.store.iter_responses(run_id) if r.get('kind') == 'PAGE'
                                and r.get('request_url') == url and r.get('access_state') == 'NORMAL']
                    if key in reverify_source_ids:
                        existing = []
                        self.store.append_event(run_id, {'kind': 'V4_T1_IN_SCOPE_REVERIFICATION', 'source_id': key,
                            'page_url': url, 'reason': 'USER_AUTHORIZED_OFFLINE_FIX_THEN_BOUNDED_REVERIFY'})
                    if (self.stage in {'T2','T3'} and existing and (authorization['sources'][sid].get('allow_native_acp')
                            or authorization['sources'][sid].get('allow_native_dynamic'))):
                        # An interrupted capture has no active browser widget in
                        # a resumed session. Reopen this exact approved page,
                        # charged against the same budgets; keep all old bytes.
                        existing=[]
                    cached = bool(existing)
                    if existing:
                        receipt = existing[-1]
                        response = self.store.get_response(receipt['request_id'])
                    else:
                        response, receipt, last = e._request(plan, authorization, transport, source, url,
                                                            'PAGE', started, last, page_key=key)
                    acp = []
                    root_receipt=receipt
                    dynamic_receipts=[]
                    rendered_frames=[]
                    derivation=None
                    for old in self.store.iter_responses(run_id):
                        if (old.get('kind') == 'ACP' and old.get('page_key') == key and old.get('access_state') == 'NORMAL'
                                and aware_time(old['observed_at']) >= aware_time(receipt['observed_at'])
                                and (old.get('ranking_context_binding') or {}).get('page_evidence_sha256') == receipt['sha256']):
                            acp.append(self._acp(old))
                    component_failures = {}
                    loading_stop_reason = None
                    scope_executed=False
                    def source_loading_count():
                        return self.store.db.execute("SELECT count(*) FROM requests WHERE run_id=? AND kind='ACP' "
                            "AND json_extract(data_json,'$.source_id')=?",(run_id,sid)).fetchone()[0]
                    def parse():
                        return parse_v4_page(response['text'], source=source, page_url=url, run_id=run_id,
                                  snapshot_id=plan['snapshot_id'], observed_at=receipt['observed_at'],
                                  evidence_path=receipt['html_path'], http_status=receipt.get('status_code'),
                                  origin='LIVE_CAPTURE', acp_responses=acp,
                                  page_number=source['page_urls'].index(url)+1)
                    parsed = parse()
                    def commit():
                        p = {**parsed['page'], 'run_id': run_id, 'snapshot_id': plan['snapshot_id'],
                             'page_key': key, 'source': source, 'capture_receipt_reference': e._receipt_ref(root_receipt),
                             'source_type_verified': bool(parsed['observations']), 'source_context_valid': True,
                             'execution_status': 'APPROVED_SOURCE_PAGE_SCOPE_EXECUTED' if scope_executed
                                else 'APPROVED_FRAME_RECORDED_NOT_COMPLETE',
                             'source_representation': receipt.get('source_representation', 'RENDERED_DOM'),
                             'document_html_reference': {'path': receipt.get('document_html_path'), 'sha256': receipt.get('document_sha256')},
                             'rendered_html_reference': {'path': receipt.get('rendered_html_path'), 'sha256': receipt.get('rendered_sha256')},
                             'continuation_failures': dict(component_failures),
                             'loading_stop_reason': loading_stop_reason,
                             'rendered_derivation': derivation,
                             'acp_captures': [{k: v for k, v in a.items() if k != 'body'} for a in acp]}
                        self.store.commit_page(run_id, key, p, parsed['observations'])
                    commit()  # Save initial cards before any continuation can fail.
                    attempted = set()
                    excluded = set()
                    if (source['source_type'] in RANKED and not cached
                            and authorization['sources'][sid].get('allow_native_acp') is True):
                        while parsed['acp_pending']:
                            # Reserve one request for each still-unvisited exact root.
                            remaining_pages = self.remaining_page_work(plan,work,committed,url)
                            if plan['max_requests_per_run'] - self.store.budget_counters(run_id)['requests'] <= remaining_pages:
                                loading_stop_reason = 'RESERVED_REQUESTS_FOR_REMAINING_PAGES'
                                break
                            if (source_loading_count() if self.stage in {'T2','T3'} else len(attempted))>=plan.get('max_native_requests_per_source',50):
                                loading_stop_reason = 'PER_SOURCE_NATIVE_REQUEST_CAP'
                                break
                            descriptor = _next_acp_pending(parsed['acp_pending'], excluded, v3=True)
                            if not descriptor:
                                loading_stop_reason = 'REMAINING_COMPONENT_CONTRACT_UNOBSERVED'
                                break
                            validate_v4_authorization(plan, authorization, e._gate(plan), now=e.clock(), store_root=self.store.root)
                            try:
                                descriptor = transport.prepare_ajax_descriptor({**descriptor, 'source_id': sid}, referer=url)
                            except ValueError as exc:
                                legacy = {'page': parsed['page'].get('legacy_page', {}), 'observations': parsed['observations']}
                                if (not (descriptor.get('metadata') or {}).get('carousel')
                                        or not continuation_unavailable(legacy, receipt, str(exc), e._gate(plan))):
                                    raise
                                context = descriptor['metadata']['ranking_context_url']
                                excluded.add(context)
                                self.store.append_event(run_id, {'kind': 'V4_NATIVE_COMPONENT_UNOBSERVED',
                                    'source_id': key, 'ranking_context_url': context, 'reason': str(exc),
                                    'completeness_status': 'UNKNOWN', 'source_success_not_proven': True})
                                component_failures[context] = str(exc)
                                commit()
                                continue
                            if not (descriptor.get('metadata') or {}).get('carousel'):
                                from .v4_authorization import saved_grid_binding
                                descriptor['ranking_context_binding']=saved_grid_binding(descriptor,source)
                                descriptor['request_payload_sha256']=hashlib.sha256(descriptor['payload'].encode('utf8')).hexdigest()
                            native_key = (descriptor['url'], descriptor['method'], descriptor.get('payload'))
                            if native_key in attempted:
                                if not (descriptor.get('metadata') or {}).get('carousel'):
                                    raise ValueError('V4_GRID_NATIVE_NO_PROGRESS')
                                self.store.append_event(run_id, {'kind': 'ACP_NO_PROGRESS', 'source_id': key})
                                excluded.add(descriptor['metadata']['ranking_context_url'])
                                continue
                            attempted.add(native_key)
                            _, saved, last = e._request(plan, authorization, transport, source, descriptor['url'],
                                              'ACP', started, last, descriptor=descriptor, page_key=key)
                            acp.append(self._acp(saved)); parsed = parse(); commit()
                        if not parsed['acp_pending']:
                            loading_stop_reason = 'OBSERVED_FINITE_METADATA_EXHAUSTED'
                        commit()  # Persist the reason; stopping does not imply completeness.
                    if (not cached and source['source_type'] in {'DEALS','AMAZON_HAUL'}
                            and parsed['page']['status']!='EXPLICIT_EMPTY'
                            and authorization['sources'][sid].get('allow_native_dynamic')):
                        dynamic_limit=plan.get('max_dynamic_requests_per_source',2)
                        for _ in range(max(0,dynamic_limit-(source_loading_count() if self.stage in {'T2','T3'} else 0))):
                            remaining_pages=self.remaining_page_work(plan,work,committed,url)
                            if plan['max_requests_per_run']-self.store.budget_counters(run_id)['requests']<=remaining_pages:
                                break
                            descriptor=transport.prepare_dynamic(source,receipt)
                            if descriptor is None:
                                self.store.append_event(run_id,{'kind':'V4_DYNAMIC_CONTRACT_UNOBSERVED','source_id':sid,
                                    'completeness_status':'UNKNOWN'})
                                break
                            _,saved,last=e._request(plan,authorization,transport,source,descriptor['url'],'ACP',
                                started,last,descriptor=descriptor,page_key=key)
                            dynamic_receipts.append(saved)
                            # Capture actual rendered DOM only after its native
                            # promise receives durable metered response bytes.
                            transport.session.page.wait_for_timeout(600)
                            body=transport.session.page.content()
                            from ..access.detector import detect_access_status
                            htmlref=self.store.save_html(body)
                            state=detect_access_status(200,body).value
                            if state!='NORMAL':
                                self.store.set_gate(state,run_id=run_id,reason='V4_RENDERED_LOADING_ACCESS_SIGNAL',
                                    evidence_path=htmlref['html_path'])
                                e._persist_shared_stop(plan,state,{'evidence_path':htmlref['html_path']})
                                raise ValueError('V4_RENDERED_LOADING_ACCESS_SIGNAL:'+state)
                            if isinstance(htmlref,dict):
                                path=htmlref['html_path'];sha=htmlref['sha256']
                            else:
                                path,sha=htmlref
                            frame_time=utc_text(e.clock())
                            rendered_frames.append({'path':str(path),'sha256':sha,'observed_at':frame_time})
                            derivation={'root_html_sha256':root_receipt['sha256'],
                                'root_observed_at':root_receipt['observed_at'],
                                'dynamic_receipt_references':[e._receipt_ref(x) for x in dynamic_receipts],
                                'local_ui_deliveries':list(transport.loading_events),
                                'rendered_frames':list(rendered_frames),
                                'rendered_observed_at':frame_time,'new_PAGE_request':False}
                            previous_count=len(parsed['observations'])
                            previous_links={r['normalized_product_url'] for r in parsed['observations'] if r.get('is_valid')}
                            receipt={**root_receipt,'html_path':str(path),'sha256':sha,
                                'observed_at':derivation['rendered_observed_at'],
                                'source_representation':'RENDERED_DOM_DERIVATION',
                                'document_html_path':None,'document_sha256':None,
                                'rendered_html_path':str(path),'rendered_sha256':sha}
                            response={**response,'text':body}
                            parsed=parse();commit()
                            self.store.append_event(run_id,{'kind':'V4_RENDERED_LOADING_FRAME','source_id':sid,
                                'previous_cards':previous_count,'actual_cards':len(parsed['observations']),
                                'rendered_derivation':derivation,'completeness_status':'UNKNOWN'})
                            if (not {r['normalized_product_url'] for r in parsed['observations'] if r.get('is_valid')}-previous_links
                                    and not getattr(transport,'has_pending_dynamic',lambda s:False)(source)):break
                        transport.cancel_dynamic()
                    self.store.append_event(run_id, {'kind': 'V4_DISCOVERED_NAVIGATION', 'source_id': sid, 'page_key': key,
                        'links': parsed['discovered_sources'], 'note': 'Discovery only, not visited or approved.'})
                    committed.add(url)
                    # A failed/unknown frame stops. It never becomes source-empty.
                    if (not parsed['observations'] and parsed['page']['status']!='EXPLICIT_EMPTY'
                            and not self.accept_navigation_frame(plan,source,parsed,receipt)):
                        raise ValueError('V4_SOURCE_FRAME_UNVERIFIED_SAVED_FOR_OFFLINE_DIAGNOSIS')
                    if self.stage in {'T2','T3'}:
                        scope_executed=True
                        commit()
                    self.after_page(plan,source,key,parsed)
                except (ValueError, OSError) as exc:
                    if self.continue_after_source_stop(str(exc)):
                        self.store.append_event(run_id, {'kind':'SOURCE_BUDGET_STOP','source_id':source['source_id'],
                            'reason':str(exc),'observed_at':utc_text(e.clock())})
                        continue
                    stop = str(exc)
                    self.store.append_event(run_id, {'kind': 'STOP', 'reason': stop, 'observed_at': utc_text(e.clock())})
                    break
            pages = self.store.pages(run_id); rows = list(self.store.iter_observations(run_id))
            qa_started = time.perf_counter()
            deferred=self.defer_central_review_to_capacity()
            quality = ({'central_full_dom_review_passed':False,'errors':[],
                'review_status':'PENDING_COMBINED_CAPACITY_REVIEW'} if deferred else
                audit_v4(pages, rows, plan['sources'], require_live=True))
            from .v4_coverage import loading_coverage
            loading=loading_coverage(pages,plan['sources']) if quality['central_full_dom_review_passed'] and self.stage!='T3' else None
            self.store.append_event(run_id, {'kind': 'V4_CENTRAL_DOM_REVIEW_DEFERRED' if deferred else 'V4_CENTRAL_DOM_REVIEW',
                    'duration_seconds': time.perf_counter() - qa_started, 'errors': quality['errors']})
            stop = stop or ('V4_'+self.stage+'_LOADING_SCOPE_EXECUTED' if loading and loading['all_loading_capabilities_observed']
                else 'V4_'+self.stage+'_LOADING_SCOPE_EXECUTED_INCOMPLETE_PROOFS')
            self.store.update_run(run_id, status='STOPPED', stop_reason=stop, qa=quality,
                    quality_gate_passed=bool(loading and loading['all_loading_capabilities_observed']
                        and len({p['source_id'] for p in pages if p['status']=='VERIFIED_WITH_PRODUCTS'})==8),
                    loading_verification=loading, finished_at=utc_text(e.clock()))
            return {'run': self.store.get_run(run_id), 'summary': self.store.summary(run_id), 'quality': quality}

    @staticmethod
    def _acp(receipt):
        return {**receipt, 'body': Path(receipt['html_path']).read_text(encoding='utf8'),
                'evidence_path': receipt['html_path'], 'evidence_sha256': receipt['sha256']}

    def defer_central_review_to_capacity(self):
        return False

    def _record_approval(self,plan,authorization):
        pass

    def accept_navigation_frame(self,plan,source,parsed,receipt):
        return False

    def continue_after_source_stop(self,reason):
        return False

    def after_page(self,plan,source,key,parsed):
        pass

    def remaining_page_work(self,plan,work,committed,current_url):
        return sum(u not in committed and u!=current_url for s,u in work)
