"""T2 execution uses actual saved DOM through TEST_ONLY offline transports."""
from copy import deepcopy
from datetime import timedelta
import hashlib,json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
import pytest
from test_highspeed_v4_execution import setup_scope,FakeTransport,NOW
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.v4_t2_runner import V4T2Runner
from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
from amazon_es_bestseller.highspeed.v4_capacity import audit_capacity,execution_for_store
from amazon_es_bestseller.highspeed.v4_workflows import export_v4
from amazon_es_bestseller.multisource.storage import digest,immutable_json


def t2_scope(tmp_path,monkeypatch):
    plan,auth=setup_scope(tmp_path)
    plan['stage']=auth['stage']='T2';plan['target_unique_links']=auth['target_unique_links']=2000
    auth['plan_sha256']=digest(plan)
    # Stage validation is separately tested with real sealed reference packs.
    # This test-only stub enables simulated requests, never a LIVE CLI approval.
    monkeypatch.setattr('amazon_es_bestseller.highspeed.v4_authorization._verify_prerequisite',lambda *a:None)
    return plan,auth


def test_offline_t2_runner_resumes_without_duplicate_page_requests(tmp_path,monkeypatch):
    p,a=t2_scope(tmp_path,monkeypatch);s=RunStore(a['store_root']);clock=[NOW]
    try:
        t=FakeTransport(p,a);runner=V4T2Runner(s,clock=lambda:clock[0],sleep=lambda n:None)
        r=runner.run(p,authorization=a,transport=t)
        assert len(t.calls)==8 and r['summary']['raw_count']==336
        assert r['quality']['central_full_dom_review_passed']
        # A static page-only simulation cannot pass loading/capacity measurement.
        assert not r['capacity']['diagnostic_passed']
        assert 'T2_LOADING_MEASUREMENT_INCOMPLETE' in r['capacity']['errors']
        assert not r['capacity']['thirty_thousand_supply_demonstrated']
        clock[0]+=timedelta(seconds=10)
        again=runner.run(p,authorization=a,transport=t,resume=True)
        assert len(t.calls)==8 and again['run']['started_at']==r['run']['started_at']
        out=export_v4(s.pages(p['run_id']),list(s.iter_observations(p['run_id'])),p['sources'],tmp_path/'report',
            run_id=p['run_id'],stage='T2',capacity_execution=execution_for_store(s,p['run_id']))
        m=json.loads((Path(out['directory'])/'manifest.json').read_text('utf8'))
        assert m['passed'] is False and m['capacity_audit']['actual_requests']==8
        assert (Path(out['directory'])/'capacity_report_v4.json').is_file()
        m['passed']=True;m['capacity_audit']['diagnostic_passed']=True
        forged=Path(out['directory'])/'forged_manifest.json';immutable_json(forged,m)
        from amazon_es_bestseller.highspeed.v4_quality import verify_stage_reference
        with pytest.raises(ValueError,match='CAPACITY_RECOMPUTATION_FAILED'):
            verify_stage_reference({'path':str(forged),'sha256':hashlib.sha256(forged.read_bytes()).hexdigest()},'T2')
    finally:s.close()


@pytest.mark.parametrize('code',[403,429,503])
def test_t2_access_failure_stops_without_advancing_to_second_source(tmp_path,monkeypatch,code):
    p,a=t2_scope(tmp_path,monkeypatch);s=RunStore(a['store_root'])
    try:
        t=FakeTransport(p,a,code=code)
        r=V4T2Runner(s,clock=lambda:NOW,sleep=lambda n:None).run(p,authorization=a,transport=t)
        assert len(t.calls)==1 and s.get_gate()['state']!='NORMAL'
        assert r['capacity']['diagnostic_passed'] is False
        assert r['run']['T3_execution_enabled'] is False
    finally:s.close()


