import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from amazon_es_bestseller.collection import detail_dual_runtime5500 as mod


def fixture(tmp_path):
    p=Path(__file__).parent/'fixtures/detail_bridge5500/actual_CDP_root_present_Playwright_root_missing_receipt.json'
    r=json.loads(p.read_text(encoding='utf8'))['payload']
    c=mod.ParallelCollector(NS(root=tmp_path,plan={}), {'request_start_interval_seconds':3})
    sent=[]; root=r['browser_network_timing']['requests'][0]
    c.tokens[0]=dict(task={'task_id':r['task_id'],'request_url':r['request_url']},
        receipt={'task_id':r['task_id'],'state':'RESERVED'}, sent=False,
        navigation_requested=True, notify_sent=lambda:sent.append(True))
    event=dict(type='Document',frameId=root['frame_id'],requestId=root['request_id'],
        timestamp=root['browser_request_event_monotonic_seconds'],
        wallTime=root['browser_request_wall_time_seconds'],
        request={'url':root['url'],'method':'GET'})
    return c,root['frame_id'],event,sent,r


def test_actual_CDP_root_releases_start_when_Playwright_only_has_redirect(tmp_path):
    c,frame,event,sent,original=fixture(tmp_path)
    assert not original['document_start_event_observed']
    assert original['browser_network_timing']['requests'][0]['status_code']==302
    c.observe_native_request(0,frame,event)
    assert sent==[True]
    receipt=c.tokens[0]['receipt']
    assert receipt['document_start_evidence_kind']=='BROWSER_CDP_SCOPED_MAIN_DOCUMENT_REQUEST_EVENT'
    assert receipt['native_document_start']['timestamp']==event['timestamp']
    assert 'status_code' not in receipt and 'document_response_at' not in receipt
    assert original['document_start_event_observed'] is False


@pytest.mark.parametrize('bad',['not_requested','no_reservation','wrong_task','foreign_frame','foreign_URL',
    'POST','XHR','redirect_only','missing_request_id','missing_timestamp','nan_timestamp','missing_walltime','stop'])
def test_native_start_is_bound_to_reserved_exact_root_and_real_timestamps(tmp_path,bad):
    c,frame,e,sent,_=fixture(tmp_path)
    if bad=='not_requested':c.tokens[0]['navigation_requested']=False
    elif bad=='no_reservation':c.tokens[0]['receipt']['state']='NORMAL'
    elif bad=='wrong_task':c.tokens[0]['receipt']['task_id']='other'
    elif bad=='foreign_frame':e['frameId']='other'
    elif bad=='foreign_URL':e['request']['url']='https://www.amazon.es/dp/B000000001'
    elif bad=='POST':e['request']['method']='POST'
    elif bad=='XHR':e['type']='XHR'
    elif bad=='redirect_only':e['redirectResponse']={'url':e['request']['url']}
    elif bad=='missing_request_id':e.pop('requestId')
    elif bad=='missing_timestamp':e.pop('timestamp')
    elif bad=='nan_timestamp':e['timestamp']=float('nan')
    elif bad=='missing_walltime':e.pop('wallTime')
    else:c.scheduler.stop.set()
    c.observe_native_request(0,frame,e)
    assert not sent and not c.tokens[0]['sent']


def test_native_duplicate_is_idempotent_and_second_root_stops(tmp_path):
    c,frame,e,sent,_=fixture(tmp_path)
    c.observe_native_request(0,frame,e);c.observe_native_request(0,frame,e)
    assert sent==[True] and not c.scheduler.stop.is_set()
    e=copy.deepcopy(e);e['requestId']='unapproved-second-root'
    c.observe_native_request(0,frame,e)
    assert sent==[True] and c.scheduler.stop.is_set()


def test_native_start_preserves_single_original_route_and_blocks_reload(tmp_path):
    c,frame,e,sent,_=fixture(tmp_path); main=object();c.pages=[NS(main_frame=main)]
    c.observe_native_request(0,frame,e)
    request=NS(resource_type='document',method='GET',url=e['request']['url'],frame=main,
               redirected_from=None,is_navigation_request=lambda:True)
    actions=[]
    async def proceed():actions.append('allow')
    async def abort():actions.append('abort')
    asyncio.run(c.route(NS(request=request,continue_=proceed,abort=abort)))
    asyncio.run(c.route(NS(request=NS(**vars(request)),continue_=proceed,abort=abort)))
    assert actions==['allow','abort'] and sent==[True]


def test_completed_goto_cannot_admit_a_user_reload_without_original_route(tmp_path):
    c,frame,e,sent,_=fixture(tmp_path);main=object();c.pages=[NS(main_frame=main)]
    c.observe_native_request(0,frame,e);c.tokens[0]['navigation_completed']=True
    req=NS(resource_type='document',method='GET',url=e['request']['url'],frame=main,
           redirected_from=None,is_navigation_request=lambda:True)
    async def forbidden():raise AssertionError('reload must not start')
    async def abort():pass
    asyncio.run(c.route(NS(request=req,continue_=forbidden,abort=abort)))
