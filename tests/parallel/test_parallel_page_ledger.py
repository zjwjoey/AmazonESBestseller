"""Offline fault and contention tests for the non-live PAGE queue prototype."""
from concurrent.futures import ThreadPoolExecutor
import hashlib,json
import pytest
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger
from amazon_es_bestseller.multisource.storage import digest


def ledger(tmp_path, **kwargs):
    return ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='original',
                              deadline=1000,page_budget=kwargs.get('budget',10),min_interval=3)


def pages(*urls):
    return [{'url':url,'category':'test','family':'BEST_SELLERS'} for url in urls]


def alias_proof(tmp_path, urls):
    refs=[]
    for i,url in enumerate(urls):
        nav=tmp_path/f'nav-{i}.json'
        payload={'page_url':url,'plan_sha256':'original'}
        nav.write_text(json.dumps({'schema_version':1,'payload':payload,'payload_sha256':digest(payload)}),encoding='utf8')
        refs.append({'url':url,'path':str(nav),'sha256':hashlib.sha256(nav.read_bytes()).hexdigest()})
    evidence=tmp_path/'TEST_ONLY_evidence.json'
    evidence.write_text(json.dumps({'kind':'TEST_ONLY_TRACKING_PATH_ALIAS_REVIEW','run_id':'TEST_ONLY',
        'original_plan_sha256':'original','urls':urls,'navigation_receipts':refs}),encoding='utf8')
    return {'path':str(evidence),'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}


def test_three_workers_cannot_claim_same_url_or_bypass_global_pacing(tmp_path):
    q=ledger(tmp_path); q.seed(pages('u1','u2','u3'))
    with ThreadPoolExecutor(max_workers=3) as pool:
        result=list(pool.map(lambda w:q.claim(w,now=10),[1,2,3]))
    assert result.count('u1')==1 and result.count(None)==2
    assert q.claim(2,now=12.999) is None
    assert q.claim(2,now=13)=='u2'
    assert q.claim(3,now=16)=='u3'
    assert len({row[0] for row in q.snapshot()['claims']})==3


def test_saved_receipt_replayed_and_committed_pages_never_recollected(tmp_path):
    q=ledger(tmp_path);q.seed([{**pages('old')[0],'state':'COMMITTED'},
                              {**pages('saved')[0],'state':'SAVED','receipt':'proof'},*pages('new')])
    assert q.claim(1,now=10) is None
    with pytest.raises(ValueError):q.replay_committed('saved','wrong',verify=lambda *_:True)
    with pytest.raises(ValueError,match='AUDIT'):q.replay_committed('saved','proof',verify=lambda *_:False)
    q.replay_committed('saved','proof',verify=lambda *_:True)
    assert q.claim(1,now=10)=='new'
    assert [r[0] for r in q.snapshot()['claims']]==['new']


def test_crash_does_not_retry_ambiguous_reservation_or_reset_budget(tmp_path):
    q=ledger(tmp_path,budget=2);q.seed(pages('a','b','c'))
    assert q.claim(1,now=10)=='a';q.hold_inflight_after_restart()
    q=ledger(tmp_path,budget=2)
    assert q.claim(2,now=13) is None
    assert q.snapshot()['config'][0]=='AMBIGUOUS_RESTART'
    assert q.snapshot()['pages'][0][3]=='HELD'
    with pytest.raises(ValueError):
        ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='original',deadline=2000,page_budget=2)


def test_access_stop_stops_future_claims_preserves_other_inflight_receipts(tmp_path):
    q=ledger(tmp_path);q.seed(pages('a','b','c'))
    assert q.claim(1,now=10)=='a';assert q.claim(2,now=13)=='b'
    q.complete(1,'a',state='ACCESS_STOP',receipt='restricted_evidence')
    assert q.claim(3,now=16) is None
    q.complete(2,'b',state='SAVED',receipt='inflight_normal_evidence')
    result=q.snapshot();assert result['config'][0]=='ACCESS_STOP'
    assert {r[0]:r[3] for r in result['pages']}=={'a':'HELD','b':'SAVED','c':'PENDING'}