def test_t2_budget_exhaustion_preserves_original_start_and_stays_closed(tmp_path,monkeypatch):
    p,a=t2_scope(tmp_path,monkeypatch);p['max_requests_per_run']=a['max_requests_per_run']=2;a['plan_sha256']=digest(p)
    s=RunStore(a['store_root'])
    try:
        t=FakeTransport(p,a);runner=V4T2Runner(s,clock=lambda:NOW,sleep=lambda n:None)
        r=runner.run(p,authorization=a,transport=t);again=runner.run(p,authorization=a,transport=t,resume=True)
        assert len(t.calls)==2 and again['run']['started_at']==r['run']['started_at']
        assert not again['capacity']['diagnostic_passed']
    finally:s.close()


def test_t1_runner_cannot_be_used_as_implicit_t2_entry(tmp_path):
    p,a=setup_scope(tmp_path);p['stage']='T2';s=RunStore(a['store_root'])
    try:
        with pytest.raises(ValueError,match='T2_T3'):V4T1Runner(s).run(p,authorization=a,transport=FakeTransport(p,a))
    finally:s.close()


def test_t3_cli_is_closed_before_browser_or_authorization(tmp_path):
    from amazon_es_bestseller.highspeed.v4_cli import run_live_v4
    p,a=setup_scope(tmp_path);p['stage']='T3'
    plan=tmp_path/'t3.json';auth=tmp_path/'auth.json';immutable_json(plan,p);immutable_json(auth,a)
    args=SimpleNamespace(plan=str(plan),authorization=str(auth),root=a['store_root'],operation='run',live=True)
    with pytest.raises(ValueError,match='T3_EXECUTION_DISABLED'):run_live_v4(args)


