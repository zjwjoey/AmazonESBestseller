"""Async Chromium adapter: one context, distinct windows and native root starts.

Instantiation does not launch a browser. The authorized entry point owns its
context/profile; offline verification supplies a new temporary test profile.
"""
from __future__ import annotations

import asyncio
import math
import time
from urllib.parse import urlsplit
from ..access.detector import detect_access_status


class ParallelBrowserCapture:
    def __init__(self,context,*,lanes=5,check_gate,timeout_ms=45000,localhost_only=False):
        self.context,self.lanes,self.check_gate=context,lanes,check_gate
        self.timeout_ms=timeout_ms
        self.localhost_only=localhost_only
        self.pages=[];self.window_ids=[];self.clients=[];self.active={}
        self.diagnostics=[]
        self.stop_event=None

    async def open(self):
        root=self.context.pages[0] if self.context.pages else await self.context.new_page()
        control=await self.context.new_cdp_session(root)
        self.pages=[root]
        try:
            for _ in range(self.lanes-1):
                target=(await control.send('Target.createTarget',{'url':'about:blank','newWindow':True}))['targetId']
                found=None
                for _ in range(100):
                    for page in self.context.pages:
                        if page in self.pages:continue
                        probe=await self.context.new_cdp_session(page)
                        try:
                            info=(await probe.send('Target.getTargetInfo'))['targetInfo']
                        finally:await probe.detach()
                        if info['targetId']==target:
                            found=page;break
                    if found is not None:break
                    await asyncio.sleep(.05)
                if found is None:raise RuntimeError('VISIBLE_WINDOW_TARGET_NOT_OBSERVED')
                self.pages.append(found)
            for lane,page in enumerate(self.pages):
                client=await self.context.new_cdp_session(page)
                self.clients.append(client)
                await client.send('Network.enable')
                window=(await client.send('Browser.getWindowForTarget'))['windowId']
                self.window_ids.append(window)
                await client.send('Browser.setWindowBounds',{'windowId':window,'bounds':{'windowState':'normal'}})
                frame=(await client.send('Page.getFrameTree'))['frameTree']['frame']['id']
                client.on('Network.requestWillBeSent',lambda event,lane=lane,frame=frame:self._native(lane,frame,event))
                await page.route('**/*',self._route_handler(lane))
                page.on('response',lambda response,lane=lane:self._response(lane,response))
            if len(set(self.window_ids))!=self.lanes:
                raise RuntimeError('DISTINCT_VISIBLE_WINDOWS_REQUIRED')
        finally:await control.detach()

    def _route_handler(self,lane):
        # Playwright supplies (route, request) to two-argument handlers. A
        # lambda with ``lane=lane`` as argument two accidentally replaces the
        # integer lane with a Request object and aborts every real navigation.
        async def handle(route):
            await self._route(lane,route)
        return handle

    def _native(self,lane,frame,event):
        active=self.active.get(lane)
        if not active or event.get('type')!='Document' or event.get('frameId')!=frame:return
        self.diagnostics.append({'event':'native_document','lane':lane,'request_id':event.get('requestId'),
                                 'request_url':(event.get('request') or {}).get('url'),
                                 'root_id':active.get('root_id')})
        request=event.get('request') or {}
        root_id=event.get('requestId')
        if active.get('root_id')==root_id:return  # same redirect chain / duplicate event
        if active.get('root_id'):
            active['restrict']('UNKNOWN');return
        if (active['stop'].is_set() or not active['goto_requested'] or request.get('method')!='GET'
                or request.get('url')!=active['task']['request_url'] or not root_id
                or not all(isinstance(event.get(k),(int,float)) and not isinstance(event.get(k),bool)
                           and math.isfinite(event[k]) and event[k]>0 for k in ('timestamp','wallTime'))):
            active['restrict']('UNKNOWN');return
        active['root_id']=root_id
        active['native_document_start']={k:event[k] for k in ('requestId','frameId','timestamp','wallTime')}
        active['native_document_start'].update(url=request['url'],method='GET')
        active['sent']()

    async def _route(self,lane,route):
        request=route.request
        if self.localhost_only and urlsplit(request.url).hostname not in ('127.0.0.1','localhost','::1'):
            await route.abort();return
        if self.stop_event is not None and self.stop_event.is_set():
            await route.abort();return
        if not request.is_navigation_request():
            await route.continue_();return
        active=self.active.get(lane)
        if (not active or active['stop'].is_set() or request.frame!=self.pages[lane].main_frame):
            self.diagnostics.append({'event':'route_abort_not_active','lane':lane,'url':request.url,
                                     'active':bool(active),'stop':bool(active and active['stop'].is_set())})
            await route.abort();return
        try:
            self.check_gate()
        except Exception:
            active['restrict']('UNKNOWN')
            await route.abort();return
        original=request
        while original.redirected_from:original=original.redirected_from
        if original.url!=active['task']['request_url'] or request.method!='GET':
            self.diagnostics.append({'event':'route_abort_scope','lane':lane,'url':request.url})
            active['restrict']('UNKNOWN');await route.abort();return
        if active.get('original_request') is None:active['original_request']=original
        if active['original_request'] is not original:
            self.diagnostics.append({'event':'route_abort_second_root','lane':lane,'url':request.url})
            active['restrict']('UNKNOWN');await route.abort();return
        await route.continue_()

    def _response(self,lane,response):
        active=self.active.get(lane)
        request=response.request
        if not active or not request.is_navigation_request() or request.frame!=self.pages[lane].main_frame:return
        original=request
        while original.redirected_from:original=original.redirected_from
        if original.url!=active['task']['request_url']:return
        active['document_responses'].append({'url':response.url,'status_code':response.status,
                                             'from_service_worker':response.from_service_worker})
        if response.status in (403,429):active['restrict']('BLOCKED' if response.status==403 else 'RATE_LIMITED')

    async def __call__(self,task,lane,sent,stop,restrict):
        from playwright.async_api import TimeoutError as NavigationTimeout
        page=self.pages[lane]
        active={'task':task,'sent':sent,'stop':stop,'restrict':restrict,
                'goto_requested':False,'document_responses':[]}
        self.active[lane]=active
        self.stop_event=stop
        status=None;error='';timed_out=False
        try:
            self.check_gate()
            if stop.is_set():raise RuntimeError('STOP_BEFORE_GOTO')
            active['goto_requested']=True
            try:
                response=await page.goto(task['request_url'],wait_until='domcontentloaded',timeout=self.timeout_ms)
                status=response.status if response else None
            except NavigationTimeout as exc:
                timed_out=True;error=str(exc)
            except Exception as exc:
                error=type(exc).__name__+': '+str(exc)
                restrict('UNKNOWN')
            # Keep six complete snapshots and a final sample >=3 seconds after
            # DOM readiness; only the last saved text enters the shared parser.
            body='';snapshot_count=0;start=time.perf_counter()
            for i in range(6):
                await asyncio.sleep(max(0,start+i*.6-time.perf_counter()))
                body=await page.content();snapshot_count+=1
                snapshot_state=detect_access_status(status,body).value
                if snapshot_state in ('BLOCKED','CHALLENGE','RATE_LIMITED'):
                    restrict(snapshot_state)
                    active['render_stop_state']=snapshot_state
                    break
            return {'html':body,'status_code':status,'request_url':task['request_url'],
                    'final_url':page.url,'document_start_observed':bool(active.get('root_id')),
                    'native_document_start':active.get('native_document_start'),
                    'document_responses':active['document_responses'],'timed_out':timed_out,
                    'error':error,'window_id':self.window_ids[lane],
                    'render_stop_state':active.get('render_stop_state'),
                    'render_snapshot_count':snapshot_count,'render_elapsed_seconds':time.perf_counter()-start}
        finally:self.active.pop(lane,None)
