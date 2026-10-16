import gzip
import json
from pathlib import Path
import pytest
from test_detail_delivery_setup import fixture
from amazon_es_bestseller.multisource.storage import immutable_json, atomic_json, sealed, read_sealed, digest
from amazon_es_bestseller.collection.quick_selection import file_sha


def prepared(tmp_path):
    root,plan,auth,a=fixture(tmp_path)
    f=Path(__file__).parent/'fixtures/detail_bridge5500'
    def ref(p):return dict(path=str(p),sha256=file_sha(p))
    hp=root/'failed_UI.html';hp.write_bytes(gzip.decompress((f/'actual_failed_JSON_UI.html.gz').read_bytes()))
    paths={}
    for n,v in [('initial_attempt',{'max_attempts':1}),('continuation_2_attempt',{'total_setup_attempt':2}),
                ('initial_failure',{'error_type':'DeliveryLocationError'}),
                ('continuation_2_failure',{'error_type':'DeliveryLocationError','pending_receipts':[], 'route_errors':[], 'evidence':{'html_sha256':file_sha(hp)}})]:
        p=root/(n+'.json');immutable_json(p,sealed(v));paths[n+'_reference']=ref(p)
    hold=root/'uncertain_setup_PAGE_budget_reservation.json'
    immutable_json(hold,sealed(dict(scope='CONSERVATIVE_RESERVATION_FOR_TWO_UNRECEIPTED_SETUP_DOCUMENTS',run_id=plan['run_id'],plan_sha256=digest(plan),held_PAGE_requests=2,not_network_receipts=True,**paths)))
    auth['request_budget_reservation_reference']=ref(hold)
    modal=read_sealed(f/'actual_setup_readonly_modal_receipt.json');modal.update(run_id=plan['run_id'],task_id=a['task_id'],plan_sha256=digest(plan))
    for i in (1,3):immutable_json(root/f'receipts/delivery_setup_0{i}.json',sealed(modal))
    immutable_json(root/'receipts/delivery_setup_02.json',sealed(dict(request_role='DELIVERY_SETUP',method='GET',kind='PAGE',state='NORMAL',status_code=200)))
    delegation=root/'delegation.json';immutable_json(delegation,dict(approved=True,approved_by='user',human_quote='直到任务提取都不要问我问题，我给你权限你觉得'))
    denied=root/'denied.json';denied.write_bytes((f/'actual_blocked_json_shipping_request.json').read_bytes())
    review=dict(approved=True,approved_by='agent_under_explicit_user_delegation',scope='ONE_NORMAL_UI_REPAIR_AFTER_LOCAL_JSON_PRE_SEND_REJECTION',
        run_id=plan['run_id'],plan_sha256=digest(plan),task_id=a['task_id'],initial_product_URL=a['initial_product_URL'],
        maximum_total_UI_attempts=3,maximum_repair_attempts=1,max_total_setup_requests=8,total_request_cap_unchanged=auth['max_requests'],
        request_deadline_unchanged=auth['request_deadline'],final_deadline_unchanged=auth['deadline'],only_browser_emitted_requests=True,
        delegation_reference=ref(delegation),attempt_2_reference=paths['continuation_2_attempt_reference'],failure_2_reference=paths['continuation_2_failure_reference'],
        failed_UI_reference=ref(hp),blocked_request_reference=ref(denied),last_modal_receipt_reference=ref(root/'receipts/delivery_setup_03.json'),budget_reservation_reference=ref(hold))
    rp=root/'repair.json';immutable_json(rp,review);auth['delivery_setup_repair_reference']=ref(rp)
    return root,plan,auth,a,review


@pytest.mark.parametrize('change',[None,'unapproved','wrong_actor','fourth_attempt','changed_UI','already_consumed','posted','no_hold'])
def test_delegated_repair_only_proven_unsent_UI_error_once(tmp_path,change):
    from amazon_es_bestseller.collection.delivery_setup5500 import validate_local_payload_repair
    root,plan,auth,a,r=prepared(tmp_path)
    if change=='unapproved':r['approved']=False
    if change=='wrong_actor':r['approved_by']='user'
    if change=='fourth_attempt':r['maximum_total_UI_attempts']=4
    if change=='changed_UI':Path(r['failed_UI_reference']['path']).write_text('changed',encoding='utf8')
    if change=='already_consumed':immutable_json(root/'delivery_setup.repair_3.attempt.json',sealed({'total_setup_attempt':3}))
    if change=='posted':immutable_json(root/'receipts/post.json',sealed(dict(request_role='DELIVERY_SETUP',method='POST',state='NORMAL')))
    if change=='no_hold':auth.pop('request_budget_reservation_reference')
    rp=Path(auth['delivery_setup_repair_reference']['path']);atomic_json(rp,r);auth['delivery_setup_repair_reference']['sha256']=file_sha(rp)
    if change is None:assert validate_local_payload_repair(root,plan,auth,a)==r
    else:
        with pytest.raises(ValueError):validate_local_payload_repair(root,plan,auth,a)


