"""Explicit offline handling of one byte-identical query-order scope stop.

Never handles access restrictions, transport errors or changed query values.
Original receipts, counters, plan, clock and checkpoint remain unchanged.
"""
import hashlib
from pathlib import Path
from ..access.detector import detect_access_status
from ..access.location import inspect_delivery_location
from ..multisource.storage import read_sealed,digest,immutable_json,sealed,atomic_json,file_lock
from .budgets import aware_time,utc_text
from .v4_authorization import authorize_v4_request
from .v4_navigation import exact_query_token_reordering


def restore_query_order_scope(store,plan,auth,source,approval,*,now=None):
    shared=Path(plan['shared_access_gate_path'])
    with store.run_lock(plan['run_id']),file_lock(shared.parent/'highspeed_live.lock'):
        local=read_sealed(store.root/'access_gate.json');prior=read_sealed(shared)
        if (plan.get('collection_contract')!='CURRENT_THREE_SOURCE_PAGE_SNAPSHOT_V1'
                or digest(plan)!=auth['plan_sha256'] or approval.get('approved') is not True
                or not approval.get('approved_by') or not approval.get('authorization_text')
                or approval.get('run_id')!=plan['run_id'] or approval.get('plan_sha256')!=digest(plan)
                or approval.get('local_gate_sha256')!=digest(local) or approval.get('shared_gate_sha256')!=digest(prior)
                or not aware_time(auth['reviewed_at'])<=aware_time(now)<aware_time(auth['expires_at'])):
            raise ValueError('EXACT_CURRENT_USER_SCOPE_RECOVERY_REQUIRED')
        reference=approval['receipt_reference'];path=Path(reference['path']).resolve()
        if hashlib.sha256(path.read_bytes()).hexdigest()!=reference['sha256']:
            raise ValueError('SCOPE_RECOVERY_RECEIPT_SHA_MISMATCH')
        r=read_sealed(path)
        if path!=(store.root/'runs'/plan['run_id']/'responses'/(r['request_id']+'.json')).resolve():
            raise ValueError('SCOPE_RECOVERY_EXACT_STORE_RECEIPT_REQUIRED')
        initial=r['request_url'];final=r.get('final_url')
        if (initial==final or not exact_query_token_reordering(initial,final)
                or r.get('status_code')!=200 or r.get('access_state')!='NORMAL'
                or r.get('origin')!='LIVE_CAPTURE' or r.get('kind')!='PAGE' or r.get('request_method')!='GET'
                or r.get('run_id')!=plan['run_id'] or r.get('source_id')!=source['source_id']):
            raise ValueError('ONLY_ACTUAL_NORMAL_QUERY_ORDER_SCOPE_STOP_RECOVERABLE')
        if (local.get('state')!='UNKNOWN' or prior.get('state')!='UNKNOWN'
                or local.get('reason')!='UNREVIEWED_REDIRECT' or prior.get('reason')!='HIGH_SPEED_V2_ACCESS_STOP'
                or local.get('run_id')!=plan['run_id'] or prior.get('run_id')!=plan['run_id']
                or local.get('evidence_path')!=str(path) or prior.get('evidence_path')!=str(path)
                or local.get('previous_gate',{}).get('state')!='NORMAL'):
            raise ValueError('NO_ACCESS_RESTRICTION_OR_OTHER_GATE_OVERRIDE')
        q=read_sealed(path.parent.parent/'requests'/(r['request_id']+'.reserved.json'))
        if (q['plan_sha256']!=digest(plan) or q['run_id']!=plan['run_id'] or q['source_id']!=source['source_id']
                or q['url']!=initial or q['kind']!='PAGE' or q['origin']!='LIVE_CAPTURE'):
            raise ValueError('SCOPE_RECOVERY_RESERVATION_BINDING_MISMATCH')
        body=Path(r['html_path']).read_bytes()
        if (hashlib.sha256(body).hexdigest()!=r['sha256']
                or detect_access_status(200,body.decode('utf8')).value!='NORMAL'
                or inspect_delivery_location(body.decode('utf8')).is_spain is not True):
            raise ValueError('SCOPE_RECOVERY_NORMAL_SPAIN_BODY_REQUIRED')
        if any(q['status']!='NORMAL' for q in store.requests(plan['run_id'])):
            raise ValueError('NO_UNFINISHED_OR_FAILED_REQUEST_RECOVERY')
        if any(r.get('status_code')!=200 or r.get('access_state')!='NORMAL' for r in store.iter_responses(plan['run_id'])):
            raise ValueError('NO_OTHER_ACCESS_FAILURE_RECOVERY')
        authorize_v4_request(initial,source,auth);authorize_v4_request(final,source,auth)
        record={'kind':'USER_APPROVED_QUERY_ORDER_SCOPE_RECOVERY','run_id':plan['run_id'],
            'approval':approval,'source':source,'old_local_gate':local,'old_shared_gate':prior,
            'receipt_reference':reference,'observed_at':utc_text(),'new_network_requests':0,
            'receipt_or_request_modifications':0,'counter_reset':False}
        target=store.root/'gate_recoveries'/(digest(record)[:24]+'.json');immutable_json(target,sealed(record))
        normal={'state':'NORMAL','reason':'EXPLICIT_VERIFIED_QUERY_ORDER_SCOPE_RECOVERY',
            'scope_recovery_evidence':str(target),'observed_at':utc_text()}
        atomic_json(store.root/'access_gate.json',sealed({**normal,'previous_gate':local}),backup=True)
        atomic_json(shared,sealed({**normal,'previous_gate':prior}),backup=True)
        store.append_event(plan['run_id'],{'kind':record['kind'],'evidence_path':str(target),'observed_at':utc_text()})
        return record
