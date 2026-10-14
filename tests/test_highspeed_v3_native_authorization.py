"""Protocol-only authorization tests; mock POST is not a LIVE contract."""
import hashlib,json
from pathlib import Path
import pytest
from types import SimpleNamespace
from amazon_es_bestseller.highspeed.authorization import authorize_request
from amazon_es_bestseller.highspeed.cli import save_native_contract
from amazon_es_bestseller.transport.playwright import PlaywrightTransport
from test_highspeed_v3_native_preparation import saved_descriptor


def carousel(tmp_path):
    descriptor,parent=saved_descriptor()
    owner=PlaywrightTransport._saved_carousel_owner(descriptor,parent)
    endpoint='https://www.amazon.es'+owner['owner_path'].rstrip('/')+'/mock-captured-fetch'
    payload='{ "opaque": "actual mock bytes" }'
    native={'url':endpoint,'method':'POST','payload':payload,'headers':{'content-type':'application/json','x-amz-acp-params':owner['owner_params']},'parent_page_url':parent,'resource_type':'fetch','request_contract_source':'NATIVE_BROWSER_REQUEST_ABORTED','ranking_context_binding':owner['ranking_context_binding'],'native_carousel_owner':{k:owner[k] for k in ('owner_id','owner_path','owner_params','carousel_options_sha256')}}
    save_native_contract(SimpleNamespace(root=tmp_path),{'run_id':'r'},native)
    descriptor.update(native)
    source={'source_id':'new','source_type':'NEW_RELEASES','parser_profile':'V3'}
    auth={'run_id':'r','snapshot_contract_version':3,'sources':{'new':{'page_urls':[parent],'allow_native_acp':True,'parser_profile':'V3','source_type':'NEW_RELEASES'}}}
    return endpoint,descriptor,source,auth


def test_v3_byte_exact_carousel_contract_authorizes_non_grid_endpoint(tmp_path):
    endpoint,d,s,a=carousel(tmp_path)
    authorize_request(endpoint,s,a,kind='ACP',descriptor=d)


@pytest.mark.parametrize('field,value',[
 ('payload','{"opaque":"forged payload"}'),('headers',{'x-amz-acp-params':'forged'}),
 ('source_id','other'),('request_contract_source','NATIVE_BROWSER_CONTRACT_DERIVED_BATCH')])
def test_v3_carousel_mutated_descriptor_rejected(tmp_path,field,value):
    endpoint,d,s,a=carousel(tmp_path);d[field]=value
    if field=='payload':d['request_payload_sha256']=hashlib.sha256(value.encode()).hexdigest()
    with pytest.raises(ValueError):authorize_request(endpoint,s,a,kind='ACP',descriptor=d)


@pytest.mark.parametrize('section,field,value',[
 ('native_carousel_owner','owner_id','neighbor'),('native_carousel_owner','owner_path','/acp/neighbor'),
 ('native_carousel_owner','owner_params','neighborparams'),('ranking_context_binding','source_id','neighbor'),
 ('ranking_context_binding','ranking_context_url','https://www.amazon.es/gp/new-releases/unrelated'),
 ('ranking_context_binding','page_evidence_sha256','0'*64)])
