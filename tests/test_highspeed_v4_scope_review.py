"""Offline gate simulation with real saved query-reordering PAGE bytes."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json
import pytest
from amazon_es_bestseller.highspeed.v4_navigation import exact_query_token_reordering
from amazon_es_bestseller.highspeed.v4_loading import same_loading_view
from amazon_es_bestseller.highspeed.v4_scope_review import correct_query_order_scope_false_positive
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.multisource.storage import atomic_json,sealed,read_sealed,digest
from test_highspeed_v4_execution import setup_scope
from test_highspeed_v4_loading import ROOT,pagination

PROOF=json.loads((ROOT/'favorites_page2_reordered.provenance.json').read_text(encoding='utf8'))


@pytest.mark.parametrize('bad',['actual','qid','duplicate','path','drop','encoding'])
def test_actual_query_order_equivalence_never_changes_tokens(bad):
    a=PROOF['requested_url'];b=PROOF['final_url']
    if bad=='qid':b=b.replace('1792011200','1')
    elif bad=='duplicate':b+='&page=2'
    elif bad=='path':b=b.replace('/s?','/deals?')
    elif bad=='drop':b=b.replace('&xpid=1-xk5ip44sMjW','')
    elif bad=='encoding':b=b.replace('%3A','%3a')
    assert exact_query_token_reordering(a,b)==(bad=='actual')


@pytest.mark.parametrize('bad',['actual','filter','empty_filter','node','path'])
def test_deals_observed_push_state_is_same_document_not_new_scope(bad):
    root='https://www.amazon.es/deals'
    view=root+'?promotionsSearchLastSeenAsin=B09W9CGD3J&promotionsSearchStartIndex=0&promotionsSearchPageSize=60'
    if bad=='filter':view+='&bubble-id=other'
    elif bad=='empty_filter':view+='&bubble-id='
    elif bad=='node':view+='&node=1'
    elif bad=='path':view=view.replace('/deals?','/gp/bestsellers?')
    assert same_loading_view(view,root)==(bad=='actual')


@pytest.mark.parametrize('bad',['none','status','query','captcha','reason','shared','expired','body'])
def test_only_proven_normal_query_scope_error_can_be_corrected_offline(tmp_path,bad):
    plan,a=setup_scope(tmp_path);source,ref=pagination()
    s=next(x for x in plan['sources'] if x['source_id']==source['source_id'])
    s['page_urls'].append(ref['url']);s['pagination_evidence']=[ref]
    plan['sources']=[s];a['sources']={s['source_id']:a['sources'][s['source_id']]}
    a['sources'][s['source_id']]['page_urls']=s['page_urls']
    now=datetime.now(timezone.utc)
    a.update(approved_by='USER_EXPLICIT_CHAT',authorization_text='修复问题，先离线测试再复验',plan_sha256=digest(plan),
        reviewed_at=(now-timedelta(minutes=5)).isoformat(),expires_at=(now+timedelta(minutes=10)).isoformat())
    store=RunStore(a['store_root']);store.begin_run(plan,started_at=now.isoformat())
    body=(ROOT/'favorites_page2_reordered.html').read_text(encoding='utf8')
    if bad=='captcha':body='<html><form action="/errors/validateCaptcha">Robot Check</form></html>'
    actual=PROOF['final_url'] if bad!='query' else PROOF['final_url']+'&other=1'
    req=store.reserve_request(plan['run_id'],{'kind':'PAGE','url':ref['url'],'source_id':s['source_id'],
        'source_type':s['source_type'],'observed_at':now.isoformat(),'origin':'LIVE_CAPTURE'})
    r=store.save_response(plan['run_id'],req,{'status_code':403 if bad=='status' else 200,'text':body,
        'url':actual,'final_url':actual,'request_url':ref['url'],'source_id':s['source_id'],'source_type':s['source_type'],
        'kind':'PAGE','origin':'LIVE_CAPTURE','access_state':'NORMAL','observed_at':now.isoformat()})
    store.update_run(plan['run_id'],status='STOPPED')
    store.set_gate('UNKNOWN',run_id=plan['run_id'],reason='OTHER' if bad=='reason' else 'UNREVIEWED_REDIRECT',evidence_path=r['evidence_path'])
    shared=Path(plan['shared_access_gate_path'])
    atomic_json(shared,sealed({'state':'UNKNOWN','run_id':plan['run_id'],
        'evidence_path':'other' if bad=='shared' else r['evidence_path']}))
    if bad=='expired':a['expires_at']=(now-timedelta(seconds=1)).isoformat()
    if bad=='body':Path(r['html_path']).write_text('tampered',encoding='utf8')
    before=[p.read_bytes() for p in [store.root/'access_gate.json',shared]]
    try:
        if bad!='none':
            with pytest.raises(ValueError):correct_query_order_scope_false_positive(store,plan,a)
            assert before==[p.read_bytes() for p in [store.root/'access_gate.json',shared]]
        else:
            proof=correct_query_order_scope_false_positive(store,plan,a)
            assert proof['new_network_requests']==0 and proof['agent_handled'] and not proof['human_handled']
            assert proof['local_gate_before']['state']=='UNKNOWN'
            assert store.get_gate()['state']==read_sealed(shared)['state']=='NORMAL'
            assert store.summary(plan['run_id'])['requests']==1
            assert store.pages(plan['run_id'])[0]['status']=='VERIFIED_WITH_PRODUCTS'
    finally:store.close()
