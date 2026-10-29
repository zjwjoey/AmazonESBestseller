import asyncio,time
import pytest
from playwright.async_api import async_playwright
from amazon_es_bestseller.highspeed.parallel_transport import ThreeWindowTransport
from amazon_es_bestseller.highspeed.parallel_dispatch_gate import AsyncDispatchGate


@pytest.mark.parametrize('code',[200,403])
def test_adapter_three_fixed_windows_shared_pacing_and_stop(tmp_path,code):
    async def exercise():
        urls=[f'https://snapshot-test.invalid/{i}' for i in range(3)]
        records=[];stops=[];admissions=[]
        gate=AsyncDispatchGate(deadline=time.time()+60)
        def admit(w,item):
            admissions.append(time.monotonic());return True
        async with async_playwright() as p:
            context=await p.chromium.launch_persistent_context(str(tmp_path/'profile'),headless=True,service_workers='block')
            try:
                adapter=await ThreeWindowTransport(context,gate,admit=admit,observed_stop=stops.append,
                    captured=lambda w,i,r:records.append((w,i,r)),
                    offline_pages={u:(code,'<html>TEST_ONLY</html>') for u in urls}).open()
                items=[{'url':u,'reservation':{'request_id':str(i)}} for i,u in enumerate(urls)]
                await asyncio.gather(*(adapter.capture(w,i) for w,i in zip((1,2,3),items)),return_exceptions=True)
                if code==200:
                    assert len(records)==3 and not stops
                    assert all(admissions[i+1]-admissions[i]>=2.99 for i in range(2))
                    assert len({r[1]['url'] for r in records})==3
                else:
                    assert len(adapter.sent)==1 and stops
                    assert len(records)==1 and records[0][2]['status_code']==403
                assert len(adapter.pages)==3
            finally:await context.close()
    asyncio.run(exercise())
