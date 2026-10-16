"""Pure observation views must match the complete candidate projection."""
from pathlib import Path
import pytest
from test_highspeed_store import setup,page
from amazon_es_bestseller.highspeed.store import RunStore


def original_rows(store,run_id):
    # Run the original projection algorithm against the same immutable inputs.
    import json
    views={c['candidate_id']:c for c in store.iter_candidates(run_id)}
    pages={p['page_key']:p for p in store.pages(run_id)}
    result=[]
    for row in store.db.execute('SELECT data_json,page_key FROM observations WHERE run_id=? ORDER BY rowid',(run_id,)):
        value=json.loads(row[0]);view=views.get(value.get('candidate_id'))
        if 'source_evidence_eligible' not in value:
            p=pages.get(row[1],{})
            value['source_evidence_eligible']=(p.get('access_status')=='NORMAL' and p.get('source_type_verified') is True and p.get('source_context_valid') is True)
        if view:
            value.setdefault('original_possible_duplicate_key',value.get('possible_duplicate_key'))
            value.setdefault('original_normalizer_version',value.get('normalizer_version'))
            value.update({k:view[k] for k in ('possible_duplicate_key','target_group_key','target_eligible',
                'url_identity_status','final_product_url','current_normalizer_version','possible_duplicate_key_version')})
            if view['url_identity_status']=='VERIFIED_SAME_DESTINATION':
                value['original_dedup_status']=value['dedup_status'];value['dedup_status']='VERIFIED_SAME_DESTINATION'
        if value['source_evidence_eligible'] is False:value['target_eligible']=False
        result.append(value)
    return result


def test_identity_projection_does_not_decode_first_observation_twice(tmp_path,monkeypatch):
    s,p,source=setup(tmp_path)
    try:
        page(s,p,source,['/dp/B012345678','/dp/B012345678?tag=x','/product/other',None])
        expected=original_rows(s,'run')
        monkeypatch.setattr(s,'iter_candidates',lambda run:(_ for _ in ()).throw(AssertionError('unnecessary complete candidate payload decode')))
        assert list(s.iter_observations('run'))==expected
    finally:s.close()


def test_current_eligibility_rows_do_not_require_all_page_payloads(tmp_path,monkeypatch):
    s,p,source=setup(tmp_path)
    try:
        page(s,p,source,['/product/a','/product/b'])
        expected=original_rows(s,'run')
        monkeypatch.setattr(s,'pages',lambda run:(_ for _ in ()).throw(AssertionError('unnecessary full page JSON decode')))
        assert list(s.iter_observations('run'))==expected
    finally:s.close()


def test_legacy_missing_eligibility_still_checks_actual_page(tmp_path):
    import json
    s,p,source=setup(tmp_path)
    try:
        page(s,p,source,['/product/a','/product/a?tag=x','/product/b'])
        for row in s.db.execute('SELECT observation_id,data_json FROM observations').fetchall():
            value=json.loads(row[1]);value.pop('source_evidence_eligible',None)
            s.db.execute('UPDATE observations SET data_json=? WHERE observation_id=?',(json.dumps(value),row[0]))
        assert list(s.iter_observations('run'))==original_rows(s,'run')
    finally:s.close()
