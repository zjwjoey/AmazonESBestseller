"""V3 audit_run rechecks actual saved card values and sealed capture receipts."""
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.contracts import DEFAULT_CONFIG
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.quality import audit_run
from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION
from amazon_es_bestseller.multisource.storage import digest


def captured(tmp_path,change=None):
    store=RunStore(tmp_path/'store');url='https://www.amazon.es/gp/bestsellers/home'
    source={'source_id':'s','source_type':'BEST_SELLERS','actual_source_url':url,'page_urls':[url],'parser_profile':'V3'}
    plan={**DEFAULT_CONFIG,'run_id':'r','snapshot_id':'r','stage':'T1','origin':'LIVE_CAPTURE','sources':[source],'snapshot_contract_version':3,'normalizer_version':NORMALIZER_VERSION}
    store.begin_run(plan)
    card=(Path(__file__).parent/'fixtures/highspeed/real_v3_rating_best_sellers.html').read_text(encoding='utf8')
    html='<h1>Los más vendidos</h1><div id="zg" data-expected-count="1">'+card+'</div>'
    time='2026-10-14T00:00:00Z'
    reserved=store.reserve_request('r',{'kind':'PAGE','url':url,'source_id':'s','source_type':'BEST_SELLERS','observed_at':time,'page_key':'page'})
    receipt=store.save_response('r',reserved,{'text':html,'status_code':200,'access_state':'NORMAL','origin':'LIVE_CAPTURE','url':url,'source_id':'s','source_type':'BEST_SELLERS','observed_at':time,'page_key':'page'})
    parsed=parse_page(html,source=source,run_id='r',page_url=url,page_number=1,observed_at=time,evidence_path=receipt['html_path'],origin='LIVE_CAPTURE')
    rows=parsed['observations']
    for row in rows:
        row.update(origin='LIVE_CAPTURE',capture_run_id='r',capture_receipt_reference={'path':receipt['evidence_path'],
            'sha256':digest(Path(receipt['evidence_path']).read_bytes())})
    if change:change(rows[0])
    store.commit_page('r','page',{**parsed['page'],'source':source},rows)
    return store


def test_v3_audit_run_valid_fields_use_real_card_dom(tmp_path):
    store=captured(tmp_path);qa=audit_run(store,'r');store.close()
    assert qa['schema_version']==3
    assert qa['card_field_quality']['ok']
    assert qa['card_field_quality']['counts']['dom_verified_filled_ratings']==1
    # A one-list fixture never pretends full-five LIVE acceptance.
    assert not qa['quality_gate_passed']
    assert any(f['code']=='FIVE_LIST_CARD_COVERAGE_NOT_PROVEN' for f in qa['findings'])

@pytest.mark.parametrize('key,value',[('rating_value',1.0),('review_count',1),('rating_raw','1,0 de 5 estrellas')])
def test_v3_audit_run_tampered_filled_fields_blocked(tmp_path,key,value):
    store=captured(tmp_path,lambda row:row.update({key:value}));qa=audit_run(store,'r');store.close()
    assert not qa['card_field_quality']['ok']
    assert any(f['code']=='RATING_DOM_OR_BINDING_MISMATCH' for f in qa['findings'])


def test_v3_audit_run_fake_capture_time_cannot_match_receipt(tmp_path):
    store=captured(tmp_path,lambda row:row.update(observed_at='2026-10-15T00:00:00Z',rating_observed_at='2026-10-15T00:00:00Z'))
    qa=audit_run(store,'r');store.close()
    assert any(f['code']=='CAPTURE_RECEIPT_UNVERIFIED' for f in qa['findings'])

def test_historical_derivation_cannot_earn_live_stage_quality(tmp_path):
    store=captured(tmp_path,lambda r:r.update(is_derivation=True,parent_run_id='old'))
    qa=audit_run(store,'r');store.close()
    assert not qa['quality_gate_passed']
    assert any(f['code']=='V3_LIVE_RUN_CONTAINS_HISTORICAL_DERIVATION' for f in qa['findings'])
    assert any(f['code']=='CAPTURE_RECEIPT_UNVERIFIED' for f in qa['findings'])


