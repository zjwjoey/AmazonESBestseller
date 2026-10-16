"""Use the already approved first DETAIL2 to verify a receipted UI update.

No setting request, extra retry, API call, or premature location acceptance.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit
from ..access.location import inspect_delivery_location
from ..access.detector import detect_access_status
from ..multisource.storage import read_sealed, immutable_json, sealed, digest
from .quick_selection import file_sha
from .delivery_setup5500 import validate_amendment, verify_setup
from .detail_budget5500 import uncertain_setup_page_holds


def reviewed_update(root, plan, auth):
    root=Path(root);a=validate_amendment(root,plan,auth)
    ref=auth.get('post_UI_first_detail_recheck_reference')
    if not ref or file_sha(ref['path'])!=ref['sha256']:
        raise ValueError('POST_UI_DETAIL2_REVIEW_REQUIRED')
    v=json.loads(Path(ref['path']).read_text(encoding='utf8'))
    if (v.get('approved') is not True or v.get('approved_by')!='agent_under_explicit_user_delegation'
            or v.get('scope')!='PREAPPROVED_FIRST_DETAIL2_WITH_ACTUAL_LOCATION_AND_DETAIL_ACCEPTANCE'
            or v.get('run_id')!=plan['run_id'] or v.get('plan_sha256')!=digest(plan)
            or v.get('task_id')!=a['task_id'] or v.get('initial_product_URL')!=a['initial_product_URL']
            or v.get('special_recheck_max_requests')!=1 or v.get('first_DETAIL_max_attempts')!=2
            or v.get('no_new_UI_requests') is not True or v.get('not_source_location_acceptance') is not True):
        raise ValueError('POST_UI_DETAIL2_SCOPE_INVALID')
    for name in ('post_receipt_reference','post_body_reference','prior_normal_UI_authorization_reference','budget_reservation_reference'):
        r=v[name]
        if not Path(r['path']).resolve().is_relative_to(root.resolve()) or file_sha(r['path'])!=r['sha256']:
            raise ValueError('POST_UI_DETAIL2_EVIDENCE_CHANGED')
    post=read_sealed(v['post_receipt_reference']['path'])
    prior=json.loads(Path(v['prior_normal_UI_authorization_reference']['path']).read_text(encoding='utf8'))
    data=json.loads(Path(v['post_body_reference']['path']).read_text(encoding='utf8'))
    url=urlsplit(post.get('request_url',''))
    if (post.get('state')!='NORMAL' or post.get('status_code')!=200 or post.get('method')!='POST'
            or post.get('kind')!='OTHER' or post.get('request_role')!='DELIVERY_SETUP'
            or post.get('run_id')!=plan['run_id'] or post.get('task_id')!=a['task_id']
            or post.get('authorization_sha256')!=digest(prior) or prior.get('run_id')!=plan['run_id'] or prior.get('plan_sha256')!=digest(plan)
            or url.scheme!='https' or url.netloc!='www.amazon.es' or url.path!='/portal-migration/hz/glow/address-change'
            or post.get('body_path')!=v['post_body_reference']['path'] or post.get('body_sha256')!=v['post_body_reference']['sha256']
            or not isinstance(data,dict) or any(data.get(k)!=1 for k in ('isValidAddress','isAddressUpdated','successful'))
            or not isinstance(data.get('address'),dict) or data['address'].get('countryCode')!='ES' or data['address'].get('zipCode')!='28001'):
        raise ValueError('POST_UI_DETAIL2_REQUIRES_ACTUAL_NORMAL_APPLIED_UI')
    if auth.get('request_budget_reservation_reference')!=v['budget_reservation_reference']:
        raise ValueError('POST_UI_DETAIL2_BUDGET_BINDING_INVALID')
    return a


def validate_post_UI_first_detail_recheck(root, plan, auth):
    root=Path(root);a=reviewed_update(root,plan,auth)
    rows=[read_sealed(p) for p in (root/'receipts').glob('*.json')]
    setup=[r for r in rows if r.get('request_role')=='DELIVERY_SETUP']
    held=uncertain_setup_page_holds(root,plan,auth)
    if (len(rows)!=7 or len(setup)+held!=8 or any(r.get('state')!='NORMAL' for r in rows)
            or (root/'receipts'/(a['task_id']+'_2.json')).exists() or (root/'delivery_setup.complete.json').exists()):
        raise ValueError('POST_UI_DETAIL2_ALREADY_CONSUMED_OR_HISTORY_CHANGED')
    return a


def finalize_setup_from_first_detail(bridge, auth):
    root,plan=bridge.root,bridge.plan
    if (root/'delivery_setup.complete.json').exists():return verify_setup(root,plan,auth)
    a=reviewed_update(root,plan,auth);task=next(t for t in plan['tasks'] if t['task_id']==a['task_id'])
    result=read_sealed(root/'tasks'/task['task_id']/'checkpoint.json')
    bridge.verify_saved_success(task,result)
    html=Path(result['html_path']).read_text(encoding='utf8');location=inspect_delivery_location(html)
    receipt=read_sealed(result['receipt_path'])
    if (result.get('status')!='SUCCESS' or receipt.get('state')!='NORMAL' or receipt.get('status_code')!=200
            or receipt.get('request_role')!='DETAIL' or receipt.get('attempt')!=2
            or receipt.get('run_id')!=plan['run_id'] or receipt.get('task_id')!=task['task_id']
            or receipt.get('request_url')!=task['request_url'] or receipt.get('html_sha256')!=result['html_sha256']
            or location.is_spain is not True or location.postal_code!='28001' or detect_access_status(200,html).value!='NORMAL'):
        raise ValueError('POST_UI_DETAIL2_ACTUAL_PAGE_NOT_VERIFIED')
    mp=Path(result['response_metadata_path'])
    if file_sha(mp)!=result['response_metadata_sha256']:raise ValueError('POST_UI_DETAIL2_METADATA_CHANGED')
    from .detail_bridge5500 import parse_candidate_detail
    replay=parse_candidate_detail(task,html,json.loads(mp.read_text(encoding='utf8')))
    if replay['status']!='SUCCESS' or replay['record']!=result['record']:
        raise ValueError('POST_UI_DETAIL2_INDEPENDENT_REPLAY_FAILED')
    setup={str(p):file_sha(p) for p in (root/'receipts').glob('delivery_setup_*.json')}
    immutable_json(root/'delivery_setup.complete.json',sealed(dict(passed=True,completed_at=receipt['finished_at'],plan_sha256=digest(plan),
        amendment_sha256=auth['delivery_setup_reference']['sha256'],postal_code=location.postal_code,delivery_text=location.text,
        html_path=result['html_path'],html_sha256=result['html_sha256'],receipts=setup,actual_UI_attempts=3,
        request_budget_reservation_reference=auth['request_budget_reservation_reference'],
        post_UI_first_detail_recheck_reference=auth['post_UI_first_detail_recheck_reference'],
        detail_recheck_receipt_reference=dict(path=result['receipt_path'],sha256=file_sha(result['receipt_path'])),
        real_product_header_not_UI_acknowledgement_is_acceptance=True)))
    return verify_setup(root,plan,auth)
