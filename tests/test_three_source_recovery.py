"""TEST_ONLY transports over saved source DOM; no live recovery or traffic."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import pytest
from test_three_source_contract import scope
from test_highspeed_v4_execution import NOW
from test_highspeed_recovery import Transport
from amazon_es_bestseller.highspeed.recovery import recheck_network_error
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.multisource.storage import atomic_json,digest,read_sealed,sealed


def setup(tmp_path,monkeypatch):
    p,a=scope(tmp_path)
    a.update(stage='T3',collection_contract=p['collection_contract'],source_targets=deepcopy(p['source_targets']),
        source_budgets=deepcopy(p['source_budgets']),campaign_id=p['campaign_id'],campaign_created_at=p['campaign_created_at'],
        task_reference=p['task_reference'],sources={s['source_id']:a['sources'][s['source_id']] for s in p['sources']})
    for k in ('max_requests_per_run','max_requests_per_day','max_pages_per_run','target_unique_links'):a[k]=p[k]
    a['plan_sha256']=digest(p)
    monkeypatch.setattr('amazon_es_bestseller.highspeed.v4_authorization._verify_prerequisite',lambda *args:None)
    from amazon_es_bestseller.highspeed.budgets import aware_time
    monkeypatch.setattr('amazon_es_bestseller.highspeed.recovery.aware_time',lambda v=None:aware_time(v) if v else NOW)
    shared=Path(p['shared_access_gate_path']);gate={'state':'NETWORK_ERROR','observed_at':(NOW-timedelta(minutes=1)).isoformat()}
    atomic_json(shared,sealed(gate));s=p['sources'][0];store=RunStore(a['store_root'])
    store.begin_run(p,started_at=(NOW-timedelta(minutes=2)).isoformat())
    recovery={'approved':True,'agent_verification_authorized':True,'gate_sha256':digest(gate),
        'store_root':str(store.root),'run_id':p['run_id'],'approved_by':'TEST_ONLY','approval_id':'TEST_ONLY_recovery',
        'reviewed_at':(NOW-timedelta(seconds=1)).isoformat(),'expires_at':a['expires_at'],
        'max_attempts':1,'source_id':s['source_id'],'page_url':s['actual_source_url']}
    normal={'status_code':200,'url':s['actual_source_url'],'text':Path(a['sources'][s['source_id']]['evidence']['path']).read_text('utf8')}
    return store,p,a,recovery,shared,s,normal


def test_v4_saved_ranking_dom_recovers_with_actual_v4_proof_and_original_start(tmp_path,monkeypatch):
    store,p,a,r,shared,s,normal=setup(tmp_path,monkeypatch)
    try:
        started=store.get_run(p['run_id'])['started_at']
        recheck_network_error(store,p,a,r,Transport([normal]))
        evidence=read_sealed(read_sealed(shared)['agent_recovery_evidence'])
        assert evidence['verified_parser_contract']==4
        assert evidence['agent_handled'] and not evidence['human_handled']
        assert store.get_run(p['run_id'])['started_at']==started
        assert store.get_gate()['state']=='NORMAL'
    finally:store.close()


def test_v4_recovery_cannot_borrow_exhausted_family_page_budget(tmp_path,monkeypatch):
    store,p,a,r,shared,s,normal=setup(tmp_path,monkeypatch)
    try:
        for i in range(p['source_budgets'][s['source_type']]['PAGE']):
            store.reserve_request(p['run_id'],{'kind':'PAGE','url':s['actual_source_url'],
                'observed_at':(NOW-timedelta(minutes=1,seconds=i*3)).isoformat(),
                'source_type':s['source_type'],'source_id':s['source_id']})
        transport=Transport([normal])
        with pytest.raises(ValueError,match='THREE_SOURCE_PAGE_BUDGET_EXHAUSTED'):
            recheck_network_error(store,p,a,r,transport,sleep=lambda n:None)
        assert not transport.calls
        assert read_sealed(shared)['state']=='NETWORK_ERROR'
    finally:store.close()


def test_v4_recovery_preserves_original_duration_deadline(tmp_path,monkeypatch):
    store,p,a,r,shared,s,normal=setup(tmp_path,monkeypatch)
    try:
        store.db.execute('UPDATE runs SET started_at=? WHERE run_id=?',
            ((NOW-timedelta(hours=p['max_duration_hours'],seconds=1)).isoformat(),p['run_id']));store.db.commit()
        transport=Transport([normal])
        with pytest.raises(ValueError,match='MAX_DURATION_REACHED'):
            recheck_network_error(store,p,a,r,transport)
        assert not transport.calls
    finally:store.close()


def test_v4_recovery_receipt_retains_reservation_time_after_slow_fetch(tmp_path,monkeypatch):
    store,p,a,r,shared,s,normal=setup(tmp_path,monkeypatch)
    from amazon_es_bestseller.highspeed.budgets import aware_time
    clock=[NOW]
    monkeypatch.setattr('amazon_es_bestseller.highspeed.recovery.aware_time',lambda v=None:aware_time(v) if v else clock[0])
    monkeypatch.setattr('amazon_es_bestseller.highspeed.recovery.utc_text',
        lambda v=None:(aware_time(v) if v else clock[0]).isoformat().replace('+00:00','Z'))
    class Slow(Transport):
        def fetch_page(self,url):
            value=super().fetch_page(url);clock[0]+=timedelta(seconds=5);return value
    try:
        receipt=recheck_network_error(store,p,a,r,Slow([normal]))
        assert receipt['observed_at']==store.requests(p['run_id'])[0]['observed_at']
    finally:store.close()


@pytest.mark.parametrize('code,text',[(403,'Access Denied'),(429,'Too Many Requests'),(200,'Robot Check CAPTCHA')])
def test_v4_recovery_never_refreshes_an_access_restriction(tmp_path,monkeypatch,code,text):
    store,p,a,r,shared,s,normal=setup(tmp_path,monkeypatch)
    try:
        transport=Transport([{**normal,'status_code':code,'text':text},normal])
        r['max_attempts']=2
        with pytest.raises(ValueError,match='RECOVERY_STOPPED'):
            recheck_network_error(store,p,a,r,transport)
        assert len(transport.calls)==1
        assert read_sealed(shared)['state']!='NORMAL'
    finally:store.close()


def test_v4_recovery_rejects_a_new_local_challenge_even_if_shared_gate_is_network_error(tmp_path,monkeypatch):
    store,p,a,r,shared,s,normal=setup(tmp_path,monkeypatch)
    try:
        store.set_gate('CHALLENGE')
        transport=Transport([normal])
        with pytest.raises(ValueError,match='RECOVERY_LOCAL_GATE'):
            recheck_network_error(store,p,a,r,transport)
        assert not transport.calls
        assert store.get_gate()['state']=='CHALLENGE'
    finally:store.close()