def test_uncertain_reservation_counts_without_fabricated_receipt(tmp_path):
    from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
    from amazon_es_bestseller.collection.delivery_setup5500 import SetupLedger
    root,plan,auth,a,r=prepared(tmp_path)
    p=DetailBridge5500(root,plan).progress()
    assert p['paid_requests']==4 and p['conservative_budget_reservation']==2 and p['effective_request_budget_used']==6
    from amazon_es_bestseller.collection.detail5500_hourly import build_report
    report=build_report(root,{})
    assert report['paid_requests']==4 and report['remaining_request_budget']==6044 and report['setup_budget_used_upper_bound']==5
    assert len(list((root/'receipts').glob('*.json')))==4
    # Three further confirmed setup requests would fill its eight-slot upper bound.
    for i in range(3):immutable_json(root/f'receipts/more_{i}.json',sealed(dict(request_role='DELIVERY_SETUP',kind='OTHER',state='NORMAL')))
    ledger=SetupLedger(root,plan,auth,a,lambda:None)
    with pytest.raises(ValueError,match='SETUP_BUDGET_EXHAUSTED'):
        ledger.reserve(a['initial_product_URL'],'GET','PAGE')


@pytest.mark.parametrize('cap',['max_requests','max_page_requests'])
def test_global_and_PAGE_caps_also_include_hold(tmp_path,cap):
    from amazon_es_bestseller.collection.delivery_setup5500 import SetupLedger
    root,plan,auth,a,r=prepared(tmp_path)
    # Override amendment validation only to isolate the budget boundary.
    auth[cap]=2
    import unittest.mock
    with unittest.mock.patch('amazon_es_bestseller.collection.delivery_setup5500.validate_amendment',return_value=a):
        with pytest.raises(ValueError,match='TOTAL_BUDGET_EXHAUSTED'):
            SetupLedger(root,plan,auth,a,lambda:None).reserve(a['initial_product_URL'],'GET','PAGE')


@pytest.mark.parametrize('correct',[True,False])
def test_limited_UI_verifies_actual_header_without_confirmation_reload(monkeypatch,correct):
    from test_delivery_location import _FakePage
    from amazon_es_bestseller.access.browser import BrowserSession
    from amazon_es_bestseller.access.location import DeliveryLocationError
    import itertools
    page=_FakePage()
    page.content=lambda:'<a id="nav-global-location-popover-link">'+(('Enviar a Madrid '+('28001' if correct else '28002')) if page.applied else 'Enviar a Taiwán')+'</a>'
    s=BrowserSession();s.page=page
    monkeypatch.setattr(s,'goto',lambda *a,**kw:pytest.fail('No manufactured document request'))
    ticks=itertools.count(0,10);monkeypatch.setattr('amazon_es_bestseller.access.browser.time.monotonic',lambda:next(ticks))
    if correct:assert s.ensure_spain_delivery('28001',navigate=False,current_status_code=200,avoid_confirmation_navigation=True).postal_code=='28001'
    else:
        with pytest.raises(DeliveryLocationError,match='HEADER_NOT_VERIFIED'):
            s.ensure_spain_delivery('28001',navigate=False,current_status_code=200,avoid_confirmation_navigation=True)
    assert page.applied and not page.done


@pytest.mark.parametrize('route_event',[True,False])
@pytest.mark.parametrize('status',[200,403,429])
def test_initial_setup_document_counted_exactly_once_even_if_route_not_emitted(tmp_path,route_event,status):
    from types import SimpleNamespace as NS
    from amazon_es_bestseller.collection.delivery_setup5500 import perform_setup
    root,plan,auth,a=fixture(tmp_path)
    events={};handlers={};body='<a id="nav-global-location-popover-link">Enviar a Madrid 28001</a>'
    page=NS(url=a['initial_product_URL'],main_frame=object(),content=lambda:body,wait_for_timeout=lambda ms:None,
        route=lambda pattern,handler:handlers.update(route=handler),unroute=lambda *a:None,
        on=lambda name,handler:events.update({name:handler}),remove_listener=lambda *a:None)
    def goto(url):
        if route_event:
            class Request(NS):
                __hash__=object.__hash__
            q=Request(url=url,method='GET',frame=page.main_frame,resource_type='document',is_navigation_request=lambda:True)
            handlers['route'](NS(request=q,continue_=lambda:None,abort=lambda:pytest.fail('initial GET must pass')))
            events['requestfinished'](q)
        if status!=200:s.document_responses.append(dict(status_code=status,url=url))
        return 200 if status==200 else None
    s=NS(page=page,goto=goto,document_responses=[],access_blocked=False,ensure_spain_delivery=lambda *a,**kw:None)
    bridge=NS(root=root,plan=plan,validate_authorization=lambda auth:None,gates_normal=lambda:None)
    if status==200:perform_setup(s,bridge,auth)
    else:
        with pytest.raises(ValueError,match='INITIAL_SETUP_DOCUMENT_ACCESS_STOP'):
            perform_setup(s,bridge,auth)
    setup=[read_sealed(p) for p in (root/'receipts').glob('*.json') if read_sealed(p).get('request_role')=='DELIVERY_SETUP']
    assert len(setup)==1 and setup[0]['kind']=='PAGE' and setup[0]['status_code']==status
    if status!=200:assert read_sealed(root/'access_gate.json')['state'] in {'BLOCKED','RATE_LIMITED'}
