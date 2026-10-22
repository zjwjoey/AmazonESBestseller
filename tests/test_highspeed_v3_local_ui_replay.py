"""Offline protocol simulation using actual received native fragment bytes.

This does not establish real AUI retry behavior or LIVE carousel completeness.
"""
from pathlib import Path
import json,hashlib
from types import SimpleNamespace
import pytest
from bs4 import BeautifulSoup
from amazon_es_bestseller.transport.playwright import PlaywrightTransport
from amazon_es_bestseller.highspeed.cli import save_native_contract
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.contracts import new_plan
from amazon_es_bestseller.multisource.storage import immutable_json,sealed

def protocol(tmp_path, *, held=False):
    root=Path('tests/fixtures/highspeed');native=json.loads((root/'real_v3_t1_best_native_carousel_contract.json').read_text(encoding='utf8'))
    fragment=root/'real_v3_t1_best_native_carousel_acp.html';parent=root/'real_v3_t1_best_native_carousel_acp_parent.html'
    proof=json.loads(fragment.with_suffix('.provenance.json').read_text(encoding='utf8'))
    source={'source_id':proof['source_id'],'source_type':proof['source_type'],'actual_source_url':proof['source_url'],'page_urls':[proof['source_url']],'parser_profile':'V3','research_category':None}
    parsed=parse_page(parent.read_text(encoding='utf8'),source=source,run_id='protocol_fixture',page_url=proof['source_url'],page_number=1,observed_at=proof['observed_at'],evidence_path=str(parent))
    descriptor={**parsed['acp_pending'][0],'source_id':source['source_id']}
    plan=new_plan('protocol_fixture',stage='T1',origin='LIVE_CAPTURE',sources=[source]);store=RunStore(tmp_path/'store');store.begin_run(plan)
    events=[]
    class Page:
        def __init__(self):self.url=proof['source_url'];self.phase=0;self.wire_fetches=0;self.fulfilled=[];self.main_frame=object();self.handlers={}
        def route(self,pattern,callback):self.capture=callback
        def on(self,name,callback):self.handlers[name]=callback
        def wait_for_timeout(self,ms):pass
        def evaluate(self,script,arg):
            if 'wrapper.outerHTML' in script:
                owner=PlaywrightTransport._saved_carousel_owner(descriptor,self.url)
                soup=BeautifulSoup(parent.read_bytes(),'lxml');wrapper=soup.find(id=owner['owner_id']);node=wrapper.select_one('[data-a-carousel-options]')
                node['class']=list(node.get('class',[]))+['a-carousel-initialized','p13n-carousel-initialized'];return str(wrapper)
            if 'next.click()' in script:
                # Phase 0 repeats the actual first native body until its own
                # promise receives a response. Phase 1 uses an explicitly mock
                # next body, never a guessed request executed on Amazon.
                payload=native['payload'] if self.phase==0 else 'mock-next-native-body'
                request=SimpleNamespace(url=native['url'],method=native['method'],post_data=payload,headers=native['headers'],resource_type='fetch')
                def fulfill(**kwargs):self.fulfilled.append(kwargs);self.phase=1
                self.capture(SimpleNamespace(request=request,abort=lambda:None,fulfill=fulfill,continue_=lambda **kwargs:pytest.fail('unmetered request')))
                return True
            assert 'await fetch(url' in script;self.wire_fetches+=1
            return {'status':200,'url':native['url'],'text':fragment.read_text(encoding='utf8'),'headers':{'content-type':'text/html;charset=UTF-8'}}
    page=Page();transport=PlaywrightTransport(SimpleNamespace(page=page),hold_native_carousel=held)
    transport.install_bounded_acp_capture(on_capture=lambda value:save_native_contract(SimpleNamespace(root=tmp_path),plan,value),on_local_replay=events.append)
    prepared=transport.prepare_ajax_descriptor(descriptor,referer=page.url)
    response=transport.fetch_ajax(prepared['url'],method=prepared['method'],referer=page.url,payload=prepared['payload'],headers=prepared['headers'])
    request=store.reserve_request(plan['run_id'],{'kind':'ACP','url':prepared['url'],'source_id':source['source_id'],'source_type':source['source_type'],'observed_at':proof['observed_at'],'page_key':'parent'})
    receipt=store.save_response(plan['run_id'],request,{'kind':'ACP','text':response.text,'status_code':response.status_code,'access_state':response.access_state,'origin':'LIVE_CAPTURE','url':response.url,'final_url':response.url,'headers':response.headers,'request_url':prepared['url'],'request_method':prepared['method'],'request_payload':prepared['payload'],'source_id':source['source_id'],'source_type':source['source_type'],'observed_at':proof['observed_at'],'page_key':'parent','native_acp_descriptor':prepared,'ranking_context_binding':prepared['ranking_context_binding']})
    return transport,page,store,receipt,descriptor,events,fragment


