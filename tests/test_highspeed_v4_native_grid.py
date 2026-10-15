"""Real saved T2 grid plus aborted browser bytes; no live requests in tests."""
import hashlib,json
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from amazon_es_bestseller.highspeed.v4_authorization import authorize_v4_request
from amazon_es_bestseller.transport.playwright import PlaywrightTransport

FIX=Path(__file__).parent/'fixtures'/'highspeed'
URL='https://www.amazon.es/gp/bestsellers/kitchen/ref=zg_bs_kitchen_sm'

def grid_request():
    path=FIX/'real_v4_t2_kitchen_grid.html';native_path=FIX/'real_v4_t2_kitchen_native_aborted.json'
    source={'source_id':'kitchen','source_type':'BEST_SELLERS','source_subtype':'BESTSELLER_SALES_RANKING',
        'actual_source_url':URL,'page_urls':[URL],'parser_profile':'V4'}
    native=json.loads(native_path.read_text(encoding='utf8'));run=native['run_id']
    parsed=parse_v4_page(path.read_text(encoding='utf8'),source=source,page_url=URL,run_id=run,
        observed_at=native['captured_at'],evidence_path=str(path),origin='OFFLINE_FIXTURE')
    pending=parsed['acp_pending'][0]
    d=PlaywrightTransport._native_batch_descriptor(dict(pending,url=native['url']),native)
    d.update(source_id='kitchen',native_contract_evidence_path=str(native_path),
        native_contract_evidence_sha256=hashlib.sha256(native_path.read_bytes()).hexdigest(),
        request_payload_sha256=hashlib.sha256(d['payload'].encode()).hexdigest(),
        ranking_context_binding={'source_id':'kitchen','page_evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'ranking_context_url':parsed['observations'][0]['rank_context']})
    scope={k:source[k] for k in ('source_type','source_subtype','page_urls','parser_profile')}
    scope['allow_native_acp']=True
    auth={'snapshot_contract_version':4,'run_id':run,'sources':{'kitchen':scope}}
    return d,source,auth

def test_actual_grid_native_request_uses_grid_evidence_not_carousel_owner():
    d,s,a=grid_request()
    assert not d['metadata'].get('carousel')
    assert len(json.loads(d['payload'])['indexes'])==8
    authorize_v4_request(d['url'],s,a,kind='ACP',descriptor=d)

@pytest.mark.parametrize('field,value',[('params','wrong'),('faceout','wrong'),('reftag','wrong'),('entries',[]),('offset_raw','99')])
def test_grid_metadata_cannot_diverge_from_saved_dom(field,value):
    d,s,a=grid_request();d['metadata'][field]=value
    with pytest.raises(ValueError):authorize_v4_request(d['url'],s,a,kind='ACP',descriptor=d)

@pytest.mark.parametrize('field,value',[('source_id','other'),('ranking_context_url','https://www.amazon.es/gp/bestsellers/books'),('page_evidence_sha256','0'*64)])
def test_grid_binding_cannot_cross_source_or_parent(field,value):
    d,s,a=grid_request();d['ranking_context_binding'][field]=value
    with pytest.raises(ValueError):authorize_v4_request(d['url'],s,a,kind='ACP',descriptor=d)

def test_grid_changed_payload_or_native_reference_is_rejected():
    for field,value in [('payload','{}'),('native_contract_evidence_sha256','0'*64),('request_contract_parent_page_url','https://www.amazon.es/gp/bestsellers/books')]:
        d,s,a=grid_request();d[field]=value
        with pytest.raises(ValueError):authorize_v4_request(d['url'],s,a,kind='ACP',descriptor=d)

def test_literal_saved_grid_rec_batches_keep_width_and_tail_without_new_ids():
    from amazon_es_bestseller.highspeed.v4_authorization import saved_grid_binding
    original,s,a=grid_request()
    native=json.loads(Path(original['native_contract_evidence_path']).read_text(encoding='utf8'))
    for start in (38,46):
        pending={**original,'offset':start,'count':50-start}
        d=PlaywrightTransport._native_batch_descriptor(pending,native)
        d.update(source_id=s['source_id'],native_contract_evidence_path=original['native_contract_evidence_path'],
            native_contract_evidence_sha256=original['native_contract_evidence_sha256'],
            request_payload_sha256=hashlib.sha256(d['payload'].encode()).hexdigest())
        d['ranking_context_binding']=saved_grid_binding(d,s)
        body=json.loads(d['payload'])
        assert body['indexes']==list(range(start,min(start+8,50)))
        assert [json.loads(v) for v in body['ids']]==d['metadata']['entries'][start:min(start+8,50)]
        authorize_v4_request(d['url'],s,a,kind='ACP',descriptor=d)

def test_grid_requires_explicit_native_scope_and_same_run():
    d,s,a=grid_request();a['sources']['kitchen']['allow_native_acp']=False
    with pytest.raises(ValueError):authorize_v4_request(d['url'],s,a,kind='ACP',descriptor=d)
    d,s,a=grid_request();a['run_id']='other'
    with pytest.raises(ValueError):authorize_v4_request(d['url'],s,a,kind='ACP',descriptor=d)
