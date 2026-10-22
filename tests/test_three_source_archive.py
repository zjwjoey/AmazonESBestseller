"""TEST_ONLY receipt envelopes over real DOM; no actual LIVE proof produced."""
import hashlib
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed.three_archive import historical_context
from amazon_es_bestseller.multisource.storage import immutable_json,sealed


def envelope(tmp_path,**updates):
    tmp_path.mkdir(parents=True,exist_ok=True)
    body=tmp_path/'real_saved_DOM.html'
    body.write_bytes((Path(__file__).parent/'fixtures/highspeed_v4/F01_best_root_complete.html').read_bytes())
    url='https://www.amazon.es/gp/bestsellers'
    value={'run_id':'TEST_ONLY_ARCHIVE','request_id':'req_TEST_ONLY','source_id':'src_TEST_ONLY',
        'source_type':'BEST_SELLERS','origin':'LIVE_CAPTURE','kind':'PAGE','request_url':url,'final_url':url,
        'observed_at':'2026-10-14T01:00:00Z','status_code':200,'access_state':'NORMAL',
        'html_path':str(body),'sha256':hashlib.sha256(body.read_bytes()).hexdigest(),**updates}
    q={'run_id':value['run_id'],'url':value['request_url'],'kind':'PAGE','source_id':value['source_id'],
       'observed_at':value['observed_at'],'origin':'LIVE_CAPTURE'}
    path=tmp_path/'runs/TEST_ONLY_ARCHIVE/responses/req_TEST_ONLY.json'
    immutable_json(path,sealed(value));immutable_json(path.parent.parent/'requests/req_TEST_ONLY.reserved.json',sealed(q))
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},body


def test_archived_context_preserves_original_origin_without_new_candidates(tmp_path):
    ref,body=envelope(tmp_path);source,page=historical_context(ref)
    assert source['historical_capture_reference']==ref and source['enabled'] is False
    assert page['origin']=='OFFLINE_FIXTURE' and page['evidence_path']==str(body)
    assert page['status']=='VERIFIED_WITH_PRODUCTS'


@pytest.mark.parametrize('changes',[{'status_code':403},{'access_state':'UNKNOWN'},{'origin':'HISTORICAL_REPLAY'},
    {'final_url':'https://www.amazon.es/s?k=unapproved'},{'source_type':'NEW_RELEASES'}])
def test_nonactual_wrong_or_restricted_archive_is_rejected(tmp_path,changes):
    ref,_=envelope(tmp_path,**changes)
    with pytest.raises(ValueError):historical_context(ref)


def test_archive_receipt_and_body_tamper_is_not_relabelled_as_verified(tmp_path):
    ref,body=envelope(tmp_path);body.write_text('changed offline test copy','utf8')
    with pytest.raises(ValueError,match='BODY_SHA'):historical_context(ref)
