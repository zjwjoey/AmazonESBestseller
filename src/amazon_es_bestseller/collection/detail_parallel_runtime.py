"""Single queue and evidence writer for controlled parallel detail execution.

Browser ownership and explicit authorization belong to the entry adapter.
All default local parser processes use the same current V2 callable.
"""
from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

from ..access.detector import AccessStopError,detect_access_status
from ..access.location import inspect_delivery_location
from ..identity import IDENTITY_MATCH
from ..multisource.storage import file_lock,digest,immutable_json,sealed
from .detail_dispatch import DetailDispatchScheduler
from .parallel_detail_store import ParallelDetailStore


def parse_detail_task(task,body,metadata,postal_code='28001'):
    from .detail_v2 import parse_detail_evidence_v2
    location=inspect_delivery_location(body)
    if location.is_spain is not True or location.postal_code!=postal_code:
        raise AccessStopError('SPAIN_DELIVERY_LOCATION_NOT_VERIFIED')
    record=parse_detail_evidence_v2(body,task['asin'],requested_url=task['request_url'],
        final_url=metadata.get('final_url') or '',ranking_context=task.get('ranking_context') or task)
    record['local_parser_pid']=os.getpid()
    return record


class ParallelDetailRunner:
    def __init__(self,root,plan,budget,*,capture,check_gate,stop_gate,parse=None,lanes=5,
                 postal_code='28001',process_parser=parse_detail_task,**scheduler_options):
        self.store=ParallelDetailStore(root,plan,budget)
        self.capture,self.check_gate,self.stop_gate=capture,check_gate,stop_gate
        self.parse=parse
        self.lanes=lanes
        self.postal_code=postal_code
        self.process_parser=process_parser
        self.parser_pids=[]
        self.access_state=None
        self.system_failure=None
        self.executors=[]
        self.commit_lock=asyncio.Lock()
        self.parse_seconds=[];self.save_seconds=[]
        self.scheduler=DetailDispatchScheduler(self.reserve,self.capture_task,
                                              lanes=lanes,**scheduler_options)

    def restrict(self,state):
        if state=='NORMAL':raise ValueError('CANNOT_RECOVER_ACCESS_GATE')
        self.scheduler.fail(AccessStopError(state),access=True)
        if self.access_state is None:
            self.access_state=state
            try:
                self.stop_gate(state)
            except Exception as exc:
                self.system_failure=exc
                raise

    async def reserve(self,task,lane):
        self.check_gate()
        self.store.reserve(task,lane)
        return task,lane

    async def commit(self,task,result):
        # One parent writes all terminal checkpoints. Parser processes never
        # receive a store, profile, gate or filesystem write capability.
        async with self.commit_lock:
            start=time.perf_counter()
            try:
                await asyncio.to_thread(self.store.finish,task,result)
            except Exception as exc:
                self.system_failure=exc
                self.scheduler.stop.set()
                raise
            self.save_seconds.append(time.perf_counter()-start)

    async def capture_task(self,token,sent):
        task,lane=token
        if self.scheduler.stop.is_set():
            await self.commit(task,{'status':'UNSENT','reason':'GLOBAL_STOP_BEFORE_NAVIGATION'})
            return
        try:
            self.check_gate()
        except Exception:
            await self.commit(task,{'status':'UNSENT','reason':'GATE_NOT_NORMAL_BEFORE_NAVIGATION'})
            raise
        observed=False
        def started():
            nonlocal observed
            sent();observed=True
        meta=await self.capture(task,lane,started,self.scheduler.stop,self.restrict)
        body=meta['html']
        if not isinstance(body,str):raise TypeError('CAPTURE_HTML_MUST_BE_TEXT')
        metadata={k:v for k,v in meta.items() if k!='html'}
        state=detect_access_status(meta.get('status_code'),body).value
        scoped=(observed and meta.get('document_start_observed') is True
                and meta.get('request_url')==task['request_url'])
        responses=meta.get('document_responses') or []
        native=meta.get('native_document_start') or {}
        normal_timeout=False
        if (scoped and state=='UNKNOWN' and meta.get('timed_out') is True
                and 'domcontentloaded' in str(meta.get('error') or '').lower()
                and native.get('url')==task['request_url'] and native.get('method')=='GET'
                and meta.get('final_url')==task['request_url']
                and responses and all(r.get('url')==task['request_url'] and r.get('status_code')==200 for r in responses)
                and not self.scheduler.stop.is_set()):
            location=inspect_delivery_location(body)
            normal_timeout=location.is_spain is True and location.postal_code==self.postal_code
        unsafe=not scoped or (state not in ('NORMAL','NETWORK_ERROR') and not normal_timeout)
        if unsafe:
            # Close admission synchronously before any fsync/thread await.
            # Evidence still drains before the shared Gate is published.
            self.scheduler.fail(AccessStopError(state),access=True)
        async with self.commit_lock:
            start=time.perf_counter()
            try:
                refs=await asyncio.to_thread(self.store.save_capture,task,body.encode('utf8'),metadata)
            except Exception as exc:
                self.system_failure=exc
                self.scheduler.stop.set()
                raise
            self.save_seconds.append(time.perf_counter()-start)
        result={'lane':lane,'group':'three' if lane<3 else 'two',**refs}
        if state in ('BLOCKED','CHALLENGE','RATE_LIMITED'):
            self.restrict(state)
            await self.commit(task,{**result,'status':'ACCESS_STOP','reason':state})
            return
        if not scoped or (state not in ('NORMAL','NETWORK_ERROR') and not normal_timeout):
            self.restrict('UNKNOWN')
            await self.commit(task,{**result,'status':'ACCESS_STOP','reason':'UNCONFIRMED_CAPTURE'})
            return
        if state=='NETWORK_ERROR':
            await self.commit(task,{**result,'status':'NETWORK_FAILED','reason':'HTTP_SERVER_ERROR'})
            return
        if meta.get('timed_out'):
            # A concrete normal main-document response, start and original URL
            # are required. A null-status timeout remains UNKNOWN and stops.
            await self.commit(task,{**result,'status':'NETWORK_FAILED','reason':'DOCUMENT_READY_TIMEOUT'})
            return
        parse_start=time.perf_counter()
        try:
            if self.parse is not None:
                record=await asyncio.to_thread(self.parse,task,body,metadata)
            else:
                record=await asyncio.get_running_loop().run_in_executor(
                    self.executors[lane],self.process_parser,task,body,metadata,self.postal_code)
        except AccessStopError as exc:
            self.restrict('UNKNOWN')
            await self.commit(task,{**result,'status':'ACCESS_STOP','reason':str(exc)})
            return
        except (OSError,MemoryError):
            raise
        except Exception as exc:
            # A broken parser process is a system condition, not a product.
            from concurrent.futures.process import BrokenProcessPool
            if isinstance(exc,BrokenProcessPool):raise
            await self.commit(task,{**result,'status':'PARSE_FAILED',
                                   'reason':type(exc).__name__+': '+str(exc)})
            return
        if not record.get('title_es_raw'):
            result.update(status='DETAIL_INVALID',reason='PRODUCT_TITLE_MISSING')
        elif (record.get('identity_status')!=IDENTITY_MATCH or record.get('resolved_asin')!=task['asin']
              or record.get('asin')!=task['asin']):
            result.update(status='IDENTITY_REVIEW',reason='STRONG_PRODUCT_IDENTITY_NOT_MATCHED',
                          identity_evidence=record.get('identity_evidence'))
        else:
            result.update(status='SUCCESS',record=record)
        self.parse_seconds.append(time.perf_counter()-parse_start)
        await self.commit(task,result)

    async def run(self):
        failure=None
        with file_lock(self.store.root/'parallel_owner.lock'):
            self.store.import_legacy_successes()
            pending=self.store.pending()
            self.check_gate()
            if self.parse is None and pending:
                self.executors=[ProcessPoolExecutor(max_workers=1,mp_context=get_context('spawn'))
                                for _ in range(self.lanes)]
                self.parser_pids=list(await asyncio.gather(*(asyncio.get_running_loop().run_in_executor(executor,os.getpid)
                                       for executor in self.executors)))
            try:
                await self.scheduler.run(pending)
            except Exception as exc:
                failure=exc
            finally:
                for executor in self.executors:executor.shutdown(wait=True)
            # Unresolved reservations after a persistence/system error remain
            # deliberately unresolved; do not manufacture terminal success.
            if self.system_failure is not None:raise self.system_failure
            if failure is not None and not isinstance(failure,AccessStopError):raise failure
            outcomes=self.store.outcomes()
            successes=sum(r['result']['status']=='SUCCESS' for r in outcomes)
            state=('ACCESS_STOP' if self.access_state or isinstance(failure,AccessStopError)
                   else 'DRAINED_WITH_ITEM_ERRORS' if len(outcomes)>successes else 'DRAINED')
            export=self.store.root/'parallel_exports'/digest(outcomes)
            immutable_json(export/'details.json',[row['result']['record'] for row in outcomes
                                                  if row['result']['status']=='SUCCESS'])
            immutable_json(export/'outcomes.json',sealed({'run_id':self.store.plan['run_id'],
                                                         'outcomes':outcomes}))
            claimed=set(self.scheduler.claimed)
            return {'state':state,
                    'successful':successes,'failed':len(outcomes)-successes,
                    'pending':len(self.store.plan['tasks'])-len(outcomes),
                    'used_requests':self.store.used_requests,
                    'capture_peak':self.scheduler.peak_active,'parser_contract':2,
                    'local_parser_pids':self.parser_pids,
                    'new_admissions':len(claimed),
                    'new_successful':sum(row['task']['task_id'] in claimed and row['result']['status']=='SUCCESS' for row in outcomes),
                    'details_export':str(export/'details.json'),
                    'parse_seconds':sum(self.parse_seconds),'save_seconds':sum(self.save_seconds)}
