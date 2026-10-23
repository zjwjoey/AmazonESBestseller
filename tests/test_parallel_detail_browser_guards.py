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