def test_pause_deadline_and_scope_conflict_fail_closed(tmp_path):
    q=ledger(tmp_path);q.seed(pages('a','b'))
    q.stop('USER_PAUSED');assert q.claim(1,now=10) is None
    q2=ParallelPageLedger(tmp_path/'other.sqlite',run_id='TEST_ONLY',original_plan_sha256='original',deadline=1000,page_budget=10)
    q2.seed(pages('a'));assert q2.claim(1,now=1000) is None
    with pytest.raises(ValueError):q2.seed([{'url':'a','category':'different','family':'BEST_SELLERS'}])


def test_prior_requests_and_last_request_spacing_are_preserved(tmp_path):
    q=ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='original',
                         deadline=1000,page_budget=4,prior_consumed=3,prior_last_start=20)
    q.seed(pages('a','b'));assert q.claim(1,now=22) is None
    assert q.claim(1,now=23)=='a';assert q.claim(2,now=26) is None
    with pytest.raises(ValueError):
        ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='original',
                           deadline=1000,page_budget=4,prior_consumed=2,prior_last_start=20)


def test_family_budget_does_not_transfer_and_unknown_family_fails_closed(tmp_path):
    q=ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='original',deadline=1000,
                         page_budget=5,prior_consumed=1,family_limits={'BEST_SELLERS':1,'NEW_RELEASES':2},
                         prior_family_consumed={'BEST_SELLERS':1,'NEW_RELEASES':0})
    q.seed([*pages('best'),{'url':'new','category':'test','family':'NEW_RELEASES'}])
    assert q.claim(1,now=10)=='new'
    q.complete(1,'new',state='HELD',receipt='TEST_ONLY')
    assert q.claim(2,now=13) is None
    assert {r[0]:r[3] for r in q.snapshot()['pages']}=={'best':'BUDGET_SKIPPED','new':'HELD'}
    q.seed([{'url':'unknown','category':'test','family':'UNREVIEWED'}])
    with pytest.raises(ValueError,match='UNAPPROVED_SOURCE_FAMILY'):q.claim(2,now=16)


def test_category_cooldown_and_inflight_barrier_survive_restart(tmp_path):
    kwargs=dict(run_id='TEST_ONLY',original_plan_sha256='original',deadline=10000,page_budget=10,
                prior_category='first',prior_cooldown_until=0)
    q=ParallelPageLedger(tmp_path/'queue.sqlite',**kwargs)
    q.seed([{'url':'a','category':'first','family':'BEST_SELLERS'},
            {'url':'b','category':'second','family':'BEST_SELLERS'}])
    assert q.claim(1,now=10)=='a'
    assert q.claim(2,now=13) is None  # Cannot cross while request is in flight.
    q.complete(1,'a',state='SAVED',receipt='proof');q.replay_committed('a','proof',verify=lambda *_:True)
    assert q.claim(2,now=16) is None  # Starts the persistent cooldown.
    q=ParallelPageLedger(tmp_path/'queue.sqlite',**kwargs)
    assert q.claim(2,now=1815) is None
    assert q.claim(2,now=1816)=='b'


def test_query_order_alias_never_recollects_historical_page(tmp_path):
    q=ledger(tmp_path)
    url='https://www.amazon.es/s?node=9&page=2'
    alias='https://www.amazon.es/s?page=2&node=9'
    q.seed([{**pages(url)[0],'state':'COMMITTED'},*pages(alias,'https://www.amazon.es/s?node=10&page=2')])
    assert q.claim(1,now=10)=='https://www.amazon.es/s?node=10&page=2'
    assert len(q.snapshot()['pages'])==2


def test_history_collision_fails_instead_of_silently_resetting(tmp_path):
    q=ledger(tmp_path);q.seed(pages('a'))
    with pytest.raises(ValueError,match='HISTORICAL_STATE_COLLISION'):q.seed([{**pages('a')[0],'state':'COMMITTED'}])


def test_navigation_tracking_aliases_are_held_for_review(tmp_path):
    q=ledger(tmp_path)
    first='https://www.amazon.es/gp/bestsellers/kitchen/2165211031/ref=zg_bs_nav_kitchen_1'
    second='https://www.amazon.es/gp/bestsellers/kitchen/2165211031/ref=zg_bs_nav_kitchen_2_3359926031'
    q.seed(pages(first,second,'https://www.amazon.es/gp/bestsellers/kitchen/other'))
    states={r[0]:r[3] for r in q.snapshot()['pages']}
    assert states[first]=='HELD' and states[second]=='HELD'
    assert q.claim(1,now=10)=='https://www.amazon.es/gp/bestsellers/kitchen/other'


