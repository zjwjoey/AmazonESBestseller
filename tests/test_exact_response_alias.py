import json,hashlib
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed.response_alias import authorize_v4_response
from amazon_es_bestseller.highspeed.response_alias import restore_reviewed_alias_gate
from amazon_es_bestseller.multisource.storage import sealed,digest,immutable_json,read_sealed,file_lock
from datetime import datetime,timedelta,timezone
import gzip

NOW=datetime.now(timezone.utc)

def setup(tmp_path):
    fixture=Path(__file__).parent/'fixtures/highspeed_v4/response_alias'
    proof=json.loads((fixture/'provenance.json').read_text('utf8'))
    body=gzip.decompress((fixture/'actual_cml_home.html.gz').read_bytes())
    assert hashlib.sha256(body).hexdigest()==proof['sha256']
    root=tmp_path/'store';root.mkdir();html=root/'parent.html';html.write_bytes(body)
    source={'source_id':proof['source_id'],'source_type':'CUSTOMER_FAVORITES','source_subtype':'CUSTOMERS_MOST_LOVED_CURATED_ZONE',
        'actual_source_url':proof['request_url'],'page_urls':[proof['request_url']],'parser_profile':'V4'}
    plan={'run_id':'fixture_alias','sources':[source],'shared_access_gate_path':str(tmp_path/'shared/access_gate.json')}
    auth={'run_id':'fixture_alias','plan_sha256':digest(plan),'store_root':str(root),'snapshot_contract_version':4,
        'sources':{source['source_id']:source},'expires_at':(NOW+timedelta(hours=1)).isoformat()}
    r={**proof,'origin':'LIVE_CAPTURE','run_id':auth['run_id'],'request_id':'req_fixture','html_path':str(html),'kind':'PAGE',
       'observed_at':(NOW-timedelta(seconds=1)).isoformat()}
    rp=root/'runs/fixture_alias/responses/req_fixture.json';r['evidence_path']=str(rp);immutable_json(rp,sealed(r))
    immutable_json(rp.parent.parent/'requests/req_fixture.reserved.json',sealed({'run_id':auth['run_id'],
        'plan_sha256':auth['plan_sha256'],'source_id':source['source_id'],'url':proof['request_url'],
        'kind':'PAGE','observed_at':r['observed_at']}))
    local={'state':'UNKNOWN','reason':'UNREVIEWED_REDIRECT','evidence_path':str(rp)}
    shared={'state':'UNKNOWN','evidence_path':str(rp)}
    gate=tmp_path/'shared/access_gate.json';immutable_json(gate,sealed(shared));immutable_json(root/'access_gate.json',sealed(local))
    alias={'approved':True,'approved_by':'TEST_ONLY_USER','approval_id':'test-exact','run_id':auth['run_id'],
        'plan_sha256':auth['plan_sha256'],'store_root':str(root),'source_id':source['source_id'],
        'initial_url':proof['request_url'],'final_url':proof['final_url'],'reviewed_at':NOW.isoformat(),'expires_at':auth['expires_at'],
        'receipt_reference':{'path':str(rp),'sha256':hashlib.sha256(rp.read_bytes()).hexdigest()},
        'canonical_url':'https://www.amazon.es/b?ie=UTF8&node=205103628031','required_headings':['Los favoritos de los clientes','Inicio'],
        'local_gate_sha256':digest(local),'shared_gate_sha256':digest(shared)}
    path=tmp_path/'alias.json';path.write_text(json.dumps(alias),encoding='utf8')
    auth['response_alias_reference']={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    current={**r,'observed_at':(NOW+timedelta(seconds=1)).isoformat()}
    return source,auth,alias,current,gate

def test_alias_never_becomes_an_initial_request_allowance():
    from amazon_es_bestseller.highspeed.v4_authorization import authorize_v4_request
    source={'source_id':'home','source_type':'CUSTOMER_FAVORITES','page_urls':['https://www.amazon.es/cml-home']}
    auth={'snapshot_contract_version':4,'sources':{'home':source}}
    with pytest.raises(ValueError):authorize_v4_request('https://www.amazon.es/b?node=1',source,auth)

def test_no_exception_keeps_exact_redirect_stop():
    source={'source_id':'home','source_type':'CUSTOMER_FAVORITES','page_urls':['https://www.amazon.es/cml-home']}
    auth={'snapshot_contract_version':4,'sources':{'home':source}}
    with pytest.raises(ValueError):authorize_v4_response({'request_url':source['page_urls'][0],'final_url':'https://www.amazon.es/b?node=1','kind':'PAGE'},source,auth)

def test_real_cml_response_alias_is_not_a_new_request_url(tmp_path):
    from amazon_es_bestseller.highspeed.v4_authorization import authorize_v4_request
    s,a,alias,r,g=setup(tmp_path)
    assert authorize_v4_response(r,s,a)=='test-exact'
    with pytest.raises(ValueError):authorize_v4_request(alias['final_url'],s,a,kind='PAGE')
    # The historical stopped receipt is not retroactively accepted.
    with pytest.raises(ValueError):authorize_v4_response({**r,'observed_at':(NOW-timedelta(seconds=1)).isoformat()},s,a)

@pytest.mark.parametrize('field,value',[('final_url','https://www.amazon.es/b?node=999'),('status_code',403),('access_state','CHALLENGE')])
def test_alias_rejects_other_destination_and_restrictions(tmp_path,field,value):
    s,a,alias,r,g=setup(tmp_path)
    with pytest.raises(ValueError):authorize_v4_response({**r,field:value},s,a)

def test_tampered_proof_rejected(tmp_path):
    s,a,alias,r,g=setup(tmp_path);Path(r['html_path']).write_text('changed',encoding='utf8')
    with pytest.raises(ValueError):authorize_v4_response(r,s,a)

class Store:
    def __init__(self,root,failures=()):self.root=root;self.failures=failures;self.events=[]
    def run_lock(self,run_id):return file_lock(self.root/'test.lock')
    def requests(self,run_id):return self.failures
    def append_event(self,run_id,event):self.events.append(event)

def test_only_exact_scope_gate_can_be_handled_without_network(tmp_path):
    s,a,alias,r,g=setup(tmp_path);store=Store(Path(a['store_root']))
    plan={'run_id':a['run_id'],'sources':[s],'shared_access_gate_path':str(g)}
    result=restore_reviewed_alias_gate(store,plan,a)
    assert result['agent_handled'] and not result['human_handled']
    assert read_sealed(g)['state']==read_sealed(store.root/'access_gate.json')['state']=='NORMAL'
    assert read_sealed(g)['previous_gate']['state']=='UNKNOWN'

def test_scope_review_never_handles_new_network_failures(tmp_path):
    s,a,alias,r,g=setup(tmp_path);store=Store(Path(a['store_root']),[{'request_id':'new-network-error','status':'NETWORK_ERROR'}])
    with pytest.raises(ValueError,match='UNHANDLED_ACCESS_FAILURE'):
        restore_reviewed_alias_gate(store,{'run_id':a['run_id'],'sources':[s],'shared_access_gate_path':str(g)},a)
    assert read_sealed(g)['state']=='UNKNOWN'

def test_changed_restriction_gate_cannot_be_restored(tmp_path):
    from amazon_es_bestseller.multisource.storage import atomic_json
    s,a,alias,r,g=setup(tmp_path);store=Store(Path(a['store_root']))
    atomic_json(g,sealed({'state':'CHALLENGE'}))
    with pytest.raises(ValueError,match='EXACT_SCOPE_GATE_REQUIRED'):
        restore_reviewed_alias_gate(store,{'run_id':a['run_id'],'sources':[s],'shared_access_gate_path':str(g)},a)
    assert read_sealed(g)['state']=='CHALLENGE'

@pytest.mark.parametrize('status',[200,403])
def test_scope_chain_keeps_only_legitimately_handled_network_failure(tmp_path,status):
    from amazon_es_bestseller.multisource.storage import atomic_json
    s,a,alias,r,g=setup(tmp_path);root=Path(a['store_root'])
    normal=root/'prior_response.json';immutable_json(normal,sealed({
        'status_code':status,'access_state':'NORMAL' if status==200 else 'BLOCKED',
        'run_id':a['run_id'],'origin':'LIVE_CAPTURE','html_path':r['html_path'],'sha256':r['sha256']}))
    network=root/'prior_network.json';immutable_json(network,sealed({'normal_verification_receipt':str(normal)}))
    old={'state':'NORMAL','agent_handled_request_ids':['old_network'],'agent_recovery_evidence':str(network)}
    scope=root/'prior_scope.json';immutable_json(scope,sealed({'kind':'USER_APPROVED_EXACT_RESPONSE_ALIAS_SCOPE_HANDLING',
        'run_id':a['run_id'],'old_local_gate':{'previous_gate':old}}))
    current=read_sealed(root/'access_gate.json');current['previous_gate']={'state':'NORMAL',
        'agent_handled_request_ids':['old_network'],'scope_alias_handling_evidence':str(scope)}
    atomic_json(root/'access_gate.json',sealed(current));alias['local_gate_sha256']=digest(current)
    path=Path(a['response_alias_reference']['path']);path.write_text(json.dumps(alias),encoding='utf8')
    a['response_alias_reference']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    store=Store(root,[{'request_id':'old_network','status':'NETWORK_ERROR'}])
    plan={'run_id':a['run_id'],'sources':[s],'shared_access_gate_path':str(g)}
    if status==200:
        restore_reviewed_alias_gate(store,plan,a)
        assert read_sealed(root/'access_gate.json')['agent_recovery_evidence']==str(network)
        assert read_sealed(g)['state']=='NORMAL'
    else:
        with pytest.raises(ValueError,match='PRIOR_NETWORK_HANDLING_PROOF_REQUIRED'):restore_reviewed_alias_gate(store,plan,a)
        assert read_sealed(g)['state']=='UNKNOWN'
