"""Offline tests: simulated transport receipts are TEST_ONLY, never live proof."""
from copy import deepcopy
from datetime import timedelta
import hashlib
from pathlib import Path
from types import SimpleNamespace
from bs4 import BeautifulSoup
import pytest

from test_highspeed_v4_execution import setup_scope,FakeTransport,NOW
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
from amazon_es_bestseller.highspeed.current_page_protocol import CONTRACT,verify_current_protocol
from amazon_es_bestseller.highspeed.page_navigation import POLICY,extend_source,validate_admitted_source
from amazon_es_bestseller.highspeed.page_snapshot import CurrentPageSnapshotRunner
from amazon_es_bestseller.highspeed.three_navigation import discover_navigation
from amazon_es_bestseller.multisource.storage import immutable_json,digest


def ref(path):
    return {'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def make_probe(tmp_path):
    plan,auth=setup_scope(tmp_path)
    plan['sources']=[s for s in plan['sources'] if s['source_type'] in {'BEST_SELLERS','NEW_RELEASES'}
                     or s['source_subtype']=='CUSTOMERS_MOST_LOVED_CURATED_ZONE']
    plan.update(max_requests_per_run=3,max_pages_per_run=3,max_requests_per_day=3)
    auth['sources']={s['source_id']:auth['sources'][s['source_id']] for s in plan['sources']}
    for s in auth['sources'].values():s.update(allow_native_acp=False,allow_native_dynamic=False,allow_held_native_carousel=False)
    for k in ('max_requests_per_run','max_pages_per_run','max_requests_per_day'):auth[k]=plan[k]
    auth['plan_sha256']=digest(plan)
    class TestTransport(FakeTransport):
        def fetch_page(self,url):
            result=super().fetch_page(url);soup=BeautifulSoup(result['text'],'lxml')
            header=soup.select_one('#nav-global-location-popover-link')
            if header is not None:header.clear()
            else:
                header=soup.new_tag('span',id='nav-global-location-popover-link');soup.body.insert(0,header)
            header.string='Enviar a Madrid 28001';result['text']=str(soup)
            return result
    store=RunStore(auth['store_root'])
    V4T1Runner(store,clock=lambda:NOW,sleep=lambda _:None).run(plan,authorization=auth,transport=TestTransport(plan,auth))
    pp=tmp_path/'test_plan.json';ap=tmp_path/'test_auth.json'
    immutable_json(pp,plan);immutable_json(ap,auth)
    proof=tmp_path/'test_protocol.json'
    immutable_json(proof,{'contract':CONTRACT,'plan_reference':ref(pp),'authorization_reference':ref(ap),
                         'run_directory':str(store.root/'runs'/plan['run_id'])})
    return store,plan,proof


def test_current_page_proof_recomputes_three_families_without_legacy_pass(tmp_path):
    store,plan,path=make_probe(tmp_path)
    try:
        result=verify_current_protocol(ref(path))
        assert result['actual_PAGE_requests']==3
        assert result['legacy_T1_T2_acceptance_claimed'] is False
        assert result['native_protocol_approved'] is False
        bad=tmp_path/'fake_pass.json'
        immutable_json(bad,{'contract':CONTRACT,'passed':True})
        with pytest.raises((ValueError,KeyError)):verify_current_protocol(ref(bad))
    finally:store.close()


def test_owned_child_needs_actual_same_run_normal_parent_receipt(tmp_path):
    store,plan,path=make_probe(tmp_path)
    try:
        page=next(p for p in store.pages(plan['run_id']) if p['source_type']=='BEST_SELLERS')
        root=deepcopy(page['source'])
        root.update(collection_category_key='TEST_ONLY_REVIEWED_ROOT',collection_category_evidence={'TEST_ONLY':True},
                    owned_page_navigation=deepcopy(POLICY))
        navigation=next(r for r in discover_navigation(page,root) if r['relationship']=='CHILD_CATEGORY' and r['label_raw']=='Hogar y cocina')
        child=extend_source(root,navigation,page['capture_receipt_reference'],root)
        assert validate_admitted_source(child,root,plan['run_id'],digest(plan)) is child
        with pytest.raises(ValueError,match='SAME_RUN'):
            validate_admitted_source(child,root,'FOREIGN_RUN')
        changed=deepcopy(child);changed['actual_source_url']='https://www.amazon.es/gp/bestsellers/guessed/'
        with pytest.raises(ValueError):validate_admitted_source(changed,root,plan['run_id'])
        changed=deepcopy(child);changed['snapshot_navigation_receipts']=[]
        with pytest.raises(ValueError,match='PARENT_RECEIPT'):
            validate_admitted_source(changed,root,plan['run_id'])
    finally:store.close()


def test_restart_preserves_category_cooldown_deadline_instead_of_resetting():
    clock=[NOW+timedelta(seconds=600)];sleeps=[]
    state={'started_at':NOW.isoformat(),'category_schedule':{
        'current':'Hogar','next':'Jardín','deadline':(NOW+timedelta(seconds=1800)).isoformat()}}
    class Store:
        def get_run(self,_):return state
        def update_run(self,_,**kw):state.update(kw)
        def append_event(self,*args):pass
        def budget_counters(self,_):return {'requests':0,'page_requests':0,'raw_count':0,'requests_by_day':{}}
    def sleep(seconds):sleeps.append(seconds);clock[0]+=timedelta(seconds=seconds)
    runner=CurrentPageSnapshotRunner.__new__(CurrentPageSnapshotRunner)
    runner.store=Store();runner.engine=SimpleNamespace(clock=lambda:clock[0],sleep=sleep,_gate=lambda _: {'state':'NORMAL'})
    runner._active_authorization={'expires_at':(NOW+timedelta(hours=24)).isoformat()}
    plan={'run_id':'TEST_ONLY','category_cooldown_seconds':1800,'max_duration_hours':24,
          'max_requests_per_run':100,'max_requests_per_day':100,'max_pages_per_run':100,
          'max_raw_observations_per_run':10000,'history_observation_limit':10000}
    runner.before_source(plan,{'collection_category_key':'Jardín'})
    assert sum(sleeps)==1200 and max(sleeps)<=30
    assert state['category_schedule']=={'current':'Jardín','next':None,'deadline':None}


@pytest.mark.parametrize('with_amendment',[False,True])
def test_page_task_replays_uncommitted_receipt_without_repeat_or_legacy_pass(tmp_path,with_amendment):
    from amazon_es_bestseller.highspeed.three_contract import source_from_navigation
    from amazon_es_bestseller.highspeed.v4_sources import source_for_url,SEEDS
    from amazon_es_bestseller.highspeed.page_snapshot import CONTRACT as PAGE
    from amazon_es_bestseller.highspeed.global_target import MAIN_PRIORITY_POLICY
    from amazon_es_bestseller.highspeed.task_clock import start_task_clock
    from amazon_es_bestseller.highspeed.v4_authorization import prerequisite_review_session
    probe,old,proof=make_probe(tmp_path/'probe')
    try:
        plan,auth=setup_scope(tmp_path/'task');sources=[]
        fixture=Path('tests/fixtures/highspeed/real_v4_t2_kitchen_grid.html').resolve()
        soup=BeautifulSoup(fixture.read_bytes(),'lxml');node=soup.select_one('h1,h2')
        context={**ref(fixture),'selector':'h1,h2','node_sha256':hashlib.sha256(str(node).encode()).hexdigest(),
                 'statement_raw':node.get_text(' ',strip=True),'category_label_raw':'Hogar y cocina'}
        for family in ('BEST_SELLERS','NEW_RELEASES'):
            page=next(p for p in probe.pages(old['run_id']) if p['source_type']==family)
            nav=next(r for r in discover_navigation(page,page['source']) if r['label_raw']=='Hogar y cocina')
            sources.append(source_from_navigation(nav,page['source']))
        fav=source_for_url(SEEDS[6]['url']);fav['page_urls']=[fav['actual_source_url']];sources.append(fav)
        for source in sources:
            source.update(collection_category_key='Hogar',collection_category_evidence=context,owned_page_navigation=deepcopy(POLICY))
        plan.update(sources=sources,collection_contract=PAGE,current_protocol_reference=ref(proof),
            target_policy=deepcopy(MAIN_PRIORITY_POLICY),source_targets=deepcopy(MAIN_PRIORITY_POLICY['source_minima']),
            snapshot_metadata_contract='SOURCE_CATEGORY_IDENTITY_V1',target_unique_links=20000,
            category_cooldown_seconds=1800,max_duration_hours=24,max_requests_per_run=5,max_requests_per_day=5,
            max_pages_per_run=5,source_budgets={'BEST_SELLERS':{'requests':3,'PAGE':3,'native':0},
            'NEW_RELEASES':{'requests':1,'PAGE':1,'native':0},'CUSTOMER_FAVORITES':{'requests':1,'PAGE':1,'native':0}},
            task_clock_path=str((tmp_path/'clock.json').resolve()),qa_reserve_policy={
                'seconds_per_raw':0.01,'safety_factor':2,'fixed_seconds':900,'minimum_seconds':3600})
        for k in ('collection_contract','current_protocol_reference','target_policy','source_targets',
                  'snapshot_metadata_contract','target_unique_links','category_cooldown_seconds','max_duration_hours',
                  'max_requests_per_run','max_requests_per_day','max_pages_per_run','source_budgets'):auth[k]=deepcopy(plan[k])
        auth.update(plan_sha256=digest(plan),expires_at=(NOW+timedelta(hours=24)).isoformat(),
            task_clock_reference=start_task_clock(plan['task_clock_path'],plan,'TEST_ONLY',now=NOW),sources={})
        for source in sources:
            auth['sources'][source['source_id']]={**{k:source[k] for k in ('source_type','source_subtype','page_urls','parser_profile')},
                'evidence':{**ref(fixture),'reviewed':True},'source':deepcopy(source),
                'owned_page_navigation':deepcopy(POLICY),'allow_native_acp':False,'allow_native_dynamic':False,
                'allow_held_native_carousel':False}
        if with_amendment:
            from test_favorite_budget import attach_amendment
            attach_amendment(tmp_path,plan,auth,NOW)
        clock=[NOW]
        class Transport:
            calls=[]
            def fetch_page(self,url):
                self.calls.append(url)
                clock[0]+=timedelta(seconds=5)
                path=Path('tests/fixtures/highspeed_v4/S07_favorites_results_complete.html') if '/s?' in url else fixture
                text=path.read_text('utf8')
                if '/gp/new-releases/' in url:
                    # Explicit TEST_ONLY template adaptation; no live proof.
                    text=text.replace('/gp/bestsellers','/gp/new-releases').replace('zg_bs','zg_bsnr').replace('Los más vendidos','Últimas novedades')
                dom=BeautifulSoup(text,'lxml')
                header=dom.select_one('#nav-global-location-popover-link')
                if header is None:header=dom.new_tag('span',id='nav-global-location-popover-link');dom.body.insert(0,header)
                header.clear();header.string='Enviar a Madrid 28001'
                final=url
                if '/s?' in url:
                    base,query=url.split('?',1);final=base+'?'+'&'.join(reversed(query.split('&')))
                return {'status_code':200,'url':final,'text':str(dom)}
        store=RunStore(auth['store_root']);transport=Transport()
        try:
            commit_page=store.commit_page;interrupted=[False]
            def interrupt_before_page_commit(run_id,key,page,observations):
                if page['source_type']=='CUSTOMER_FAVORITES' and not interrupted[0]:
                    interrupted[0]=True
                    raise OSError('TEST_ONLY_INTERRUPTION_AFTER_RECEIPT_BEFORE_COMMIT')
                return commit_page(run_id,key,page,observations)
            store.commit_page=interrupt_before_page_commit
            with prerequisite_review_session():
                result=CurrentPageSnapshotRunner(store,clock=lambda:clock[0],sleep=lambda _:None).run(plan,authorization=auth,transport=transport)
            assert len(transport.calls)==3 and len(store.pages(plan['run_id']))==2
            from amazon_es_bestseller.multisource.storage import atomic_json,sealed
            from amazon_es_bestseller.highspeed.query_scope_recovery import restore_query_order_scope
            receipt=next(r for r in store.iter_responses(plan['run_id']) if r['source_type']=='CUSTOMER_FAVORITES')
            receipt_path=Path(receipt['evidence_path'])
            local={'state':'UNKNOWN','reason':'UNREVIEWED_REDIRECT','run_id':plan['run_id'],
                'evidence_path':str(receipt_path),'previous_gate':{'state':'NORMAL'}}
            shared={**local,'reason':'HIGH_SPEED_V2_ACCESS_STOP'}
            atomic_json(store.root/'access_gate.json',sealed(local));atomic_json(Path(plan['shared_access_gate_path']),sealed(shared))
            approval={'approved':True,'approved_by':'TEST_ONLY','authorization_text':'TEST_ONLY_EXPLICIT_RESUME',
                'run_id':plan['run_id'],'plan_sha256':digest(plan),'local_gate_sha256':digest(local),
                'shared_gate_sha256':digest(shared),'receipt_reference':ref(receipt_path)}
            bad=deepcopy(approval);bad['local_gate_sha256']='CHANGED_GATE'
            with pytest.raises(ValueError):restore_query_order_scope(store,plan,auth,fav,bad,now=clock[0])
            evidence=restore_query_order_scope(store,plan,auth,fav,approval,now=clock[0])
            assert evidence['new_network_requests']==0 and evidence['counter_reset'] is False
            before=len(store.requests(plan['run_id']));assert before==3
            with prerequisite_review_session():
                result=CurrentPageSnapshotRunner(store,clock=lambda:clock[0],sleep=lambda _:None).run(plan,authorization=auth,transport=transport,resume=True)
            # Persisted siblings may be visited in a different hash-file order;
            # either family or total cap stops the same cumulative budget.
            assert 3<=len(transport.calls)<=5,result['run']['stop_reason']
            calls_after_resume=len(transport.calls)
            assert transport.calls.count(fav['actual_source_url'])==1
            assert result['quality']['central_full_dom_review_passed'],result['quality']['errors'][:3]
            replayed=next(p for p in store.pages(plan['run_id']) if p['source_type']=='CUSTOMER_FAVORITES')
            assert replayed['capture_receipt_reference']==ref(receipt_path)
            assert result['snapshot_metrics']['sources']['CUSTOMER_FAVORITES']['E_qualified_review_groups']>0
            assert not result['run']['quality_gate_passed'] and not result['legacy_stage_acceptance_claimed']
            with prerequisite_review_session():
                again=CurrentPageSnapshotRunner(store,clock=lambda:clock[0],sleep=lambda _:None).run(plan,authorization=auth,transport=transport,resume=True)
            # A budget amendment may allow a previously pending Next on another
            # resume. Completed URLs must never be fetched a second time.
            assert not set(transport.calls[calls_after_resume:]) & set(transport.calls[:calls_after_resume])
            assert len(transport.calls)<=plan['max_requests_per_run']
            assert again['run']['started_at']==result['run']['started_at']
            assert again['quality']['central_full_dom_review_passed'],again['quality']['errors'][:3]
            assert again['run']['snapshot_review']['status']=='PASSED'
            assert again['run']['snapshot_acceptance']=='NOT_ACCEPTED_SNAPSHOT_QUANTITY_SHORTFALL'
            assert again['snapshot_metrics']['acceptance']==again['run']['snapshot_acceptance']
            assert again['run']['production_acceptance']=='NOT_EVALUATED'
            # Repair an old display using the existing final audit only.
            from amazon_es_bestseller.highspeed.snapshot_review import refresh_snapshot_review
            store.update_run(plan['run_id'],snapshot_review=None)
            before_requests=len(store.requests(plan['run_id']))
            correction=refresh_snapshot_review(store,plan['run_id'],tmp_path/'correction.json')
            assert correction['corrected']['snapshot_review']['status']=='PASSED'
            assert correction['new_network_requests']==0
            assert len(store.requests(plan['run_id']))==before_requests
            assert store.get_run(plan['run_id'])['qa']==again['quality']
            assert store.get_run(plan['run_id'])['ended_at']==again['run']['ended_at']
            import json
            progress=json.loads((store.root/'snapshot_progress.json').read_text('utf8'))
            assert progress['snapshot_review']['status']=='PASSED'
            assert progress['status']=='STOPPED'
            assert progress['metrics']['acceptance']==again['run']['snapshot_acceptance']
            assert progress['production_acceptance']=='NOT_EVALUATED'
            assert len(transport.calls)<=plan['max_requests_per_run']
        finally:store.close()
    finally:probe.close()
