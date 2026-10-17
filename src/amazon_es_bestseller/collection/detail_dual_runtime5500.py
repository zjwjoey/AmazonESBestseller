"""Isolated, explicitly amended two in-flight DETAIL lanes, sharing one browser identity.

The existing serial entry point is unchanged. This entry point requires its own
current-code approval, immutable original authorization and mode amendment.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path

from ..access.detector import AccessStopError, detect_access_status
from ..multisource.storage import (atomic_json, digest, file_lock, immutable_bytes,
                                  immutable_json, read_sealed, sealed)
from .detail_bridge5500 import DetailBridge5500, SUCCESS, hashlib_sha, safe_url, utc
from .detail_budget5500 import total_conservative_page_holds as uncertain_setup_page_holds
from .detail_local_lanes5500 import parse_captured_detail
from .detail_dual5500 import TwoRequestScheduler, TARGET_START_INTERVAL_SECONDS
from .quick_selection import file_sha
from .browser_network_timing5500 import NetworkTimingRecorder


def checked_reference(ref):
    if file_sha(ref['path']) != ref['sha256']:
        raise ValueError('PARALLEL_REFERENCE_CHANGED')
    return json.loads(Path(ref['path']).read_text(encoding='utf8'))


def validate_parallel_mode(root, plan, auth):
    """Only concurrency changes. Pacing, identity, scope and cumulative caps do not."""
    base_ref = auth['parallel_base_authorization_reference']
    for reference in (base_ref, auth['parallel_execution_approval_reference']):
        if Path(reference['path']).resolve().parent != Path(root).resolve():
            raise ValueError('PARALLEL_REFERENCES_MUST_BE_IN_ORIGINAL_RUN')
    base = checked_reference(base_ref)
    amendment = checked_reference(auth['parallel_execution_approval_reference'])
    if (amendment.get('approved') is not True or not amendment.get('approved_by')
            or not amendment.get('explicit_user_instruction')
            or amendment.get('scope') != 'TWO_IN_FLIGHT_DETAIL_1000_TARGET_SAME_FROZEN_RUN'
            or amendment.get('run_id') != plan['run_id']
            or amendment.get('runtime_root') != str(Path(root).resolve())
            or amendment.get('plan_sha256') != digest(plan)
            or amendment.get('base_authorization_sha256') != base_ref['sha256']
            or amendment.get('code_sha') != auth.get('code_sha')
            or not amendment.get('browser_profile')
            or amendment.get('network_parallelism') != 2
            or auth.get('network_parallelism') != 2
            or auth.get('local_processing_lanes') != 2
            or auth.get('visible_browser_windows') != 2
            or auth.get('visible_browser_required') is not True
            or amendment.get('target_requests_per_hour') != 1000
            or auth.get('target_requests_per_hour') != 1000
            or amendment.get('max_uncommitted_tasks') != 3
            or auth.get('max_uncommitted_tasks') != 3
            or amendment.get('target_start_interval_seconds') != TARGET_START_INTERVAL_SECONDS):
        raise ValueError('EXPLICIT_CURRENT_PARALLEL_AMENDMENT_REQUIRED')
    invariants = ('run_id', 'scope', 'runtime_root', 'plan_sha256', 'starts_at',
        'deadline', 'request_deadline', 'max_requests', 'max_page_requests',
        'max_other_requests', 'max_attempts_per_task', 'request_start_interval_seconds',
        'request_budget_reservation_reference', 'delivery_setup_reference',
        'post_UI_first_detail_recheck_reference', 'reviewed_identity_quarantines_reference')
    for key in invariants:
        if auth.get(key) != base.get(key):
            raise ValueError('PARALLEL_CHANGED_ORIGINAL_CONTROL: ' + key)
    permitted = {'code_sha','ci_run_id','ci_success_evidence','offline_acceptance_reference',
        'approved_by','approval_id','reviewed_at','network_parallelism','local_processing_lanes',
        'visible_browser_windows','visible_browser_required','target_requests_per_hour','max_uncommitted_tasks',
        'parallel_base_authorization_reference','parallel_execution_approval_reference','family_identity_policy_reference'}
    for key in set(base) | set(auth):
        if key not in permitted and auth.get(key) != base.get(key):
            raise ValueError('PARALLEL_CHANGED_INHERITED_AUTHORIZATION: ' + key)
    if (auth['request_start_interval_seconds'] < 3
            or amendment.get('request_start_interval_seconds') != auth['request_start_interval_seconds']
            or amendment.get('no_successful_task_replay') is not True):
        raise ValueError('ORIGINAL_PACING_AND_CHECKPOINTS_REQUIRED')
    from .detail_family_identity5500 import read_policy
    read_policy(root,plan,auth)
    return amendment


def pending_tasks(bridge, auth):
    from .identity_quarantine5500 import reviewed_identity_quarantines
    reviewed = reviewed_identity_quarantines(bridge.root, bridge.plan, auth)
    pending = []
    for task in bridge.plan['tasks']:
        prior = bridge.outcomes.get(task['task_id'])
        if prior:
            if prior['status'] in SUCCESS:
                bridge.verify_saved_success(task, prior)
            elif task['task_id'] not in reviewed:
                raise ValueError('UNRESOLVED_TASK_BLOCKS_PARALLEL: ' + task['task_id'])
        else:
            pending.append(task)
    return pending


def full_qa_reserve(root, total):
    proof=read_sealed(Path(root)/'T2_acceptance.json')
    if proof.get('passed') is not True or proof.get('success_count')!=100:
        raise ValueError('REAL_T2_AUDIT_MEASUREMENT_REQUIRED')
    elapsed=proof.get('independent_audit_elapsed_seconds')
    if not isinstance(elapsed,(int,float)) or elapsed<=0:
        raise ValueError('REAL_T2_AUDIT_MEASUREMENT_REQUIRED')
    return max(3600,elapsed/100*total*1.2+900)


class ParallelCollector:
    """One asyncio owner writes receipts/checkpoints; two independent pages await I/O."""
    def __init__(self, bridge, auth, *, qa_seconds=3600):
        self.bridge, self.auth = bridge, auth
        self.root, self.plan = bridge.root, bridge.plan
        from .detail_family_identity5500 import read_policy
        self.family_policy = read_policy(self.root,self.plan,auth)
        self.scheduler = TwoRequestScheduler(self.reserve, self.capture,
            interval=max(TARGET_START_INTERVAL_SECONDS, auth['request_start_interval_seconds']),
            drain=self.drain_parsers)
        self.browser_network = NetworkTimingRecorder()
        self.pages, self.windows, self.tokens = [], [], {}
        self.executors = []
        self.commit_order, self.results = [], {}
        self.all_tokens = {}
        self.parse_jobs = set()
        self.admission = asyncio.Semaphore(3)
        self.peak_uncommitted = 0
        self.receipts = []
        self.restriction = None
        self.persistence_failure = None
        if qa_seconds<3600:raise ValueError('FULL_QA_RESERVE_CANNOT_BE_LOWERED')
        self.qa_seconds=qa_seconds

    def check_qa_clock(self):
        remaining=(datetime.fromisoformat(self.auth['deadline'])-datetime.now(timezone.utc)).total_seconds()
        if remaining<=self.qa_seconds+60:
            raise ValueError('FULL_QA_RESERVE_REACHED_STOP_NEW_REQUESTS')

    def signal_access(self, reason):
        self.scheduler.fail(AccessStopError('DETAIL_ACCESS_STOP ' + reason), access=True)

    def check_dispatch_boundary(self):
        # A reservation can wait for pacing. Re-read both live Gates and clocks
        # immediately before goto; do not rely on the earlier authorization check.
        self.bridge.gates_normal()
        self.check_qa_clock()
        at = datetime.now(timezone.utc)
        if not datetime.fromisoformat(self.auth['starts_at']) <= at < datetime.fromisoformat(
                self.auth.get('request_deadline') or self.auth['deadline']):
            raise ValueError('DETAIL_AUTHORIZATION_OUTSIDE_TIME')

    async def reserve(self, task, lane):
        # Two browser lanes plus at most one already-persisted local backlog.
        # A global stop interrupts admission even if persistence has failed.
        acquire = asyncio.create_task(self.admission.acquire())
        stopped = asyncio.create_task(self.scheduler.stop.wait())
        try:
            await asyncio.wait([acquire, stopped], return_when=asyncio.FIRST_COMPLETED)
            if self.scheduler.stop.is_set():
                if acquire.done() and not acquire.cancelled(): self.admission.release()
                else: acquire.cancel()
                raise RuntimeError('GLOBAL_STOP_BEFORE_ADMISSION')
            await acquire
        finally:
            stopped.cancel()
        try:
            return await self.reserve_admitted(task, lane)
        except BaseException:
            self.admission.release()
            raise

    async def reserve_admitted(self, task, lane):
        self.check_qa_clock()
        await asyncio.to_thread(self.bridge.validate_authorization, self.auth)
        validate_parallel_mode(self.root, self.plan, self.auth)
        if self.scheduler.stop.is_set():
            raise RuntimeError('GLOBAL_STOP_BEFORE_RESERVATION')
        rows = [read_sealed(p) for p in (self.root/'receipts').glob('*.json')]
        held = uncertain_setup_page_holds(self.root, self.plan, self.auth)
        if (len(rows)+held >= self.auth['max_requests']
                or sum(r.get('kind', 'PAGE') == 'PAGE' for r in rows)+held >= self.auth['max_page_requests']):
            raise ValueError('DETAIL_CUMULATIVE_BUDGET_EXHAUSTED')
        # All current RESERVED rows must belong to this dispatcher, never a
        # second worker or a reservation abandoned by a previous process.
        owned = {str(t['rp']) for t in self.tokens.values()}
        if any(r['state'] == 'RESERVED' and str(p) not in owned
               for p, r in ((p, read_sealed(p)) for p in (self.root/'receipts').glob('*.json'))):
            raise ValueError('FOREIGN_OR_ABANDONED_RESERVATION')
        # Run both full validations inside the existing receipt pacing window.
        # The scheduler separately enforces 3.6 seconds between real starts;
        # boundary Gates and clocks are still re-read immediately before goto.
        await asyncio.to_thread(self.bridge.validate_authorization, self.auth)
        if rows:
            last = max(datetime.fromisoformat(r['started_at']).timestamp() for r in rows)
            await asyncio.sleep(max(0, last+self.auth['request_start_interval_seconds']-time.time()))
        if self.scheduler.stop.is_set():raise RuntimeError('GLOBAL_STOP_AFTER_PACING')
        self.check_qa_clock()
        slot = self.root/'tasks'/task['task_id']
        if (slot/'checkpoint.json').exists():raise ValueError('TASK_ALREADY_HAS_CHECKPOINT')
        rp = self.root/'receipts'/(task['task_id']+'_1.json')
        receipt = dict(run_id=self.plan['run_id'], task_id=task['task_id'], kind='PAGE',
            request_url=task['request_url'], started_at=utc(), state='RESERVED',
            plan_sha256=digest(self.plan), authorization_sha256=digest(self.auth),
            request_role='DETAIL', attempt=1, parallel_lane=lane,
            execution_mode='DUAL_1000_TARGET', target_requests_per_hour=1000)
        immutable_json(rp, sealed(receipt))
        atomic_json(slot/'checkpoint.json', sealed(dict(status='RUNNING', task_id=task['task_id'],
            candidate_id=task['candidate_id'], run_id=self.plan['run_id'], receipt_path=str(rp))))
        token = dict(task=task, lane=lane, rp=rp, slot=slot, receipt=receipt,
                     history=[], sent=False, navigation_requested=False, committed=asyncio.Event())
        self.tokens[lane] = token
        self.all_tokens[task['task_id']] = token
        self.peak_uncommitted = max(self.peak_uncommitted, len(self.all_tokens))
        if len(self.all_tokens) > 3: raise RuntimeError('LOCAL_BACKLOG_BOUND_EXCEEDED')
        self.commit_order.append(task['task_id'])
        return token

    async def route(self, route):
        request = route.request
        if self.scheduler.stop.is_set():return await route.abort()
        if request.resource_type in {'image', 'media', 'font', 'xhr', 'fetch'}:
            return await route.abort()
        if request.is_navigation_request():
            initial = request
            while initial.redirected_from:initial = initial.redirected_from
            token = next((self.tokens.get(i) for i, p in enumerate(self.pages)
                          if request.frame == p.main_frame), None)
            if (not token or not token.get('navigation_requested',True) or token.get('navigation_completed') or request.method != 'GET' or not safe_url(request.url)
                    or initial.url != token['task']['request_url']):
                return await route.abort()
            if request.redirected_from is None:
                if token.get('initial_request') is not None and token['initial_request'] is not request:
                    return await route.abort()
                if token.get('root_route_seen'):return await route.abort()
                token['root_route_seen']=True
            self.mark_document_start(token,request,'FIRST_OBSERVED_SCOPED_MAIN_DOCUMENT_ROUTE')
        return await route.continue_()

    def mark_document_start(self, token, request, kind):
        initial=request
        while initial.redirected_from:initial=initial.redirected_from
        if token['sent']:
            # Native CDP may precede Playwright's route/request event. Bind the
            # actual original Request object without acknowledging a second start.
            if token.get('native_document_request_id') and token.get('initial_request') is None:
                token['initial_request']=initial
            return
        token['initial_request']=initial
        token['sent']=True
        token['receipt']['document_started_at']=utc()
        token['receipt']['document_start_evidence_kind']=kind
        token['receipt']['initial_request_route_observed']=(kind=='FIRST_OBSERVED_SCOPED_MAIN_DOCUMENT_ROUTE' and request.redirected_from is None)
        token['receipt']['physical_wire_start_not_independently_observed']=True
        token['notify_sent']()

    def observe_native_request(self, lane, main_frame, event):
        """A real, reserved root CDP request event, never a response-derived start."""
        token=self.tokens.get(lane);request=event.get('request',{})
        if (not token or self.scheduler.stop.is_set() or not token.get('navigation_requested')
                or token.get('navigation_completed') or event.get('type')!='Document'
                or event.get('frameId')!=main_frame or request.get('method')!='GET'
                or event.get('redirectResponse') or request.get('url')!=token['task']['request_url']
                or not safe_url(request.get('url','')) or not event.get('requestId')
                or token['receipt'].get('state')!='RESERVED'
                or token['receipt'].get('task_id')!=token['task']['task_id']):
            return
        for key in ('timestamp','wallTime'):
            value=event.get(key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:return
        prior=token.get('native_document_request_id')
        if prior:
            if prior!=event['requestId']:
                self.scheduler.fail(ValueError('UNAPPROVED_SECOND_NATIVE_ROOT_DOCUMENT'))
            return
        token['native_document_request_id']=event['requestId']
        token['receipt']['native_document_start']=dict(requestId=event['requestId'],frameId=main_frame,
            url=request['url'],method='GET',timestamp=event['timestamp'],wallTime=event['wallTime'])
        if token['sent']:return
        token['sent']=True
        token['receipt'].update(document_started_at=utc(),
            document_start_evidence_kind='BROWSER_CDP_SCOPED_MAIN_DOCUMENT_REQUEST_EVENT',
            initial_request_route_observed=False,physical_wire_start_not_independently_observed=True)
        token['notify_sent']()

    async def configure_page(self, lane, page):
        # Page guards also cover pages created through CDP newWindow. Retain
        # the context guard as a fail-closed fallback for unexpected targets.
        await page.route('**/*', self.route)
        page.on('response', lambda response, i=lane:self.observe(i,response))
        page.on('request', lambda request, i=lane:self.observe_request(i,request,'REQUEST'))
        page.on('requestfailed', lambda request, i=lane:self.observe_request(i,request,'REQUEST_FAILED'))

    def observe_request(self, lane, request, kind):
        token=self.tokens.get(lane)
        if not token or request.frame!=self.pages[lane].main_frame or request.resource_type!='document':return
        token['receipt'].setdefault('document_request_events',[]).append(dict(
            event=kind,url=request.url,observed_at=utc(),
            is_navigation_request=request.is_navigation_request(),
            redirected_from_url=request.redirected_from.url if request.redirected_from else None,
            failure=getattr(request,'failure',None) if kind=='REQUEST_FAILED' else None))
        initial=request
        while initial.redirected_from:initial=initial.redirected_from
        if (kind=='REQUEST' and not self.scheduler.stop.is_set()
                and token.get('navigation_requested',True) and request.is_navigation_request()
                and request.method=='GET' and safe_url(request.url)
                and initial.url==token['task']['request_url']):
            # Real browser request events are available even where Chromium
            # does not call a route handler for a cached/redirected document.
            # This never infers a start from a later response or manufactures HTTP.
            self.mark_document_start(token,request,'PLAYWRIGHT_SCOPED_MAIN_DOCUMENT_REQUEST_EVENT')

    def observe(self, lane, response):
        request = response.request
        token = self.tokens.get(lane)
        if response.status in {403, 429}:
            self.signal_access(str(response.status))
        if token and (response.status in {403,429} or
                request.resource_type == 'document' and request.frame == self.pages[lane].main_frame):
            row = dict(url=response.url, status_code=response.status, observed_at=utc())
            token['history'].append(row)
            if 'document_response_at' not in token['receipt']:
                token['receipt']['document_response_at'] = row['observed_at']

    async def capture(self, token, sent):
        try:
            await self.capture_and_commit(token, sent)
        except Exception as exc:
            self.scheduler.fail(exc)
            if not token['committed'].is_set():
                try:
                    receipt=read_sealed(token['rp'])
                    if receipt['state']=='RESERVED':
                        receipt.update(state='UNKNOWN',status_code=None,finished_at=utc(),error=repr(exc))
                        atomic_json(token['rp'],sealed(receipt),backup=True)
                    self.results[token['task']['task_id']]=(token['task'],dict(
                        status='CAPTURE_FAILED',reason='PARALLEL_CAPTURE_EXCEPTION',
                        receipt_path=str(token['rp']),error=repr(exc)))
                    self.commit_ready()
                except Exception as persistence_error:
                    self.persistence_failure=persistence_error
            raise

    async def capture_and_commit(self, token, sent):
        task, lane, page = token['task'], token['lane'], self.pages[token['lane']]
        token['notify_sent'] = sent
        html, status, error = '', None, None
        try:
            if self.scheduler.stop.is_set():raise RuntimeError('RESERVED_BUT_NOT_SENT_GLOBAL_STOP')
            self.check_dispatch_boundary()
            token['navigation_requested']=True
            response = await page.goto(task['request_url'], wait_until='domcontentloaded', timeout=45000)
            token['navigation_completed']=True
            status = response.status if response else None
            html = await page.content()
            access = detect_access_status(status, html).value
            if access != 'NORMAL':self.signal_access(access)
            else:
                # Same full three-second rendering buffer as the serial runtime;
                # other lanes may await independent responses during this time.
                for _ in range(6):
                    await asyncio.sleep(.5)
                    html = await page.content()
                    access = detect_access_status(status, html).value
                    if access != 'NORMAL':
                        self.signal_access(access)
                        break
        except Exception as exc:
            error = type(exc).__name__+': '+str(exc)
            try:html = await page.content()
            except Exception:pass
        access = detect_access_status(status, html).value
        restricted = next((r for r in token['history'] if r['status_code'] in {403,429}), None)
        if restricted:access = detect_access_status(restricted['status_code'], html).value
        elif error:access = 'UNKNOWN'
        body = html.encode('utf8'); hp = token['slot']/'html'/(hashlib_sha(body)+'.html')
        immutable_bytes(hp, body)
        receipt = token['receipt']
        receipt.update(state=access, status_code=status, final_url=page.url, finished_at=utc(),
            document_start_event_observed=token['sent'],
            browser_network_timing=self.browser_network.snapshot(token),
            html_path=str(hp), html_sha256=file_sha(hp), error=error, document_responses=token['history'])
        atomic_json(token['rp'], sealed(receipt), backup=True)
        meta = dict(status_code=status, requested_url=task['request_url'], final_url=page.url,
            access_state=access, collected_at=receipt['finished_at'], receipt_path=str(token['rp']),
            visible_window_lane=lane, network_parallelism=2)
        mp = token['slot']/'response_metadata.json'; immutable_json(mp, meta)
        evidence = dict(html_path=str(hp), html_sha256=file_sha(hp), receipt_path=str(token['rp']),
            receipt_sha256=file_sha(token['rp']), response_metadata_path=str(mp),
            response_metadata_sha256=file_sha(mp), local_execution_authorization_sha256=digest(self.auth))
        if access != 'NORMAL':
            self.signal_access(access)
            # The first observed restricted capture owns the Gate. Later
            # aborted captures retain their own evidence without erasing it.
            if self.restriction is None or (self.restriction['state']=='UNKNOWN' and access!='UNKNOWN'):
                self.restriction = dict(state=access, run_id=self.plan['run_id'],
                    receipt_path=str(token['rp']), observed_at=utc())
                for gp in (self.root/'access_gate.json', Path(self.plan['shared_gate_path'])):
                    old = read_sealed(gp)
                    # Preserve a pre-existing specific restriction if this token
                    # was blocked before navigation and has no observed HTTP.
                    if old.get('state') == 'NORMAL' or old.get('state') == 'UNKNOWN' and access != 'UNKNOWN':
                        atomic_json(gp, sealed({**self.restriction, 'previous_gate':old}), backup=True)
            result = dict(status='ACCESS_STOP', access_state=access)
            self.results[task['task_id']] = (task, {**result, **evidence})
            self.commit_ready()
        else:
            # Release only the browser lane after raw/receipt/meta are durable.
            # Parsing still owns a bounded admission slot until FIFO commit.
            job = asyncio.create_task(self.parse_and_commit(token, html, meta, evidence))
            self.parse_jobs.add(job)
            job.add_done_callback(self.parse_jobs.discard)

    async def parse_and_commit(self, token, html, meta, evidence):
        task, lane = token['task'], token['lane']
        try:
            try:
                result = await asyncio.wrap_future(self.executors[lane].submit(
                    parse_captured_detail, task, html, meta, bool(self.auth.get('delivery_setup_reference')), self.family_policy))
            except Exception as exc:
                result = dict(status='PARSE_FAILED', reason='LOCAL_PARSER_EXCEPTION', error=repr(exc))
            if result['status'] != 'SUCCESS':
                self.scheduler.fail(ValueError('DETAIL_SYSTEMATIC_SAFETY_REVIEW_REQUIRED: '+result['status']))
            self.results[task['task_id']] = (task, {**result, **evidence})
            self.commit_ready()
        except Exception as exc:
            self.persistence_failure = exc
            self.scheduler.fail(exc)

    async def drain_parsers(self):
        while self.parse_jobs:
            jobs = tuple(self.parse_jobs)
            await asyncio.gather(*jobs)
            # gather of already-completed tasks may return without yielding to
            # their call_soon discard callbacks. Do not spin waiting for them.
            self.parse_jobs.difference_update(jobs)
        if self.persistence_failure:
            raise RuntimeError('PERSISTENCE_FAILED_REQUIRES_OFFLINE_REVIEW') from self.persistence_failure
        if self.commit_order:
            raise RuntimeError('CAPTURE_EVIDENCE_NOT_FULLY_COMMITTED')

    def commit_ready(self):
        while self.commit_order and self.commit_order[0] in self.results:
            task_id = self.commit_order.pop(0)
            task, result = self.results.pop(task_id)
            self.bridge.save_result(task, result)
            token=self.all_tokens.pop(task_id)
            token['committed'].set()
            self.admission.release()

    async def open_windows(self, context):
        first = await context.new_page()
        self.pages.append(first)
        control = await context.new_cdp_session(first)
        for _ in range(1):
            target = (await control.send('Target.createTarget', {'url':'about:blank','newWindow':True}))['targetId']
            found = None
            for _ in range(100):
                for page in context.pages:
                    if page in self.pages:continue
                    cdp = await context.new_cdp_session(page)
                    info = (await cdp.send('Target.getTargetInfo'))['targetInfo']
                    await cdp.detach()
                    if info['targetId']==target:found=page;break
                if found:break
                await asyncio.sleep(.05)
            if not found:raise RuntimeError('VISIBLE_WINDOW_NOT_OBSERVED')
            self.pages.append(found)
        await control.detach()
        for lane,page in enumerate(self.pages):
            await self.configure_page(lane,page)
            cdp=await context.new_cdp_session(page)
            frame=(await cdp.send('Page.getFrameTree'))['frameTree']['frame']['id']
            cdp.on('Network.requestWillBeSent',lambda event,i=lane,f=frame:self.observe_native_request(i,f,event))
            window=(await cdp.send('Browser.getWindowForTarget'))['windowId']
            self.windows.append(window)
            await cdp.send('Browser.setWindowBounds', {'windowId':window,'bounds':{'windowState':'normal'}})
            await cdp.send('Browser.setWindowBounds', {'windowId':window,
                'bounds':{'left':lane*600,'top':0,'width':600,'height':900}})
            await self.browser_network.attach(cdp, lane, lambda i=lane:self.tokens.get(i))
        if len(set(self.windows))!=2:raise RuntimeError('TWO_DISTINCT_WINDOWS_REQUIRED')

    async def run(self, context, tasks):
        if any(read_sealed(p)['state']=='RESERVED' for p in (self.root/'receipts').glob('*.json')):
            raise ValueError('AMBIGUOUS_IN_FLIGHT_REQUEST_REQUIRES_REVIEW')
        await context.route('**/*', self.route)
        await self.open_windows(context)
        self.executors=[ProcessPoolExecutor(max_workers=1,mp_context=get_context('spawn')) for _ in range(2)]
        atomic_json(self.root/'worker_status.json', dict(phase='FINAL_COLLECTING', updated_at=utc(),
            code_sha=self.auth['code_sha'], visible_browser=True, visible_browser_windows=2,
            visible_window_ids=self.windows, network_parallelism=2, local_processing_lanes=2,
            authorization_sha256=digest(self.auth),
            full_QA_reserve_seconds=self.qa_seconds,
            execution_mode='DUAL_1000_TARGET', target_requests_per_hour=1000,
            max_uncommitted_tasks=3,
            effective_dispatch_interval_seconds=self.scheduler.interval,
            original_global_start_interval_seconds=self.auth['request_start_interval_seconds']))
        try:
            await self.scheduler.run(tasks)
        finally:
            self.commit_ready()
            for pool in self.executors:pool.shutdown(wait=True)
            await self.browser_network.close()
            if self.commit_order:raise RuntimeError('CAPTURE_EVIDENCE_NOT_FULLY_COMMITTED')


async def run(root, authorization, profile):
    from playwright.async_api import async_playwright
    root=Path(root);ap=Path(authorization)
    if ap.resolve().parent!=root.resolve():raise ValueError('AUTHORIZATION_MUST_BE_IN_RUN')
    plan=json.loads((root/'detail_plan_5500.json').read_text(encoding='utf8'))
    auth=json.loads(ap.read_text(encoding='utf8'))
    bridge=DetailBridge5500(root,plan)
    bridge.validate_authorization(auth)
    amendment=validate_parallel_mode(root,plan,auth)
    if Path(profile).resolve()!=Path(amendment['browser_profile']).resolve():
        raise ValueError('ORIGINAL_APPROVED_BROWSER_PROFILE_REQUIRED')
    if (subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()!=auth['code_sha']
            or subprocess.check_output(['git','status','--porcelain'],text=True).strip()):
        raise ValueError('APPROVED_CLEAN_CODE_REQUIRED')
    bridge.validate_stage('T1',30,auth=auth);bridge.validate_stage('T2',100,auth=auth)
    from .delivery_setup5500 import verify_setup
    verify_setup(root,plan,auth)
    tasks=pending_tasks(bridge,auth)
    with file_lock(root/'controller.lock'),file_lock(root/'collector.lock'),file_lock(Path(plan['shared_gate_path']).parent/'highspeed_live.lock'):
        async with async_playwright() as pw:
            context=await pw.chromium.launch_persistent_context(str(profile),headless=False,service_workers='block')
            collector=ParallelCollector(bridge,auth,qa_seconds=full_qa_reserve(root,len(plan['tasks'])))
            try:
                await collector.run(context,tasks)
                from .detail5500_runtime import audit_stage
                audit_stage(bridge,'FINAL',len(plan['tasks']),auth)
                from .quick5500_cli import export
                export(root)
                immutable_json(root/'detail_run_manifest.json',dict(run_id=plan['run_id'],completed_at=utc(),
                    plan_sha256=digest(plan),authorization_sha256=file_sha(ap),code_sha=auth['code_sha'],
                    final_acceptance_sha256=file_sha(root/'FINAL_acceptance.json'),progress=bridge.progress()))
                atomic_json(root/'worker_status.json',dict(phase='COMPLETE',updated_at=utc()))
            except Exception as exc:
                from .quick5500_cli import export
                export(root)
                atomic_json(root/'worker_status.json',dict(phase='PAUSED',updated_at=utc(),
                    error=type(exc).__name__+': '+str(exc),network_parallelism=2,visible_browser=True))
                if collector.scheduler.access_failure:
                    marker=root/'manual_assistance.close.requested'
                    original=marker.read_bytes() if marker.exists() else None
                    atomic_json(root/'worker_status.json',dict(phase='WAITING_MANUAL_ASSISTANCE',
                        updated_at=utc(),new_network_requests_blocked=True,automatic_resume=False,
                        human_network_handoff_requires_review=True,network_parallelism=2,visible_browser=True))
                    while (any(not p.is_closed() for p in collector.pages)
                            and datetime.now(timezone.utc)<datetime.fromisoformat(auth['deadline'])):
                        if marker.exists() and marker.read_bytes()!=original:break
                        await asyncio.sleep(1)
                raise
            finally:
                await context.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True);parser.add_argument('--authorization',required=True)
    parser.add_argument('--profile',required=True)
    args=parser.parse_args()
    asyncio.run(run(args.root,args.authorization,args.profile))


if __name__=='__main__':main()