def test_v3_forged_saved_owner_even_with_valid_file_hash_rejected(tmp_path,section,field,value):
    endpoint,d,s,a=carousel(tmp_path)
    path=Path(d['native_contract_evidence_path']);native=json.loads(path.read_text(encoding='utf8'))
    native[section][field]=value;d[section]=dict(native[section])
    path.write_text(json.dumps(native),encoding='utf8');d['native_contract_evidence_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):authorize_request(endpoint,s,a,kind='ACP',descriptor=d)


def test_carousel_does_not_bypass_v2_endpoint_guard(tmp_path):
    endpoint,d,s,a=carousel(tmp_path);a.pop('snapshot_contract_version');a['sources']['new'].pop('parser_profile');s.pop('parser_profile')
    with pytest.raises(ValueError):authorize_request(endpoint,s,a,kind='ACP',descriptor=d)



@pytest.mark.parametrize('field,value',[
 ('run_id','prior-run'),('url','https://www.amazon.es/acp/neighbor/mock-captured-fetch'),('method','GET')])
def test_saved_capture_wrong_run_path_or_method_rejected_even_if_refs_rehashed(tmp_path,field,value):
    endpoint,d,s,a=carousel(tmp_path);path=Path(d['native_contract_evidence_path'])
    native=json.loads(path.read_text(encoding='utf8'));native[field]=value;d[field]=value
    path.write_text(json.dumps(native),encoding='utf8');d['native_contract_evidence_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):authorize_request(value if field=='url' else endpoint,s,a,kind='ACP',descriptor=d)


def test_static_acp_allowlist_cannot_bypass_v3_owner_guard(tmp_path):
    endpoint,d,s,a=carousel(tmp_path);a['sources']['new'].update(allow_acp=True,acp_urls=[endpoint])
    d['native_carousel_owner']=dict(d['native_carousel_owner'],owner_id='forged-neighbor')
    with pytest.raises(ValueError):authorize_request(endpoint,s,a,kind='ACP',descriptor=d)


def test_parent_html_tamper_invalidates_carousel_authorization(tmp_path):
    endpoint,d,s,a=carousel(tmp_path);original=Path(d['evidence_path']);other=tmp_path/'modified.html'
    other.write_bytes(original.read_bytes()+b'<!-- tampered -->');d['evidence_path']=str(other)
    with pytest.raises(ValueError,match='OWNER_EVIDENCE_UNVERIFIED'):authorize_request(endpoint,s,a,kind='ACP',descriptor=d)


def v3_pool(tmp_path, *, stage='T0', origin='OFFLINE_FIXTURE', count=2, derivation=False):
    from test_highspeed_runner import plan
    from amazon_es_bestseller.highspeed.v3_links import normalize_product_url_v3,NORMALIZER_VERSION
    p=plan(tmp_path,stage);p.update(snapshot_contract_version=3,normalizer_version=NORMALIZER_VERSION)
    p['sources'][0]['parser_profile']='V3'
    def save(name,value,lines=False):
        path=tmp_path/(name+('.jsonl' if lines else '.json'))
        path.write_text('\n'.join(json.dumps(r) for r in value) if lines else json.dumps(value),encoding='utf8')
        return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    observations=[{'run_id':p['run_id'],'origin':origin,'source_id':'home','source_type':'BEST_SELLERS','raw_product_url':'/dp/Opaque/ref=zg_bs_g_123_1/524-6514838-9761305?color='+color,'is_derivation':derivation} for color in ('red','blue')]
    candidates=[]
    for o in observations:
        n=normalize_product_url_v3(o['raw_product_url']);candidates.append({'candidate_id':n['candidate_id'],'normalized_product_url':n['normalized_product_url']})
    return {'run_id':p['run_id'],'origin':origin,'unique_valid_links':count,
        'manifest':save('manifest',{'run_id':p['run_id'],'stage':stage,'origin':origin,'plan':p}),
        'quality_report':save('quality',{'run_id':p['run_id'],'gate_passed':True,'unique_valid_links':count}),
        'observations':save('observations',observations,True),'candidates':save('candidates',candidates,True)}


def test_v3_prior_pool_keeps_distinct_color_candidates_not_v2_coarse_groups(tmp_path):
    from amazon_es_bestseller.highspeed.authorization import _artifact_link_pool
    record=v3_pool(tmp_path)
    _,_,urls=_artifact_link_pool(record,'T0')
    assert len(urls)==2


def test_v3_prior_pool_rejects_false_count_from_coarse_alias_group(tmp_path):
    from amazon_es_bestseller.highspeed.authorization import _artifact_link_pool
    record=v3_pool(tmp_path,count=1)
    with pytest.raises(ValueError,match='DECLARED_LINK_COUNT'):_artifact_link_pool(record,'T0')


def test_v3_prior_live_stage_cannot_smuggle_derivation_as_live(tmp_path):
    from amazon_es_bestseller.highspeed.authorization import _artifact_link_pool
    record=v3_pool(tmp_path,stage='T1',origin='LIVE_CAPTURE',derivation=True)
    with pytest.raises(ValueError,match='FRESH_LIVE_OBSERVATION_REQUIRED'):_artifact_link_pool(record,'T1')



def test_v3_explicit_normalizer_approval_required_even_for_live_plan(tmp_path):
    from test_highspeed_runner import plan,approval,NOW
    from amazon_es_bestseller.highspeed.authorization import validate_authorization
    from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION
    p=plan(tmp_path,'T1');p.update(snapshot_contract_version=3,normalizer_version=NORMALIZER_VERSION)
    p['sources'][0]['parser_profile']='V3'
    a=approval(p,tmp_path);a.pop('snapshot_contract_version')
    with pytest.raises(ValueError,match='EXPLICIT_PROFILE_AUTHORIZATION_REQUIRED'):
        validate_authorization(p,a,{'state':'NORMAL'},now=NOW)


@pytest.mark.parametrize('field,value',[('parent_run_id','legacy'),('continuation_mode','legacy-replay'),('seed_reference',{'path':'legacy'}),('origin','OFFLINE_FIXTURE')])
def test_v3_fresh_t3_approval_rejects_history_import_aliases(tmp_path,field,value):
    from test_highspeed_runner import plan,approval,NOW
    from amazon_es_bestseller.highspeed.authorization import validate_authorization
    from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION
    p=plan(tmp_path,'T3');p.update(snapshot_contract_version=3,normalizer_version=NORMALIZER_VERSION,origin='LIVE_CAPTURE',new_batch_required=True)
    p['sources'][0]['parser_profile']='V3';p['sources'][0].pop('offline_pages')
    p[field]=value;a=approval(p,tmp_path);a['sources']['home']['parser_profile']='V3'
    with pytest.raises(ValueError,match='FRESH_BATCH_REQUIRED'):
        validate_authorization(p,a,{'state':'NORMAL'},now=NOW)
