from copy import deepcopy
from pathlib import Path
import hashlib
import pytest
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.v3_quality import audit_observations

SOURCE={'source_id':'s','source_type':'BEST_SELLERS','actual_source_url':'https://www.amazon.es/gp/bestsellers','parser_profile':'V3'}

def actual_row():
    card=(Path(__file__).parent/'fixtures/highspeed/real_v3_rating_best_sellers.html').read_text(encoding='utf8')
    body='<h1>Los más vendidos</h1><div id="zg">'+card+'</div>'
    row=parse_page(body,source=SOURCE,run_id='r',snapshot_id='s',page_url=SOURCE['actual_source_url'],page_number=1,observed_at='2026-10-14T00:00:00Z',evidence_path='derived_fixture.html')['observations'][0]
    return row,body

def audit(row,body,**kw):
    return audit_observations([row],[SOURCE],evidence_loader=lambda _:body,receipt_validator=lambda r,s:True,**kw)

def test_real_dom_values_verified():
    row,body=actual_row(); result=audit(row,body)
    assert result['ok'] and result['counts']['dom_verified_filled_ratings']==1
    assert result['counts']['source_completeness_UNKNOWN']==1

@pytest.mark.parametrize('field,value,code',[('rating_value',1.0,'RATING_DOM_OR_BINDING_MISMATCH'),('review_count',1,'RATING_DOM_OR_BINDING_MISMATCH'),('raw_product_url','/dp/B012345678','CARD_PRODUCT_HREF_MISMATCH'),('evidence_sha256','0'*64,'HTML_SHA_MISMATCH'),('representation_slot',1,'CARD_SLOT_NOT_FOUND'),('ranking_position',9,'CARD_RANK_MISMATCH'),('rating_observed_at','2026-10-13T00:00:00Z','RATING_CAPTURE_TIME_MISMATCH'),('rank_context','https://www.amazon.es/gp/bestsellers/bogus','RANK_CONTEXT_MISMATCH')])
def test_reject_tampered_observation(field,value,code):
    row,body=actual_row();row[field]=value
    assert any(f['code']==code for f in audit(row,body)['findings'])

def test_receipts_not_self_certified():
    row,body=actual_row()
    result=audit_observations([row],[SOURCE],evidence_loader=lambda _:body)
    assert not result['ok'] and result['findings'][0]['code']=='CAPTURE_RECEIPT_UNVERIFIED'

def test_bounded_cache_reuses_same_source_body():
    row,body=actual_row();other=deepcopy(row);other['observation_id']='other'
    calls=[]
    result=audit_observations([row,other],[SOURCE],evidence_loader=lambda r:calls.append(r) or body,receipt_validator=lambda r,s:True)
    assert result['ok'] and len(calls)==1

def test_cross_source_context_not_reused():
    row,body=actual_row();other=deepcopy(row);other['source_id']='foreign'
    foreign={**SOURCE,'source_id':'foreign','actual_source_url':'https://www.amazon.es/gp/bestsellers/hpc'}
    result=audit_observations([row,other],[SOURCE,foreign],evidence_loader=lambda _:body,receipt_validator=lambda r,s:True)
    assert result['counts']['dom_verified_observations']==1
    assert any(f['code']=='SOURCE_CONTEXT_MISMATCH' for f in result['findings'])

def test_no_false_complete_claim():
    row,body=actual_row();row['source_completeness_status']='COMPLETE'
    result=audit(row,body)
    assert not result['ok'] and any(f['code']=='SOURCE_COMPLETENESS_NOT_PROVEN' for f in result['findings'])

def test_page_context_change_cannot_reuse_proof():
    row,body=actual_row();row['page_url']='https://www.amazon.es/gp/bestsellers/hpc'
    assert not audit(row,body)['ok']

def test_v3_tracking_suffix_recomputed_against_same_card():
    row,body=actual_row()
    assert '/ref=zg_' in row['raw_product_url']
    assert '/ref=zg_' not in row['normalized_product_url']
    assert audit(row,body)['ok']


def test_v3_variant_query_tampering_rejected():
    row,body=actual_row()
    changed=body.replace('?psc=1','?psc=1&amp;variant=red')
    fresh=parse_page(changed,source=SOURCE,run_id='r',snapshot_id='red',page_url=SOURCE['actual_source_url'],page_number=1,observed_at='2026-10-14T00:00:00Z',evidence_path='derived_fixture_red.html')['observations'][0]
    assert 'variant=red' in fresh['normalized_product_url'] and audit(fresh,changed)['ok']
    fresh['normalized_product_url']=fresh['normalized_product_url'].replace('variant=red','variant=blue')
    assert any(f['code']=='NORMALIZED_URL_MISMATCH' for f in audit(fresh,changed)['findings'])