def captured_acp(tmp_path):
    store=RunStore(tmp_path/'store');url='https://www.amazon.es/gp/bestsellers/home'
    source={'source_id':'s','source_type':'BEST_SELLERS','actual_source_url':url,'page_urls':[url],'parser_profile':'V3'}
    plan={**DEFAULT_CONFIG,'run_id':'r','snapshot_id':'r','stage':'T1','origin':'LIVE_CAPTURE','sources':[source],'snapshot_contract_version':3,'normalizer_version':NORMALIZER_VERSION}
    store.begin_run(plan)
    card=(Path(__file__).parent/'fixtures/highspeed/real_v3_rating_best_sellers.html').read_text(encoding='utf8')
    html='<h1>Los más vendidos</h1><div id="zg" data-expected-count="2">'+card+'</div>'
    acp=(Path(__file__).parent/'fixtures/highspeed/real_v3_rating_acp.html').read_text(encoding='utf8')
    captures=[]
    for kind,time,body in [('PAGE','2026-10-14T00:00:00Z',html),('ACP','2026-10-14T00:01:00Z',acp)]:
        request_url=url if kind=='PAGE' else 'https://www.amazon.es/acp/test/nextPage'
        reserved=store.reserve_request('r',{'kind':kind,'url':request_url,'source_id':'s','source_type':'BEST_SELLERS','observed_at':time,'page_key':'page'})
        response={'text':body,'status_code':200,'access_state':'NORMAL','origin':'LIVE_CAPTURE','url':request_url,'source_id':'s','source_type':'BEST_SELLERS','observed_at':time,'page_key':'page'}
        if kind=='ACP':
            parent=captures[0]
            binding={'source_id':'s','ranking_context_url':url,'page_evidence_sha256':parent['sha256']}
            response.update(ranking_context_binding=binding,native_acp_descriptor={
                'request_contract_parent_page_url':url,'parent_page_url':url,
                'evidence_path':parent['html_path'],'ranking_context_binding':binding})
        proof=store.save_response('r',reserved,response)
        captures.append(proof)
    parsed=parse_page(html,source=source,run_id='r',page_url=url,page_number=1,observed_at=captures[0]['observed_at'],evidence_path=captures[0]['html_path'],origin='LIVE_CAPTURE',acp_responses=[{**captures[1],'body':acp,'evidence_path':captures[1]['html_path']}])
    for row in parsed['observations']:
        receipt=captures[0] if row['representation']=='SERVER' else captures[1]
        row.update(origin='LIVE_CAPTURE',capture_run_id='r',capture_receipt_reference={'path':receipt['evidence_path'],
            'sha256':digest(Path(receipt['evidence_path']).read_bytes())})
    store.commit_page('r','page',{**parsed['page'],'source':source},parsed['observations'])
    return store,parsed


def test_v3_acp_actual_capture_time_and_complete_combined_parse(tmp_path):
    store,parsed=captured_acp(tmp_path)
    assert parsed['page']['status']=='COMPLETE'
    acp=next(row for row in parsed['observations'] if row['representation']=='ACP_0')
    assert acp['observed_at']=='2026-10-14T00:01:00Z'
    qa=audit_run(store,'r');store.close()
    assert qa['card_field_quality']['ok']
    assert qa['card_field_quality']['counts']['capture_receipts_verified']==2
    assert sum(x['observations'] for x in qa['card_field_quality']['field_status_by_reverified_page_state'] if x['field']=='rating' and x['page_state']=='COMPLETE')==2
    assert not any(f['code']=='V3_RAW_ROWS_NOT_BOUND_TO_FULL_REPARSE' for f in qa['findings'])


def test_v3_historical_audit_uses_original_capture_receipts(tmp_path):
    from amazon_es_bestseller.highspeed.v3_workflows import replay_v3,readonly_store
    store=captured(tmp_path);root=store.root;store.close()
    mapping=tmp_path/'mapping.json';mapping.write_text('{"rules":[]}',encoding='utf8')
    result=replay_v3(root,'r',tmp_path/'derived',mapping_config=mapping)
    with readonly_store(result['store_root']) as replay_store:
        qa=audit_run(replay_store,result['run_id'])
    assert qa['origin']=='HISTORICAL_REPLAY' and qa['card_field_quality']['ok']
    assert qa['card_field_quality']['counts']['capture_receipts_verified']==1

def test_actual_failed_zero_card_acp_not_dropped_from_completion(tmp_path):
    store=captured(tmp_path);url='https://www.amazon.es/acp/failed/nextPage';time='2026-10-14T00:01:00Z'
    reserved=store.reserve_request('r',{'kind':'ACP','url':url,'source_id':'s','source_type':'BEST_SELLERS','observed_at':time,'page_key':'page'})
    store.save_response('r',reserved,{'text':'Access Denied','status_code':403,'access_state':'BLOCKED','origin':'LIVE_CAPTURE','url':url,'source_id':'s','source_type':'BEST_SELLERS','observed_at':time,'page_key':'page'})
    qa=audit_run(store,'r');store.close()
    assert any(f['code']=='V3_FALSE_COMPLETE_PAGE' for f in qa['findings'])
    assert any(f['code']=='PAGE_SOURCE_OR_ACCESS_INVALID' for f in qa['findings'])
