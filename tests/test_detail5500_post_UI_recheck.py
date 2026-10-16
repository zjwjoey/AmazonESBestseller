import json
from pathlib import Path
import pytest
from datetime import datetime,timedelta,timezone
from test_detail5500_local_repair import prepared
from amazon_es_bestseller.multisource.storage import immutable_json,atomic_json,sealed,digest,read_sealed
from amazon_es_bestseller.collection.quick_selection import file_sha


def ready(tmp_path):
    root,plan,auth,a,_=prepared(tmp_path)
    def ref(p):return dict(path=str(p),sha256=file_sha(p))
    f=Path(__file__).parent/'fixtures/detail_bridge5500'
    body=root/'actual_UI_ack.json';body.write_bytes((f/'actual_UI_address_applied.safe_projection.json').read_bytes())
    prior=root/'prior_normal_UI_auth.json';immutable_json(prior,dict(run_id=plan['run_id'],plan_sha256=digest(plan)))
    for i in (4,5):immutable_json(root/f'receipts/delivery_setup_0{i}.json',sealed(dict(request_role='DELIVERY_SETUP',method='GET',kind='PAGE' if i==4 else 'OTHER',state='NORMAL',status_code=200)))
    post=root/'receipts/delivery_setup_06.json';immutable_json(post,sealed(dict(run_id=plan['run_id'],task_id=a['task_id'],request_role='DELIVERY_SETUP',kind='OTHER',method='POST',state='NORMAL',status_code=200,request_url='https://www.amazon.es/portal-migration/hz/glow/address-change?actionSource=glow',body_path=str(body),body_sha256=file_sha(body),authorization_sha256=digest(json.loads(prior.read_text(encoding='utf8'))))))
    for p in (root/'receipts').glob('*.json'):
        row=read_sealed(p)
        if not row.get('started_at'):
            row['started_at']=(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat();atomic_json(p,sealed(row))
    review=dict(approved=True,approved_by='agent_under_explicit_user_delegation',scope='PREAPPROVED_FIRST_DETAIL2_WITH_ACTUAL_LOCATION_AND_DETAIL_ACCEPTANCE',run_id=plan['run_id'],plan_sha256=digest(plan),task_id=a['task_id'],initial_product_URL=a['initial_product_URL'],special_recheck_max_requests=1,first_DETAIL_max_attempts=2,no_new_UI_requests=True,not_source_location_acceptance=True,
        post_receipt_reference=ref(post),post_body_reference=ref(body),prior_normal_UI_authorization_reference=ref(prior),budget_reservation_reference=auth['request_budget_reservation_reference'])
    rp=root/'post_UI_review.json';immutable_json(rp,review);auth.pop('delivery_setup_repair_reference');auth['post_UI_first_detail_recheck_reference']=ref(rp)
    return root,plan,auth,a,review


@pytest.mark.parametrize('change',[None,'wrong_postcode','unapproved','consumed','failed_POST','extra_setup'])
def test_only_preapproved_DETAIL2_after_actual_normal_applied_UI(tmp_path,change):
    from amazon_es_bestseller.collection.delivery_post_UI5500 import validate_post_UI_first_detail_recheck
    root,plan,auth,a,r=ready(tmp_path)
    if change=='wrong_postcode':
        p=Path(r['post_body_reference']['path']);d=json.loads(p.read_text(encoding='utf8'));d['address']['zipCode']='90210';atomic_json(p,d);r['post_body_reference']['sha256']=file_sha(p)
    if change=='unapproved':r['approved']=False
    if change=='consumed':immutable_json(root/'receipts/first_2.json',sealed({'state':'NORMAL'}))
    if change=='failed_POST':
        p=Path(r['post_receipt_reference']['path']);d=read_sealed(p);d['state']='RATE_LIMITED';d['status_code']=429;atomic_json(p,sealed(d));r['post_receipt_reference']['sha256']=file_sha(p)
    if change=='extra_setup':immutable_json(root/'receipts/extra.json',sealed(dict(state='NORMAL',request_role='DELIVERY_SETUP')))
    p=Path(auth['post_UI_first_detail_recheck_reference']['path']);atomic_json(p,r);auth['post_UI_first_detail_recheck_reference']['sha256']=file_sha(p)
    if change is None:assert validate_post_UI_first_detail_recheck(root,plan,auth)==a
    else:
        with pytest.raises(ValueError):validate_post_UI_first_detail_recheck(root,plan,auth)


@pytest.mark.parametrize('header,replay_ok',[('Enviar a Madrid 28001',True),('Enviar a Taiwán',True),('Enviar a Madrid 28001',False)])
def test_first_DETAIL2_acceptance_requires_real_page_then_independent_replay(tmp_path,monkeypatch,header,replay_ok):
    from types import SimpleNamespace as NS
    from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
    from amazon_es_bestseller.collection.delivery_post_UI5500 import finalize_setup_from_first_detail
    root,plan,auth,a,_=ready(tmp_path);task=plan['tasks'][0];slot=root/'tasks/first';slot.mkdir(parents=True)
    old=dict(status='IDENTITY_REVIEW',reason='SPAIN_DELIVERY_LOCATION_NOT_VERIFIED',receipt_path=a['original_failed_receipt_reference']['path'],candidate_id=task['candidate_id'],run_id=plan['run_id'])
    immutable_json(slot/'checkpoint.json',sealed(old));immutable_json(slot/'response_metadata.json',{'original':'Taiwan attempt1 metadata'})
    original=file_sha(slot/'response_metadata.json')
    # Test-only UI/transport double. This is not live delivery proof.
    html='<a id="nav-global-location-popover-link">'+header+'</a>'
    calls=[];session=NS(page=NS(url=task['request_url'],content=lambda:html),goto=lambda u:calls.append(u) or 200,wait_for_product_page=lambda:None,wait_for_price_text=lambda:None)
    bridge=DetailBridge5500(root,plan);monkeypatch.setattr(bridge,'validate_authorization',lambda auth:None)
    monkeypatch.setattr('amazon_es_bestseller.collection.detail_bridge5500.parse_candidate_detail',lambda *a:dict(status='SUCCESS',record=None))
    if 'Taiwán' in header:
        with pytest.raises(ValueError,match='SAFETY_REVIEW_REQUIRED'):bridge.run(session,auth,until_completed=1)
        assert not (root/'delivery_setup.complete.json').exists()
        with pytest.raises(ValueError):bridge.run(session,auth,until_completed=1)
    else:
        bridge.run(session,auth,until_completed=1)
        assert not (root/'delivery_setup.complete.json').exists() # UI acknowledgement alone never pre-accepts location.
        if not replay_ok:
            monkeypatch.setattr('amazon_es_bestseller.collection.detail_bridge5500.parse_candidate_detail',lambda *a:dict(status='IDENTITY_REVIEW',record=None))
            with pytest.raises(ValueError,match='INDEPENDENT_REPLAY_FAILED'):finalize_setup_from_first_detail(bridge,auth)
            assert not (root/'delivery_setup.complete.json').exists()
        else:
            finalize_setup_from_first_detail(bridge,auth)
            bridge.run(session,auth,until_completed=1) # Actual success reused; no second request.
            assert read_sealed(root/'delivery_setup.complete.json')['real_product_header_not_UI_acknowledgement_is_acceptance'] is True
    assert len(calls)==1 and len(list((root/'receipts').glob('delivery_setup_*.json')))==6
    assert read_sealed(slot/'checkpoint.attempt_1.preserved.json')==old and file_sha(slot/'response_metadata.json')==original