def test_prerequisite_session_rechecks_all_changed_dependency_bytes(tmp_path,monkeypatch):
    from amazon_es_bestseller.highspeed.v4_authorization import prerequisite_review_session,_verify_prerequisite
    body=tmp_path/'actual.html';body.write_text('actual saved DOM','utf8')
    manifest=tmp_path/'manifest.json';immutable_json(manifest,{'stage':'T0'})
    reference={'path':str(manifest),'sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()};calls=[]
    def review(ref,stage):
        calls.append(body.read_text('utf8'))
        if len(calls)>1:raise ValueError('CHANGED_DOM')
        return {'pages':[{'evidence_path':str(body)}]}
    monkeypatch.setattr('amazon_es_bestseller.highspeed.v4_quality.verify_stage_reference',review)
    with prerequisite_review_session():
        _verify_prerequisite(reference,'T0');_verify_prerequisite(reference,'T0')
        assert len(calls)==1
        body.write_text('changed saved DOM','utf8')
        with pytest.raises(ValueError,match='CHANGED_DOM'):_verify_prerequisite(reference,'T0')
    assert len(calls)==2


def test_missing_t2_prerequisites_block_before_any_request(tmp_path):
    p,a=setup_scope(tmp_path);p['stage']=a['stage']='T2';a['plan_sha256']=digest(p);s=RunStore(a['store_root'])
    try:
        t=FakeTransport(p,a)
        with pytest.raises(ValueError,match='PRIOR_STAGE_REQUIRED'):V4T2Runner(s,clock=lambda:NOW).run(p,authorization=a,transport=t)
        assert not t.calls
    finally:s.close()


def test_t2_speed_subtracts_whole_first_capture_and_detects_receipt_body_tamper(tmp_path,monkeypatch):
    p,a=t2_scope(tmp_path,monkeypatch);s=RunStore(a['store_root']);clock=[NOW]
    class Advancing(FakeTransport):
        def fetch_page(self,url):
            value=super().fetch_page(url);clock[0]+=timedelta(seconds=5);return value
    try:
        r=V4T2Runner(s,clock=lambda:clock[0],sleep=lambda n:None).run(p,authorization=a,transport=Advancing(p,a))
        capacity=r['capacity']
        rows=list(s.iter_observations(p['run_id']))
        from amazon_es_bestseller.highspeed.v4_quality import build_candidates_v4
        candidates=build_candidates_v4(rows)
        target={c['normalized_product_url']:c['target_review_group'] for c in candidates if c['eligible_for_target']}
        first=min(x['observed_at'] for x in rows)
        initial={target[x['normalized_product_url']] for x in rows if x['observed_at']==first and x['normalized_product_url'] in target}
        assert len(initial)>1
        assert capacity['sample_new_review_groups']==len(set(target.values())-initial)
        assert capacity['minimum_request_interval_seconds']==5
        receipt=next(s.iter_responses(p['run_id']))
        from amazon_es_bestseller.multisource.storage import read_sealed,sealed,canonical_bytes
        receipt_path=Path(receipt['evidence_path']);original=receipt_path.read_bytes()
        changed={**read_sealed(receipt_path),'final_url':'https://www.amazon.es/s?k=unapproved'}
        receipt_path.write_bytes(canonical_bytes(sealed(changed)))
        redirected=audit_capacity(s.pages(p['run_id']),rows,p['sources'],execution_for_store(s,p['run_id']))
        assert 'REQUEST_NOT_IN_APPROVED_SCOPE' in redirected['errors']
        receipt_path.write_bytes(original)
        Path(receipt['html_path']).write_text('corrupt body','utf8')
        bad=audit_capacity(s.pages(p['run_id']),rows,p['sources'],execution_for_store(s,p['run_id']))
        assert not bad['diagnostic_passed'] and 'RECEIPT_BODY_SHA_MISMATCH' in bad['errors']
    finally:s.close()


def test_offline_prepare_uses_real_category_hrefs_not_root_ref_aliases(tmp_path,monkeypatch):
    from amazon_es_bestseller.highspeed.v4_t2_prepare import prepare_t2
    p,a=setup_scope(tmp_path);s=RunStore(a['store_root'])
    try:
        V4T1Runner(s,clock=lambda:NOW,sleep=lambda n:None).run(p,authorization=a,transport=FakeTransport(p,a))
        pack={'sources':p['sources'],'pages':s.pages(p['run_id'])}
        monkeypatch.setattr('amazon_es_bestseller.highspeed.v4_t2_prepare.verify_stage_reference',lambda *args:pack)
        result=prepare_t2({}, {},tmp_path/'out',tmp_path/'runtime',p['shared_access_gate_path'])
        plan=json.loads((Path(result['directory'])/'plan.pending.json').read_text('utf8'))
        auth=json.loads((Path(result['directory'])/'authorization.pending.json').read_text('utf8'))
        deep=[s for s in plan['sources'] if s.get('navigation_evidence')]
        assert len(deep)==4
        assert all(not urlsplit(s['actual_source_url']).path.split('/')[3].startswith('ref=') for s in deep)
        assert plan['stage']=='T2' and not plan['live_enabled'] and not auth['approved']
        assert plan['max_requests_per_run']==200 and plan['max_duration_hours']==1
        assert plan['max_native_requests_per_source']==20
        assert result['live_started'] is False
    finally:s.close()


def test_t2_resume_retains_both_explicit_approvals_without_resetting_start(tmp_path,monkeypatch):
    p,a=t2_scope(tmp_path,monkeypatch);a['expires_at']=(NOW+timedelta(seconds=45)).isoformat()
    s=RunStore(a['store_root']);clock=[NOW]
    class Advancing(FakeTransport):
        def fetch_page(self,url):
            value=super().fetch_page(url);clock[0]+=timedelta(seconds=5);return value
    try:
        t=Advancing(p,a);runner=V4T2Runner(s,clock=lambda:clock[0],sleep=lambda n:None)
        first=runner.run(p,authorization=a,transport=t)
        clock[0]=NOW+timedelta(minutes=1)
        renewed={**a,'approval_id':'TEST_ONLY_RENEWED','reviewed_at':clock[0].isoformat(),'expires_at':(clock[0]+timedelta(hours=1)).isoformat()}
        second=runner.run(p,authorization=renewed,transport=t,resume=True,reverify_source_ids=[p['sources'][0]['source_id']])
        assert len(t.calls)==9 and len(second['run']['execution_approval_references'])==2
        assert second['run']['started_at']==first['run']['started_at']
        assert 'REQUEST_OUTSIDE_APPROVED_TIME' not in second['capacity']['errors']
        assert 'EXECUTION_APPROVAL_REQUIRED' not in second['capacity']['errors']
    finally:s.close()
