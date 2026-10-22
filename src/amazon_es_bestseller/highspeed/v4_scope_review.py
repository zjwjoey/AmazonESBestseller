"""Offline correction of one proven query-order scope false positive.

This does not recover an access restriction or probe Amazon. The original
UNKNOWN gate and stop remain immutable evidence. Every other UNKNOWN reason,
HTTP restriction, changed parameter, expired approval or missing proof fails.
"""
from pathlib import Path
import hashlib
import uuid
from ..access.detector import detect_access_status
from ..multisource.storage import read_sealed,immutable_json,sealed,atomic_json,file_lock,digest
from .budgets import aware_time,utc_text
from .v4_navigation import exact_query_token_reordering
from .v4_authorization import validate_plan_v4,authorize_v4_request
from .v4_adapters import parse_v4_page


def correct_query_order_scope_false_positive(store,plan,authorization):
    validate_plan_v4(plan)
    a=authorization;now=aware_time()
    if (plan['stage']!='T1' or a.get('approved') is not True or not a.get('approval_id')
            or a.get('approved_by')!='USER_EXPLICIT_CHAT' or a.get('run_id')!=plan['run_id']
            or a.get('plan_sha256')!=digest(plan) or a.get('store_root')!=str(store.root.resolve())
            or not aware_time(a['reviewed_at'])<=now<aware_time(a['expires_at'])
            or '修复' not in a.get('authorization_text','')):
        raise ValueError('V4_CURRENT_EXPLICIT_REPAIR_TASK_REQUIRED')
    shared=Path(plan['shared_access_gate_path'])
    if str(shared.resolve())!=a.get('shared_access_gate_path'):
        raise ValueError('V4_EXACT_SHARED_GATE_REQUIRED')
    with store.run_lock(plan['run_id']),file_lock(shared.parent/'highspeed_live.lock'):
        if (store.get_run(plan['run_id'])['status']!='STOPPED'
                or not aware_time(a['reviewed_at'])<=aware_time()<aware_time(a['expires_at'])):
            raise ValueError('V4_STOPPED_CURRENT_APPROVED_RUN_REQUIRED')
        local=store.get_gate();global_gate=read_sealed(shared)
        if (local.get('state')!='UNKNOWN' or local.get('reason')!='UNREVIEWED_REDIRECT'
                or global_gate.get('state')!='UNKNOWN' or global_gate.get('run_id')!=plan['run_id']
                or global_gate.get('evidence_path')!=local.get('evidence_path')
                or local.get('run_id')!=plan['run_id']):
            raise ValueError('V4_ONLY_EXACT_QUERY_SCOPE_FALSE_POSITIVE_CAN_BE_REVIEWED')
        path=Path(local['evidence_path']);r=read_sealed(path)
        approved=r.get('request_url');actual=r.get('final_url')
        if (r.get('run_id')!=plan['run_id'] or r.get('kind')!='PAGE'
                or r.get('origin')!='LIVE_CAPTURE' or r.get('status_code')!=200 or r.get('access_state')!='NORMAL'
                or actual!=r.get('url') or actual==approved
                or not exact_query_token_reordering(actual,approved)):
            raise ValueError('V4_ACTUAL_200_EXACT_TOKEN_REORDER_PROOF_REQUIRED')
        if not aware_time(a['reviewed_at'])<=aware_time(r['observed_at'])<aware_time(a['expires_at']):
            raise ValueError('V4_ORIGINAL_CAPTURE_APPROVAL_TIME_REQUIRED')
        source=next(s for s in plan['sources'] if s['source_id']==r['source_id'])
        if approved not in a['sources'][source['source_id']]['page_urls']:
            raise ValueError('V4_ORIGINAL_EXACT_APPROVED_REQUEST_REQUIRED')
        authorize_v4_request(actual,source,a,kind='PAGE')
        reservation=read_sealed(path.parent.parent/'requests'/(r['request_id']+'.reserved.json'))
        if (reservation.get('url')!=approved or reservation.get('run_id')!=plan['run_id']
                or reservation.get('source_id')!=source['source_id'] or reservation.get('kind')!='PAGE'
                or reservation.get('plan_sha256')!=digest(plan)):
            raise ValueError('V4_ACTUAL_RESERVED_REQUEST_REQUIRED')
        body=Path(r['html_path']).read_bytes()
        if hashlib.sha256(body).hexdigest()!=r['sha256'] or detect_access_status(200,body.decode('utf8')).value!='NORMAL':
            raise ValueError('V4_NORMAL_UNCHANGED_CAPTURE_REQUIRED')
        parsed=parse_v4_page(body.decode('utf8'),source=source,page_url=approved,run_id=plan['run_id'],
            observed_at=r['observed_at'],snapshot_id=plan['snapshot_id'],evidence_path=r['html_path'],
            http_status=200,origin='LIVE_CAPTURE')
        if parsed['page']['status']!='VERIFIED_WITH_PRODUCTS':
            raise ValueError('V4_SOURCE_PRODUCTS_DOM_PROOF_REQUIRED')
        directory=store.root/'scope_corrections';proof_path=directory/(uuid.uuid4().hex+'.json')
        proof={'local_gate_before':local,'shared_gate_before':global_gate,'approved_task_id':a['approval_id'],
            'original_request_receipt_reference':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
            'approved_url':approved,'actual_url':actual,'body_sha256':r['sha256'],
            'reviewed_at':utc_text(),'new_network_requests':0,'agent_handled':True,'human_handled':False,
            'reason':'BYTE_IDENTICAL_UNIQUE_QUERY_TOKENS_REORDERED_ONLY','actual_product_cards':len(parsed['observations'])}
        immutable_json(proof_path,sealed(proof))
        # Index this already captured, unchanged PAGE after offline scope review.
        key=source['source_id']+'_'+hashlib.sha256(approved.encode()).hexdigest()[:12]
        page={**parsed['page'],'run_id':plan['run_id'],'snapshot_id':plan['snapshot_id'],'page_key':key,'source':source,
            'capture_receipt_reference':proof['original_request_receipt_reference'],
            'source_type_verified':True,'source_context_valid':True,'source_representation':'RENDERED_DOM',
            'actual_final_url':actual,'scope_correction_reference':str(proof_path),'acp_captures':[],
            'execution_status':'EXISTING_PAGE_OFFLINE_QUERY_SCOPE_REVIEW_NOT_COMPLETE'}
        store.commit_page(plan['run_id'],key,page,parsed['observations'])
        atomic_json(store.root/'access_gate.json',sealed({'state':'NORMAL','previous_gate':local,
            'scope_correction_evidence':str(proof_path),'agent_handled':True,'human_handled':False,'reviewed_at':utc_text()}),backup=True)
        atomic_json(shared,sealed({'state':'NORMAL','previous_gate':global_gate,
            'scope_correction_evidence':str(proof_path),'agent_handled':True,'human_handled':False,'reviewed_at':utc_text()}),backup=True)
        store.append_event(plan['run_id'],{'kind':'V4_OFFLINE_QUERY_SCOPE_FALSE_POSITIVE_CORRECTED',
            'evidence_path':str(proof_path),'request_id':r['request_id'],'new_network_requests':0,
            'agent_handled':True,'human_handled':False,'original_UNKNOWN_retained':True})
        return proof
