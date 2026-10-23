import asyncio
import pytest
from amazon_es_bestseller.access.detector import AccessStopError

from amazon_es_bestseller.collection.detail_parallel_runtime import ParallelDetailRunner


def tasks(n=8):
    return [{'task_id':str(i),'asin':f'B{i:09d}',
             'request_url':f'https://www.amazon.es/dp/B{i:09d}',
             'candidate_id':f'c{i}','selection_id':'selection'} for i in range(n)]


def test_item_errors_continue_but_access_stops_and_evidence_is_preserved(tmp_path):
    async def case():
        plan={'run_id':'run','tasks':tasks()}
        now=[0.0]
        async def sleep(delay):now[0]+=delay;await asyncio.sleep(0)
        captures=[]
        async def capture(task,lane,sent,stop,restrict):
            sent();captures.append(task['task_id'])
            status=403 if task['task_id']=='4' else 200
            if status==403:restrict('BLOCKED')
            return {'html':'<html>saved evidence</html>','status_code':status,
                    'request_url':task['request_url'],'final_url':task['request_url'],
                    'document_start_observed':True,'window_id':str(lane)}
        def parse(task,html,meta):
            if task['task_id']=='1':raise ValueError('ordinary parser failure')
            return {'asin':task['asin'],'title_es_raw':'Producto',
                    'identity_status':'IDENTITY_MATCH','resolved_asin':task['asin']}
        gates=[]
        runner=ParallelDetailRunner(tmp_path,plan,{'max_requests':20,'prior_requests':0,'budget_holds':0},
            capture=capture,parse=parse,check_gate=lambda:None,stop_gate=gates.append,
            clock=lambda:now[0],sleep=sleep)
        result=await runner.run()
        outcomes=runner.store.outcomes()
        assert result['state']=='ACCESS_STOP'
        assert gates==['BLOCKED']
        assert len(captures)==5
        assert any(r['result']['status']=='PARSE_FAILED' for r in outcomes)
        assert all(r['result'].get('html_sha256') for r in outcomes)
        assert result['pending']==3
        with pytest.raises(AccessStopError):runner.store.pending()
    asyncio.run(case())


def test_resume_does_not_recapture_success_or_failed_items(tmp_path):
    async def case():
        plan={'run_id':'run','tasks':tasks(2)}
        async def capture(task,lane,sent,stop,restrict):
            sent()
            return {'html':'<html>saved evidence</html>','status_code':200,
                    'request_url':task['request_url'],'final_url':task['request_url'],
                    'document_start_observed':True,'window_id':str(lane)}
        def parse(task,*args):return {'asin':task['asin'],'title_es_raw':'P','identity_status':'IDENTITY_MISMATCH'}
        now=[0.0]
        async def sleep(delay):now[0]+=delay;await asyncio.sleep(0)
        opts=dict(capture=capture,parse=parse,check_gate=lambda:None,stop_gate=lambda state:None,
                  clock=lambda:now[0],sleep=sleep)
        budget={'max_requests':10,'prior_requests':0,'budget_holds':0}
        first=ParallelDetailRunner(tmp_path,plan,budget,**opts)
        assert (await first.run())['failed']==2
        async def never(*args):raise AssertionError('replayed')
        opts['capture']=never
        second=ParallelDetailRunner(tmp_path,plan,budget,**opts)
        assert (await second.run())['failed']==2
    asyncio.run(case())


def test_current_v2_identity_contract_is_used_without_legacy_status_translation(tmp_path):
    from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2
    async def case():
        task=tasks(1)[0]
        async def capture(task,lane,sent,stop,restrict):
            sent()
            return {'html':f'<input id="ASIN" value="{task["asin"]}"><span id="productTitle">Producto</span>',
                'status_code':200,'request_url':task['request_url'],'final_url':task['request_url'],
                'document_start_observed':True}
        def parse(task,body,meta):
            return parse_detail_evidence_v2(body,task['asin'],requested_url=task['request_url'],final_url=meta['final_url'])
        runner=ParallelDetailRunner(tmp_path,{'run_id':'run','tasks':[task]},
            {'max_requests':1,'prior_requests':0,'budget_holds':0},capture=capture,parse=parse,
            check_gate=lambda:None,stop_gate=lambda state:None)
        assert (await runner.run())['successful']==1
    asyncio.run(case())


