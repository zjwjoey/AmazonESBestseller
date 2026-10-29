"""Three-window fixture transport with all network blocked. TEST_ONLY."""
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
from .parallel_dispatch_gate import DispatchStopped


class OfflineThreeWindowTransport:
    """Exercise three windows in one test profile, never target a real host."""
    def __init__(self, profile, fixtures, gate):
        self.profile=profile;self.fixtures=dict(fixtures);self.gate=gate
        if not self.fixtures or any(urlsplit(url).hostname!='snapshot-test.invalid' for url in self.fixtures):
            raise ValueError('TEST_ONLY_FIXTURE_HOST_REQUIRED')
        self.playwright=None;self.context=None;self.pages=[];self.window_ids=[];self.sent=[];self.blocked=[]

    async def __aenter__(self):
        self.playwright=await async_playwright().start()
        try:
            self.context=await self.playwright.chromium.launch_persistent_context(str(self.profile),headless=True,service_workers='block')
            async def route_handler(route):
                url=route.request.url
                if url not in self.fixtures:
                    self.blocked.append(url);await route.abort();return
                fixture=self.fixtures[url]
                async def send():
                    self.sent.append(url)
                    await route.fulfill(status=fixture['status'],content_type='text/html',body=fixture['body'])
                    if fixture['status'] in (403,429):self.gate.stop()
                try:await self.gate.dispatch(verify_reservation=lambda:True,send=send)
                except DispatchStopped:
                    self.blocked.append(url);await route.abort()
            await self.context.route('**/*',route_handler)
            initial=self.context.pages[0] if self.context.pages else await self.context.new_page()
            cdp=await self.context.new_cdp_session(initial)
            for _ in range(3):
                async with self.context.expect_page(timeout=10000) as opened:
                    result=await cdp.send('Target.createTarget',{'url':'about:blank','newWindow':True})
                self.pages.append(await opened.value)
                self.window_ids.append((await cdp.send('Browser.getWindowForTarget',{'targetId':result['targetId']}))['windowId'])
            if len(set(self.window_ids))!=3:raise ValueError('THREE_WINDOWS_REQUIRED')
            return self
        except BaseException:
            await self.__aexit__(None,None,None)
            raise

    async def fetch(self,slot,url):
        if slot not in (1,2,3) or url not in self.fixtures:raise ValueError('TEST_ONLY_SLOT_AND_URL_REQUIRED')
        response=await self.pages[slot-1].goto(url)
        return {'status_code':response.status,'url':url,'text':await self.pages[slot-1].content(),'origin':'OFFLINE_FIXTURE'}

    async def __aexit__(self,*args):
        if self.context is not None:
            await self.context.close();self.context=None
        if self.playwright is not None:
            await self.playwright.stop();self.playwright=None
