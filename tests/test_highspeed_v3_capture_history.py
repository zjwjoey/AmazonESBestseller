"""Saved multi-capture protocol fixtures, never a LIVE acceptance claim."""
import json
from pathlib import Path
import pytest
from test_highspeed_v3_workflows import old_capture
from amazon_es_bestseller.highspeed.capture_history import live_observation_capture_pages
from amazon_es_bestseller.highspeed.v3_workflows import readonly_store
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.multisource.storage import digest,read_sealed,immutable_json,sealed


def two_saved_captures(tmp_path):
    root,mapping=old_capture(tmp_path,v3=True)
    with readonly_store(root) as s:
        old_page=s.pages('old')[0];old_ref=(old_page['journal_path'],digest(Path(old_page['journal_path']).read_bytes()))
    s=RunStore(root);source=s.get_run('old')['plan']['sources'][0];url=source['page_urls'][0]
    html=Path(old_page['evidence_path']).read_text(encoding='utf-8').replace('Exact source title','New source title')
    observed='2026-10-14T01:00:00Z'
    request=s.reserve_request('old',{'kind':'PAGE','url':url,'source_id':'home','source_type':'BEST_SELLERS','observed_at':observed,'page_key':'page'})
    receipt=s.save_response('old',request,{'text':html,'status_code':200,'access_state':'NORMAL','origin':'LIVE_CAPTURE','url':url,'source_id':'home','source_type':'BEST_SELLERS','observed_at':observed,'page_key':'page'})
    parsed=parse_page(html,source=source,run_id='old',page_url=url,page_number=1,observed_at=observed,evidence_path=receipt['html_path'],origin='LIVE_CAPTURE')
    for row in parsed['observations']:
        row.update(origin='LIVE_CAPTURE',capture_run_id='old',source_completeness_status='UNKNOWN',capture_receipt_reference={'path':receipt['evidence_path'],'sha256':digest(Path(receipt['evidence_path']).read_bytes())})
    s.commit_page('old','page',{**parsed['page'],'page_key':'page','source':source},parsed['observations']);s.close()
    return root,mapping,old_ref


def test_saved_old_and_revision_rows_bind_own_original_capture_not_latest(tmp_path):
    root,mapping,old_ref=two_saved_captures(tmp_path)
    with readonly_store(root) as s:
        rows=list(s.iter_observations('old'));assert len(rows)==2
        latest_ids={r['observation_id'] for r in read_sealed(s.pages('old')[0]['journal_path'])['observations']}
        # Regression evidence: the previous latest-only lookup lacks revision IDs.
        assert any(r['observation_id'] not in latest_ids for r in rows)
        bound=live_observation_capture_pages(s,'old',rows)
        assert bound[rows[0]['observation_id']]['journal_path']==old_ref[0]
        assert bound[rows[0]['observation_id']]['observed_at']=='2026-10-14T00:00:00Z'
        assert bound[rows[1]['observation_id']]['observed_at']=='2026-10-14T01:00:00Z'
        assert rows[1]['parent_observation_id']==rows[0]['observation_id']
        assert bound[rows[0]['observation_id']]['evidence_sha256']!=bound[rows[1]['observation_id']]['evidence_sha256']
    assert digest(Path(old_ref[0]).read_bytes())==old_ref[1]


@pytest.mark.parametrize('field,value',[('raw_sha256','0'*64),('observed_at','2026-10-14T02:00:00Z'),('parent_observation_id','unrelated'),('evidence_sha256','0'*64)])
def test_capture_history_rejects_missing_or_misbound_original_row(tmp_path,field,value):
    root,mapping,old_ref=two_saved_captures(tmp_path)
    with readonly_store(root) as s:
        rows=list(s.iter_observations('old'));rows[1][field]=value
        with pytest.raises(ValueError,match='CAPTURE_HISTORY'):
            live_observation_capture_pages(s,'old',rows)


def test_capture_history_rejects_changed_original_parent_body(tmp_path):
    root,mapping,old_ref=two_saved_captures(tmp_path)
    parent=read_sealed(old_ref[0])['page'];Path(parent['evidence_path']).write_bytes(b'changed')
    with readonly_store(root) as s:
        with pytest.raises(ValueError,match='PARENT_BODY_HASH_CONFLICT'):
            live_observation_capture_pages(s,'old',list(s.iter_observations('old')))


def test_capture_history_rejects_forged_ACP_receipt(tmp_path):
    root,mapping,old_ref=two_saved_captures(tmp_path)
    original=read_sealed(old_ref[0]);raw=original['observations'][0]
    # One unchanged ACP representation must never be rebound to a different server parent.
    raw['representation']='ACP_0'
    # A malformed receipt is rejected before a made-up parent can become eligible.
    raw['capture_receipt_reference']['sha256']='0'*64
    forged={**original,'observations':[raw]}
    path=Path(old_ref[0]).parent/'invalid_capture.json';immutable_json(path,sealed(forged))
    with readonly_store(root) as s:
        rows=list(s.iter_observations('old'));rows[0]['raw_sha256']=digest(raw)
        with pytest.raises(ValueError):live_observation_capture_pages(s,'old',rows)


def test_capture_history_cannot_substitute_latest_parent_for_old_server_raw(tmp_path):
    root,mapping,old_ref=two_saved_captures(tmp_path)
    original=read_sealed(old_ref[0])
    with readonly_store(root) as s:latest=s.pages('old')[0]
    forged={**original,'page':{**latest,'journal_path':None}}
    immutable_json(Path(old_ref[0]).parent/'wrong_latest_parent.json',sealed(forged))
    with readonly_store(root) as s:
        with pytest.raises(ValueError,match='SERVER_PARENT_CONFLICT'):
            live_observation_capture_pages(s,'old',list(s.iter_observations('old')))
