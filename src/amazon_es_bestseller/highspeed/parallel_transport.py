"""Three fixed windows in one persistent context, with shared admission.

The coordinator supplies a durable reservation and fresh admission callback
before navigation. This adapter never retries or replaces a stopped window.
"""
import asyncio
from urllib.parse import urlsplit
from .parallel_dispatch_gate import DispatchStopped
from ..access.detector import detect_access_status


class ThreeWindowTransport:
    def __init__(self, context, gate, *, admit, observed_stop, captured, offline_pages=None):
        self.context=context;self.gate=gate;self.admit=admit
        self.observed_stop=observed_stop;self.captured=captured
        self.offline_pages=offline_pages
        if offline_pages is not None and any(urlsplit(u).scheme!='https' or urlsplit(u).hostname not in
                {'snapshot-test.invalid','www.amazon.es','amazon.es'} for u in offline_pages):
            raise ValueError('LOCAL_FIXTURE_URLS_REQUIRED')
        self.pages={};self.active={};self.sent=set();self.disabled=set()

    async def open(self):
        initial=self.context.pages[0] if self.context.pages else await self.context.new_page()
        cdp=await self.context.new_cdp_session(initial)
        for worker in (1,2,3):
            async with self.context.expect_page(timeout=10000) as opened:
                await cdp.send('Target.createTarget',{'url':'about:blank','newWindow':True})
            page=await opened.value;self.pages[worker]=page
            page.on('response',self._response)
            def handler_for(w):
                async def handler(route):await self._route(w,route)
                return handler
            await page.route('**/*',handler_for(worker))
        return self

    def _stop(self, state):
        self.gate.stop()
        self.disabled.update((1,2,3))
        self.observed_stop(state)

    def _response(self,response):
        if response.request.resource_type=='document' and response.status in (403,429):
            self._stop(detect_access_status(response.status,'').value)

    async def _route(self,worker,route):
        req=route.request;item=self.active.get(worker)
        if worker in self.disabled or not item:
            await route.abort();return
        if req.resource_type in {'xhr','fetch'}:
            await route.abort();return
        if self.offline_pages is not None and req.resource_type!='document':
            await route.abort();return
        if req.resource_type!='document':
            await route.continue_();return
        if req.frame!=self.pages[worker].main_frame:
            # Saved Amazon documents can create incidental iframe documents.
            # Never send these unmetered requests. Their local rejection is
            # not an observed remote restriction or top-level redirect.
            await route.abort();return
        token=(worker,item['reservation']['request_id'])
        if (req.url!=item['url'] or req.method!='GET' or req.redirected_from is not None
                or token in self.sent):
            self._stop('UNREVIEWED_DOCUMENT:'+req.url);await route.abort();return
        async def send():
            self.sent.add(token)
            if self.offline_pages is None:await route.continue_()
            else:
                code,body=self.offline_pages[item['url']]
                await route.fulfill(status=code,body=body,content_type='text/html')
        try:
            await self.gate.dispatch(verify_reservation=lambda:self.admit(worker,item),send=send)
        except DispatchStopped:
            await route.abort()
        except Exception:
            self._stop('SEND_ADMISSION_FAILURE')
            await route.abort()
            raise

    async def capture(self,worker,item):
        if worker not in self.pages or worker in self.active or worker in self.disabled:
            raise ValueError('FIXED_AVAILABLE_WINDOW_REQUIRED')
        self.active[worker]=item
        try:
            response=await self.pages[worker].goto(item['url'],wait_until='domcontentloaded',timeout=45000)
            if response is None:raise ValueError('DOCUMENT_RESPONSE_REQUIRED')
            document=await response.text();rendered=await self.pages[worker].content()
            state=detect_access_status(response.status,document+'\n'+rendered).value
            if state!='NORMAL':self._stop(state)
            self.gate.note_completion()
            value={'status_code':response.status,'url':response.url,'text':document,
                'http_document_html':document,'rendered_dom_html':rendered,
                'source_representation':'HTTP_DOCUMENT_BODY','access_state':state,
                'final_url':response.url,'request_method':'GET'}
            # A synchronous durable callback completes before this slot can
            # return to the scheduler; journal errors close all dispatch.
            result=self.captured(worker,item,value)
            return result
        except BaseException:
            self._stop('CAPTURE_OR_JOURNAL_FAILURE')
            raise
        finally:self.active.pop(worker,None)
