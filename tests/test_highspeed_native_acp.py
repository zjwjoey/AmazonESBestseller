import hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from amazon_es_bestseller.highspeed.authorization import authorize_request

def test_actual_native_acp_contract_can_use_dynamic_endpoint(tmp_path):
    parent='https://www.amazon.es/gp/bestsellers/home'
    endpoint='https://www.amazon.es/acp/actual-observed-uuid/nextPage'
    payload='{"opaque":"captured actual request body"}'
    raw={'url':endpoint,'method':'POST','payload':payload,'headers':{'x-amz-acp-params':'actual-page-params'},'parent_page_url':parent,'resource_type':'fetch','request_contract_source':'NATIVE_BROWSER_REQUEST_ABORTED','run_id':'native_review'}
    path=tmp_path/'native.json'; path.write_text(json.dumps(raw),encoding='utf-8')
    descriptor={**raw,'request_contract_parent_page_url':parent,'request_payload_sha256':hashlib.sha256(payload.encode()).hexdigest(),'native_contract_evidence_path':str(path),'native_contract_evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    source={'source_id':'home','source_type':'BEST_SELLERS'}
    approval={'run_id':'native_review','sources':{'home':{'page_urls':[parent],'allow_native_acp':True}}}
    authorize_request(endpoint,source,approval,kind='ACP',descriptor=descriptor)

def native_fixture(tmp_path):
    from amazon_es_bestseller.highspeed.cli import save_native_contract
    parent='https://www.amazon.es/gp/bestsellers/home'
    endpoint='https://www.amazon.es/acp/page-specific-actual-stamp/nextPage'
    captured={'url':endpoint,'method':'POST','payload':'{"opaque":"native actual body"}',
        'headers':{'content-type':'application/json','x-amz-acp-params':'actual-page-params'},
        'parent_page_url':parent,'resource_type':'fetch','request_contract_source':'NATIVE_BROWSER_REQUEST_ABORTED'}
    proof=save_native_contract(SimpleNamespace(root=tmp_path),{'run_id':'native_review'},captured)
    descriptor=dict(captured)
    source={'source_id':'home','source_type':'BEST_SELLERS'}
    approval={'run_id':'native_review','sources':{'home':{'page_urls':[parent],'allow_native_acp':True}}}
    return endpoint,descriptor,source,approval,proof

@pytest.mark.parametrize('field,value,error',[
    ('request_contract_parent_page_url','https://www.amazon.es/gp/bestsellers/unreviewed','PARENT_OR_ENDPOINT'),
    ('request_payload_sha256','0'*64,'PAYLOAD_HASH'),
    ('native_contract_evidence_sha256','0'*64,'EVIDENCE_HASH'),
    ('request_contract_source','CALLER_GUESSED','TRANSPORT_PROOF'),
    ('native_contract_evidence_path',None,'EVIDENCE_REQUIRED')])
def test_unbound_or_unapproved_native_contract_never_authorizes(tmp_path,field,value,error):
    endpoint,desc,source,approval,_=native_fixture(tmp_path)
    desc[field]=value
    with pytest.raises(ValueError,match=error): authorize_request(endpoint,source,approval,kind='ACP',descriptor=desc)

def test_caller_cannot_change_body_even_with_matching_new_hash(tmp_path):
    endpoint,desc,source,approval,_=native_fixture(tmp_path)
    desc['payload']='{"opaque":"invented body"}'
    desc['request_payload_sha256']=hashlib.sha256(desc['payload'].encode()).hexdigest()
    with pytest.raises(ValueError,match='BODY_NOT_BOUND'): authorize_request(endpoint,source,approval,kind='ACP',descriptor=desc)

def test_native_capture_callback_preserves_actual_payload_in_immutable_file(tmp_path):
    endpoint,desc,source,approval,proof=native_fixture(tmp_path)
    raw=Path(proof['path']).read_bytes()
    saved=json.loads(raw)
    assert saved['payload']==desc['payload']
    assert saved['parent_page_url']==desc['request_contract_parent_page_url']
    assert hashlib.sha256(raw).hexdigest()==proof['sha256']
    authorize_request(endpoint,source,approval,kind='ACP',descriptor=desc)

def test_derived_native_batch_must_rebuild_from_saved_first_contract(tmp_path):
    from amazon_es_bestseller.highspeed.cli import save_native_contract
    from amazon_es_bestseller.transport.playwright import PlaywrightTransport
    parent='https://www.amazon.es/gp/bestsellers/home'; endpoint='https://www.amazon.es/acp/observed-batch-id/nextPage'
    entries=[{'opaque':str(i)} for i in range(10)]
    body={'faceoutkataname':'native-faceout','ids':[json.dumps(e) for e in entries[2:4]],'indexes':[2,3],
        'linkparameters':'','offset':'2','reftagprefix':'native-reftag'}
    captured={'url':endpoint,'method':'POST','payload':json.dumps(body),
        'headers':{'content-type':'application/json','x-amz-acp-params':'page-token'},
        'parent_page_url':parent,'resource_type':'fetch','request_contract_source':'NATIVE_BROWSER_REQUEST_ABORTED'}
    save_native_contract(SimpleNamespace(root=tmp_path),{'run_id':'native_review'},captured)
    request={'url':endpoint,'method':'POST','offset':4,'count':6,'metadata':{
        'entries':entries,'index_offset_raw':'2','offset_raw':'10','faceout':'native-faceout','reftag':'native-reftag'}}
    desc=PlaywrightTransport._native_batch_descriptor(request,captured)
    desc['request_payload_sha256']=hashlib.sha256(desc['payload'].encode()).hexdigest()
    source={'source_id':'home','source_type':'BEST_SELLERS'}
    approval={'run_id':'native_review','sources':{'home':{'page_urls':[parent],'allow_native_acp':True}}}
    authorize_request(endpoint,source,approval,kind='ACP',descriptor=desc)
    mutated=json.loads(desc['payload']); mutated['ids']=['not observed opaque data']*2
    desc['payload']=json.dumps(mutated); desc['request_payload_sha256']=hashlib.sha256(desc['payload'].encode()).hexdigest()
    with pytest.raises(ValueError,match='BODY_NOT_BOUND'): authorize_request(endpoint,source,approval,kind='ACP',descriptor=desc)
