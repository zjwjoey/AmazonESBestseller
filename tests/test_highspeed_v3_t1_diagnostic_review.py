"""Explicit user review preserves a failed T1 diagnostic page, never stage QA."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timezone
import hashlib,json
import pytest
from amazon_es_bestseller.highspeed.runner import reviewed_t1_diagnostic_failures
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.contracts import new_plan
from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.multisource.storage import immutable_json,sealed

def ref(path):return {'path':str(Path(path).resolve()),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}

def fixture_review(tmp_path):
    body=Path('tests/fixtures/highspeed/real_v3_t1_movers_shakers_empty_unverified.html').read_text(encoding='utf8')
    url='https://www.amazon.es/gp/movers-and-shakers';source={'source_id':'move','source_type':'MOVERS_SHAKERS','actual_source_url':url,'page_urls':[url],'parser_profile':'V3','research_category':None}
    plan=new_plan('diagnostic_fixture',stage='T1',origin='LIVE_CAPTURE',snapshot_contract_version=3,normalizer_version=NORMALIZER_VERSION,sources=[source],probe_all_reviewed_page_urls=True)
    store=RunStore(tmp_path/'store');store.begin_run(plan)
    key=hashlib.sha256(('move\0'+url).encode()).hexdigest();observed='2026-10-14T10:55:28.718027Z'
    request=store.reserve_request(plan['run_id'],{'kind':'PAGE','url':url,'source_id':'move','source_type':'MOVERS_SHAKERS','observed_at':observed,'page_key':key})
    receipt=store.save_response(plan['run_id'],request,{'kind':'PAGE','text':body,'status_code':200,'access_state':'NORMAL','origin':'LIVE_CAPTURE','url':url,'request_url':url,'final_url':url,'source_id':'move','source_type':'MOVERS_SHAKERS','observed_at':observed,'page_key':key})
    parsed=parse_page(body,source=source,run_id=plan['run_id'],page_url=url,page_number=1,observed_at=observed,evidence_path=receipt['html_path'],origin='LIVE_CAPTURE')
    store.commit_page(plan['run_id'],key,{**parsed['page'],'page_key':key,'source':source},[])
    page=store.pages(plan['run_id'])[0]
    review={'kind':'T1_DIAGNOSTIC_FAILED_PAGE_RETAINED_APPROVAL','approved':True,'approved_by':'user_in_current_conversation','approval_id':'user_review_fixture','run_id':plan['run_id'],'store_root':str(store.root.resolve()),'reviewed_at':'2026-10-14T11:00:00Z','expires_at':'2026-10-14T20:54:00Z','source_id':'move','source_type':'MOVERS_SHAKERS','page_url':url,'page_key':key,'original_page_journal_reference':ref(page['journal_path']),'capture_receipt_reference':ref(receipt['evidence_path']),'html_reference':ref(receipt['html_path']),'preserve_failed_unknown':True,'no_quality_gate_relaxation':True,'no_extra_source_scope':True}
    return store,plan,review

def approval(tmp_path,review):
    path=tmp_path/'review.json';immutable_json(path,sealed(review))
    return {'expires_at':'2026-10-14T20:54:00Z','t1_diagnostic_failure_reviews':[ref(path)]}

NOW=datetime(2026,10,14,11,30,tzinfo=timezone.utc)

def test_explicit_user_review_retains_failed_page_without_mutation(tmp_path):
    store,plan,review=fixture_review(tmp_path)
    try:
        before=ref(store.pages(plan['run_id'])[0]['journal_path'])
        result=reviewed_t1_diagnostic_failures(store,plan,approval(tmp_path,review),{'state':'NORMAL'},now=NOW)
        assert len(result)==1 and result[0]['page_key']==review['page_key']
        page=store.pages(plan['run_id'])[0]
        assert page['status']=='FAILED' and page['page_complete'] is False and not page.get('terminal_for_snapshot')
        assert ref(page['journal_path'])==before
    finally:store.close()

@pytest.mark.parametrize('key,value',[('approved',False),('approved_by','agent'),('run_id','other'),('store_root','other'),('source_id','other'),('source_type','MOST_GIFTED'),('page_url','https://www.amazon.es/gp/movers-and-shakers/other'),('page_key','other'),('preserve_failed_unknown',False),('no_quality_gate_relaxation',False),('no_extra_source_scope',False),('reviewed_at','2026-10-15T00:00:00Z'),('expires_at','2026-10-14T10:00:00Z'),('expires_at','2026-10-15T00:00:00Z')])
def test_invalid_review_cannot_skip(tmp_path,key,value):
    store,plan,review=fixture_review(tmp_path);review[key]=value
    try:
        with pytest.raises(ValueError):reviewed_t1_diagnostic_failures(store,plan,approval(tmp_path,review),{'state':'NORMAL'},now=NOW)
    finally:store.close()

@pytest.mark.parametrize('stage',['T0','T2','T3'])
def test_review_never_applies_outside_t1(tmp_path,stage):
    store,plan,review=fixture_review(tmp_path);plan['stage']=stage
    try:
        with pytest.raises(ValueError):reviewed_t1_diagnostic_failures(store,plan,approval(tmp_path,review),{'state':'NORMAL'},now=NOW)
    finally:store.close()

def test_missing_review_does_not_skip_and_blocked_gate_rejects(tmp_path):
    store,plan,review=fixture_review(tmp_path)
    try:
        assert reviewed_t1_diagnostic_failures(store,plan,{}, {'state':'NORMAL'},now=NOW)==[]
        with pytest.raises(ValueError):reviewed_t1_diagnostic_failures(store,plan,approval(tmp_path,review),{'state':'CHALLENGE'},now=NOW)
    finally:store.close()

@pytest.mark.parametrize('field',['original_page_journal_reference','capture_receipt_reference','html_reference'])
def test_exact_reference_hash_required(tmp_path,field):
    store,plan,review=fixture_review(tmp_path);review[field]['sha256']='0'*64
    try:
        with pytest.raises(ValueError):reviewed_t1_diagnostic_failures(store,plan,approval(tmp_path,review),{'state':'NORMAL'},now=NOW)
    finally:store.close()