def test_challenge_blocks_admission_before_slow_evidence_write(tmp_path):
    import time
    async def case():
        captured=[];now=[0.0]
        async def sleep(delay):now[0]+=delay;await asyncio.sleep(0)
        async def capture(task,lane,sent,stop,restrict):
            sent();captured.append(task['task_id'])
            return {'html':'<html>Robot Check</html>','status_code':200,
                'request_url':task['request_url'],'document_start_observed':True}
        runner=ParallelDetailRunner(tmp_path,{'run_id':'run','tasks':tasks()},
            {'max_requests':20,'prior_requests':0,'budget_holds':0},capture=capture,
            parse=lambda *args:{},check_gate=lambda:None,stop_gate=lambda state:None,
            clock=lambda:now[0],sleep=sleep)
        original=runner.store.save_capture
        def slow(*args):time.sleep(.05);return original(*args)
        runner.store.save_capture=slow
        assert (await runner.run())['state']=='ACCESS_STOP'
        assert captured==['0']
    asyncio.run(case())


@pytest.mark.parametrize('has_document_200',[True,False])
def test_null_http_timeout_is_only_isolated_with_scoped_normal_evidence(tmp_path,has_document_200):
    async def case():
        now=[0.0];stops=[]
        async def sleep(delay):now[0]+=delay;await asyncio.sleep(0)
        async def capture(task,lane,sent,stop,restrict):
            sent()
            if task['task_id']=='0':
                return {'html':'<span id="glow-ingress-line2">Madrid 28001</span>',
                    'status_code':None,'request_url':task['request_url'],'final_url':task['request_url'],
                    'document_start_observed':True,'timed_out':True,'error':'45000ms domcontentloaded timeout',
                    'native_document_start':{'url':task['request_url'],'method':'GET'},
                    'document_responses':[{'url':task['request_url'],'status_code':200}] if has_document_200 else []}
            return {'html':'Product','status_code':200,'request_url':task['request_url'],
                    'document_start_observed':True}
        def parse(task,*args):return {'asin':task['asin'],'resolved_asin':task['asin'],
                                     'identity_status':'IDENTITY_MATCH','title_es_raw':'P'}
        runner=ParallelDetailRunner(tmp_path,{'run_id':'run','tasks':tasks(2)},
            {'max_requests':2,'prior_requests':0,'budget_holds':0},capture=capture,parse=parse,
            check_gate=lambda:None,stop_gate=stops.append,clock=lambda:now[0],sleep=sleep)
        result=await runner.run()
        if has_document_200:
            assert result['successful']==1 and result['failed']==1 and not stops
            from amazon_es_bestseller.multisource.storage import read_sealed
            failed=next(row for row in runner.store.outcomes() if row['result']['status']=='NETWORK_FAILED')
            receipt=read_sealed(runner.store.root/failed['result']['receipt_path'])
            assert receipt['capture']['status_code'] is None
        else:
            assert result['state']=='ACCESS_STOP' and stops==['UNKNOWN']
            assert result['successful']==0
    asyncio.run(case())


def test_terminal_disk_failure_stops_admission_and_keeps_saved_evidence(tmp_path):
    async def case():
        captured=[]
        async def capture(task,lane,sent,stop,restrict):
            sent();captured.append(task['task_id'])
            return {'html':'original saved page','status_code':200,
                    'request_url':task['request_url'],'document_start_observed':True}
        def parse(task,*args):return {'asin':task['asin'],'resolved_asin':task['asin'],
                                     'identity_status':'IDENTITY_MATCH','title_es_raw':'P'}
        runner=ParallelDetailRunner(tmp_path,{'run_id':'run','tasks':tasks(2)},
            {'max_requests':2,'prior_requests':0,'budget_holds':0},capture=capture,parse=parse,
            check_gate=lambda:None,stop_gate=lambda state:None,lanes=1)
        def fail(*args):raise OSError('LOCAL_TEST_TERMINAL_DISK_FAILURE')
        runner.store.finish=fail
        with pytest.raises(OSError,match='TERMINAL_DISK_FAILURE'):await runner.run()
        assert captured==['0'] and runner.scheduler.stop.is_set()
        directory=runner.store.directory(tasks(2)[0])
        assert (directory/'response.html').read_text()=='original saved page'
        assert (directory/'receipt.json').exists() and not (directory/'outcome.json').exists()
        with pytest.raises(ValueError,match='UNRESOLVED_RESERVATION'):runner.store.pending()
    asyncio.run(case())