def test_alias_of_completed_page_cannot_reopen_old_page(tmp_path):
    q=ledger(tmp_path)
    first='https://www.amazon.es/gp/bestsellers/kitchen/2165211031/ref=zg_bs_nav_kitchen_1'
    alias='https://www.amazon.es/gp/bestsellers/kitchen/2165211031/ref=zg_bs_nav_kitchen_2_3359926031'
    q.seed([{**pages(first)[0],'state':'COMMITTED'},*pages(alias)])
    states={r[0]:r[3] for r in q.snapshot()['pages']}
    assert states[first]=='COMMITTED' and states[alias]=='HELD'
    assert q.claim(1,now=10) is None


def test_evidenced_tracking_alias_resolution_preserves_both_urls(tmp_path):
    q=ledger(tmp_path)
    one='https://www.amazon.es/gp/bestsellers/kitchen/3360028031/ref=zg_bs_nav_kitchen_2_2165211031'
    two='https://www.amazon.es/gp/bestsellers/kitchen/3360028031/ref=zg_bs_nav_kitchen_2_3359926031'
    q.seed(pages(one,two))
    ref=alias_proof(tmp_path,[one,two])
    with pytest.raises(ValueError):q.resolve_tracking_path_aliases([one,two],{'path':ref['path'],'sha256':'bad'})
    assert q.resolve_tracking_path_aliases([one,two],ref)==one
    state={r[0]:r[3] for r in q.snapshot()['pages']}
    assert state=={one:'PENDING',two:'ALIASED'}
    assert q.snapshot()['aliases'][0][:2]==(two,one)
    assert q.claim(1,now=10)==one


def test_tracking_alias_review_rejects_changed_parameters_or_context(tmp_path):
    q=ledger(tmp_path)
    a='https://www.amazon.es/gp/bestsellers/kitchen/1/ref=a'
    b='https://www.amazon.es/gp/bestsellers/kitchen/1/ref=b?node=99'
    q.seed(pages(a,b)); ref=alias_proof(tmp_path,[a,b])
    with pytest.raises(ValueError,match='SCOPE'):q.resolve_tracking_path_aliases([a,b],ref)


def test_alias_resolution_rejects_changed_navigation_evidence(tmp_path):
    q=ledger(tmp_path)
    a='https://www.amazon.es/gp/bestsellers/kitchen/1/ref=a'
    b='https://www.amazon.es/gp/bestsellers/kitchen/1/ref=b'
    q.seed(pages(a,b));ref=alias_proof(tmp_path,[a,b])
    (tmp_path/'nav-0.json').write_text('{}',encoding='utf8')
    with pytest.raises(ValueError,match='NAVIGATION_PROOF_CHANGED'):
        q.resolve_tracking_path_aliases([a,b],ref)
    assert {r[3] for r in q.snapshot()['pages']}=={'HELD'}


def test_resume_acknowledges_only_exact_user_pause_without_resetting_pacing(tmp_path):
    q=ledger(tmp_path);q.seed(pages('new'));q.persist_actual_dispatch_deadline(42)
    q.stop('USER_PAUSED')
    pause={'run_id':'TEST_ONLY','plan_sha256':'original','token':'test'}
    release={'run_id':'TEST_ONLY','plan_sha256':'original','pause_sha256':digest(pause)}
    (tmp_path/'snapshot_pause.json').write_text(json.dumps(pause),encoding='utf8')
    (tmp_path/'snapshot_pause_release.json').write_text(json.dumps(release),encoding='utf8')
    q.acknowledge_user_resume(tmp_path)
    assert q.snapshot()['config']==(None,42.0)
    assert q.claim(1,now=41) is None
    q.stop('ACCESS_STOP')
    with pytest.raises(ValueError,match='ONLY_USER_PAUSE'):q.acknowledge_user_resume(tmp_path)


def test_source_minimum_skip_does_not_reserve_or_spend_budget(tmp_path):
    q=ledger(tmp_path);q.seed(pages('skip','keep'))
    assert q.claim(1,now=10,eligible=lambda u,f:u!='skip')=='keep'
    state=q.snapshot()
    assert state['pages'][0][3]=='SOURCE_SKIPPED'
    assert len(state['claims'])==1 and state['claims'][0][0]=='keep'
