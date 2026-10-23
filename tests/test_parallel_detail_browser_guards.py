import asyncio
from amazon_es_bestseller.collection.detail_parallel_browser import ParallelBrowserCapture


def adapter():
    capture=ParallelBrowserCapture(None,check_gate=lambda:None)
    starts=[];restrictions=[]
    capture.active[0]={'task':{'request_url':'https://www.amazon.es/dp/B000000001'},
        'goto_requested':True,'stop':asyncio.Event(),'sent':lambda:starts.append(True),
        'restrict':restrictions.append}
    return capture,starts,restrictions


def event(**changes):
    value={'type':'Document','frameId':'main','requestId':'root',
        'timestamp':10.0,'wallTime':100.0,
        'request':{'url':'https://www.amazon.es/dp/B000000001','method':'GET'}}
    value.update(changes);return value


def test_native_root_duplicate_and_redirect_are_counted_once():
    capture,starts,errors=adapter()
    capture._native(0,'main',event())
    capture._native(0,'main',event())
    capture._native(0,'main',event(request={'url':'https://www.amazon.es/redirect','method':'GET'}))
    assert len(starts)==1 and not errors
    assert capture.active[0]['native_document_start']['requestId']=='root'


def test_missing_native_timestamp_foreign_frame_post_and_second_root_refused():
    for bad in (event(timestamp=None),event(request={'url':'https://www.amazon.es/dp/B000000001','method':'POST'})):
        capture,starts,errors=adapter();capture._native(0,'main',bad)
        assert starts==[] and errors==['UNKNOWN']
    capture,starts,errors=adapter()
    capture._native(0,'main',event(frameId='iframe'))
    assert not starts
    capture._native(0,'main',event())
    capture._native(0,'main',event(requestId='reload'))
    assert len(starts)==1 and errors==['UNKNOWN']


def test_route_callback_keeps_the_bound_integer_lane():
    async def case():
        capture,_,_=adapter();seen=[]
        async def route(lane,value):seen.append((lane,value))
        capture._route=route
        value=object()
        await capture._route_handler(3)(value)
        assert seen==[(3,value)]
    asyncio.run(case())


def test_render_challenge_is_observed_before_waiting_for_remaining_samples():
    from types import SimpleNamespace
    async def case():
        class Page:
            url='https://www.amazon.es/dp/B000000001'
            calls=0
            async def goto(self,*args,**kwargs):return SimpleNamespace(status=200)
            async def content(self):
                self.calls+=1
                return '<html>'+('x'*10000)+'Robot Check</html>'
        capture=ParallelBrowserCapture(None,lanes=1,check_gate=lambda:None)
        page=Page();capture.pages=[page];capture.window_ids=[1]
        stop=asyncio.Event();signals=[]
        def restrict(state):signals.append(state);stop.set()
        result=await capture({'request_url':page.url},0,lambda:None,stop,restrict)
        assert signals==['CHALLENGE'] and stop.is_set()
        assert page.calls==1 and result['render_snapshot_count']==1
    asyncio.run(case())


def test_same_native_redirect_chain_cannot_leave_amazon_scope():
    capture,starts,errors=adapter()
    capture._native(0,'main',event())
    capture._native(0,'main',event(request={'url':'https://foreign.invalid/dp/B000000001','method':'GET'}))
    assert len(starts)==1 and errors==['UNKNOWN']


def test_redirect_route_refuses_foreign_document_before_continue():
    from types import SimpleNamespace
    async def case():
        capture,starts,errors=adapter()
        frame=object();capture.pages=[SimpleNamespace(main_frame=frame)]
        original=SimpleNamespace(url='https://www.amazon.es/dp/B000000001',redirected_from=None)
        request=SimpleNamespace(url='https://foreign.invalid/dp/B000000001',method='GET',
            redirected_from=original,frame=frame,is_navigation_request=lambda:True)
        calls=[]
        class Route:
            async def abort(self):calls.append('abort')
            async def continue_(self):calls.append('continue')
        route=Route();route.request=request
        await capture._route(0,route)
        assert calls==['abort'] and errors==['UNKNOWN']
    asyncio.run(case())