def test_held_original_promise_resolves_after_durable_receipt_without_retry(tmp_path):
    transport,page,store,receipt,descriptor,events,fragment=protocol(tmp_path,held=True)
    try:
        assert not page.fulfilled
        assert transport._held_carousel_routes
        assert receipt['native_acp_descriptor']['request_contract_source']=='NATIVE_BROWSER_REQUEST_HELD'
        assert transport.acknowledge_native_response(receipt)
        assert page.phase==1 and len(page.fulfilled)==1
        assert page.fulfilled[0]['body']==fragment.read_text(encoding='utf8')
        assert page.wire_fetches==1 and not transport._held_carousel_routes
        assert events[0]['new_network_request'] is False
        assert store.summary('protocol_fixture')['requests']==1
    finally:store.close()


def test_held_native_requires_explicit_scope_permission(tmp_path):
    transport,page,store,receipt,descriptor,events,fragment=protocol(tmp_path,held=True)
    try:
        from amazon_es_bestseller.highspeed.authorization import _validate_v3_native_carousel
        d=receipt['native_acp_descriptor'];sid=d['source_id']
        scope={'parser_profile':'V3','source_type':receipt['source_type'],
            'page_urls':[page.url],'allow_native_acp':True}
        auth={'snapshot_contract_version':3,'run_id':receipt['run_id'],'sources':{sid:scope}}
        with pytest.raises(ValueError,match='EXACT_ABORTED_POST'):
            _validate_v3_native_carousel(d['url'],d,scope,auth,source_id=sid)
        scope['allow_held_native_carousel']=True
        _validate_v3_native_carousel(d['url'],d,scope,auth,source_id=sid)
    finally:store.close()

def test_exact_saved_response_advances_only_native_ui_without_second_wire_fetch(tmp_path):
    transport,page,store,receipt,descriptor,events,fragment=protocol(tmp_path)
    try:
        transport.acknowledge_native_response(receipt)
        second=transport.prepare_ajax_descriptor(descriptor,referer=page.url)
        assert page.wire_fetches==1 and len(page.fulfilled)==1 and page.phase==1
        assert page.fulfilled[0]['body']==fragment.read_text(encoding='utf8')
        assert second['payload']=='mock-next-native-body'
        assert events[0]['origin']=='LOCAL_UI_REPLAY' and events[0]['new_network_request'] is False
        assert len(list(store.iter_responses('protocol_fixture')))==1
        assert store.summary('protocol_fixture')['requests']==1
    finally:store.close()

@pytest.mark.parametrize('mismatch',['body','headers','context','owner','URL'])
def test_current_owner_exact_request_required_for_local_delivery(tmp_path,mismatch):
    transport,page,store,receipt,descriptor,events,fragment=protocol(tmp_path)
    try:
        assert transport.acknowledge_native_response(receipt) is True
        owner=transport._saved_carousel_owner(descriptor,page.url)
        native=json.loads(Path('tests/fixtures/highspeed/real_v3_t1_best_native_carousel_contract.json').read_text(encoding='utf8'))
        transport._active_carousel_owner=owner
        payload=native['payload'];headers=dict(native['headers']);url=native['url']
        if mismatch=='body':payload='different-body'
        elif mismatch=='headers':headers['x-amz-acp-params']='different-opaque-owner'
        elif mismatch=='context':owner['ranking_context_binding']={**owner['ranking_context_binding'],'ranking_context_url':'https://www.amazon.es/gp/bestsellers/toys'}
        elif mismatch=='owner':owner['owner_id']='other-opaque-owner'
        elif mismatch=='URL':url+='&unknown=changed'
        request=SimpleNamespace(url=url,method=native['method'],post_data=payload,headers=headers,resource_type='fetch')
        aborted=[]
        page.capture(SimpleNamespace(request=request,abort=lambda:aborted.append(True),fulfill=lambda **kw:pytest.fail('mismatched request fulfilled'),continue_=lambda **kw:pytest.fail('unmetered request')))
        assert aborted==[True] and not page.fulfilled and not events and page.wire_fetches==1
    finally:store.close()

def test_main_frame_refresh_clears_same_URL_local_response_cache(tmp_path):
    transport,page,store,receipt,descriptor,events,fragment=protocol(tmp_path)
    try:
        assert transport.acknowledge_native_response(receipt) is True
        page.handlers['framenavigated'](page.main_frame)
        assert not transport._carousel_ui_replay and not transport._pending_carousel_responses
        assert not transport._prepared_carousel_requests
        transport.prepare_ajax_descriptor(descriptor,referer=page.url)
        assert not page.fulfilled and not events and page.wire_fetches==1
    finally:store.close()

@pytest.mark.parametrize('field,value',[('status_code',429),('access_state','CHALLENGE'),('origin','HISTORICAL_REPLAY'),('request_payload','wrong'),('source_id','wrong'),('sha256','0'*64)])
def test_wrong_or_restricted_receipt_never_activates_ui_replay(tmp_path,field,value):
    transport,page,store,receipt,descriptor,events,fragment=protocol(tmp_path);receipt={**receipt,field:value}
    try:
        transport.acknowledge_native_response(receipt)
        with pytest.raises(ValueError,match='CONTRACT_NOT_OBSERVED'):transport.prepare_ajax_descriptor(descriptor,referer=page.url)
        assert page.wire_fetches==1 and not page.fulfilled and not events
    finally:store.close()
