"""Actual URL regression; test scopes are TEST_ONLY and never live approvals."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed.v4_authorization import authorize_v4_request

def context(monkeypatch):
    pair=json.loads(Path('tests/fixtures/highspeed_v4/query_order_scope_stop.json').read_text('utf8'))
    source={'source_id':'TEST_ONLY_CHILD','snapshot_admission_root':'TEST_ONLY_ROOT','page_urls':[pair['requested_url']]}
    auth={'snapshot_contract_version':4,'collection_contract':'CURRENT_THREE_SOURCE_PAGE_SNAPSHOT_V1',
          'run_id':'TEST_ONLY','plan_sha256':'TEST_ONLY','sources':{'TEST_ONLY_ROOT':{'source':{'TEST_ONLY':True}}}}
    calls=[]
    monkeypatch.setattr('amazon_es_bestseller.highspeed.page_navigation.validate_admitted_source',lambda *args:calls.append(args))
    return pair,source,auth,calls

def test_actual_reordered_runtime_response_keeps_full_source_validation(monkeypatch):
    pair,source,auth,calls=context(monkeypatch)
    assert pair['observed_final_url'] not in source['page_urls']  # old failing comparison
    authorize_v4_request(pair['requested_url'],source,auth)
    authorize_v4_request(pair['observed_final_url'],source,auth)
    assert len(calls)==2

@pytest.mark.parametrize('change',[
    lambda u:u.replace('page=2','page=3'),lambda u:u.replace('i=kitchen','i=toys'),
    lambda u:u.replace('n%3A','n%3a'),lambda u:u+'&page=2',lambda u:u.replace('&qid=1792594246',''),
    lambda u:u+'&extra=1',lambda u:u.replace('amazon.es/s','amazon.es/b'),
    lambda u:u.replace('amazon.es','example.com')])
def test_reordering_does_not_authorize_changed_bytes_or_scope(monkeypatch,change):
    pair,source,auth,calls=context(monkeypatch)
    with pytest.raises(ValueError):authorize_v4_request(change(pair['observed_final_url']),source,auth)

def test_runtime_parent_receipt_validation_cannot_be_skipped(monkeypatch):
    pair,source,auth,calls=context(monkeypatch)
    def reject(*args):raise ValueError('TEST_ONLY_MISSING_PARENT_RECEIPT')
    monkeypatch.setattr('amazon_es_bestseller.highspeed.page_navigation.validate_admitted_source',reject)
    with pytest.raises(ValueError,match='MISSING_PARENT_RECEIPT'):authorize_v4_request(pair['observed_final_url'],source,auth)

def test_saved_uncommitted_page_replays_before_new_requests_without_crossing_department():
    from amazon_es_bestseller.highspeed.page_snapshot import prioritize_saved_pages
    a={'collection_category_key':'Hogar'};b={'collection_category_key':'DIY'}
    work=[(a,'new'),(a,'committed'),(a,'saved'),(b,'saved_other_department')]
    receipts=[{'request_url':u,'kind':'PAGE','status_code':200,'access_state':'NORMAL','origin':'LIVE_CAPTURE'}
              for u in ('committed','saved','saved_other_department')]
    result=prioritize_saved_pages(work,receipts,{'committed'},['Hogar','DIY'])
    assert [u for _,u in result]==['saved','new','committed','saved_other_department']
