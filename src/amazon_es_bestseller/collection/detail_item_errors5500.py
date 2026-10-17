"""Explicit task-local data errors remain failures while the queue continues.

This is not an access recovery or identity-family acceptance. Saved failures
are consumed once, never retried or counted as successful product details.
"""
import json
from pathlib import Path

from ..access.detector import detect_access_status
from ..access.location import inspect_delivery_location
from ..multisource.storage import digest,immutable_json,read_sealed,sealed
from .quick_selection import file_sha
from .detail_bridge5500 import SUCCESS,utc

STATUSES = ('IDENTITY_REVIEW','PARSE_FAILED')
GLOBAL_REASONS = {'SPAIN_DELIVERY_LOCATION_NOT_VERIFIED','NON_AMAZON_PRODUCT_DESTINATION'}


def _checked(reference,root):
    path=Path(reference['path'])
    if not path.resolve().is_relative_to(Path(root).resolve()) or file_sha(path)!=reference['sha256']:
        raise ValueError('ITEM_ERROR_EVIDENCE_CHANGED')
    return path


def read_policy(root,plan,auth):
    reference=auth.get('item_error_continuation_reference')
    if reference is None:return None
    doc=json.loads(_checked(reference,root).read_text(encoding='utf8'))
    if (doc.get('approved') is not True or doc.get('approved_by')!='agent_under_explicit_user_delegation'
            or not doc.get('explicit_user_instruction')
            or doc.get('scope')!='CONTINUE_RECORDED_ITEM_DATA_ERRORS_NO_RETRY'
            or doc.get('run_id')!=plan['run_id'] or doc.get('runtime_root')!=str(Path(root).resolve())
            or doc.get('plan_sha256')!=digest(plan) or doc.get('code_sha')!=auth.get('code_sha')
            or doc.get('allowed_statuses')!=list(STATUSES)
            or any(doc.get(key) is not True for key in ('no_successful_task_replay','preserve_failed_status',
                       'stop_on_access_restriction','stop_on_persistence_failure'))):
        raise ValueError('EXPLICIT_CURRENT_ITEM_ERROR_POLICY_REQUIRED')
    return doc


def eligible(result,meta):
    return (result.get('status') in STATUSES and result.get('reason') not in GLOBAL_REASONS
            and meta.get('access_state')=='NORMAL' and meta.get('status_code')==200)


def _validate_evidence(root,plan,task,result):
    meta=json.loads(Path(result['response_metadata_path']).read_text(encoding='utf8'))
    if not eligible(result,meta):raise ValueError('NOT_A_NORMAL_ITEM_DATA_FAILURE')
    for key,hashkey in (('html_path','html_sha256'),('receipt_path','receipt_sha256'),
                        ('response_metadata_path','response_metadata_sha256')):
        _checked(dict(path=result[key],sha256=result[hashkey]),root)
    receipt=read_sealed(result['receipt_path'])
    if (receipt.get('state')!='NORMAL' or receipt.get('status_code')!=200
            or receipt.get('request_role')!='DETAIL' or receipt.get('run_id')!=plan['run_id']
            or receipt.get('task_id')!=task['task_id'] or receipt.get('request_url')!=task['request_url']
            or receipt.get('html_sha256')!=result['html_sha256']
            or meta.get('requested_url')!=task['request_url'] or meta.get('receipt_path')!=result['receipt_path']):
        raise ValueError('ITEM_ERROR_RECEIPT_BINDING_CHANGED')
    body=Path(result['html_path']).read_text(encoding='utf8')
    location=inspect_delivery_location(body)
    if (detect_access_status(200,body).value!='NORMAL' or location.is_spain is not True
            or location.postal_code!='28001'):
        raise ValueError('ITEM_ERROR_ACCESS_OR_DELIVERY_NOT_NORMAL')
    return receipt


def persist_failure(bridge,auth,task,result):
    if read_policy(bridge.root,bridge.plan,auth) is None:
        raise ValueError('EXPLICIT_CURRENT_ITEM_ERROR_POLICY_REQUIRED')
    receipt=_validate_evidence(bridge.root,bridge.plan,task,result)
    if receipt.get('authorization_sha256')!=digest(auth):raise ValueError('ITEM_ERROR_WRONG_AUTHORIZATION')
    cp=bridge.root/'tasks'/task['task_id']/'checkpoint.json'
    current=read_sealed(cp)
    if current.get('task_id')!=task['task_id'] or current.get('run_id')!=bridge.plan['run_id']:
        raise ValueError('ITEM_ERROR_WRONG_CHECKPOINT')
    if any(current.get(k)!=v for k,v in result.items()):raise ValueError('ITEM_ERROR_RESULT_CHANGED')
    event=dict(at=utc(),run_id=bridge.plan['run_id'],task_id=task['task_id'],
        plan_sha256=digest(bridge.plan),code_sha=auth['code_sha'],failed_not_successful=True,
        no_retry=True,no_family_acceptance=True,policy_reference=auth['item_error_continuation_reference'],
        authorization_sha256=digest(auth),checkpoint_reference=dict(path=str(cp),sha256=file_sha(cp)),
        result=result)
    immutable_json(bridge.root/'item_errors'/(task['task_id']+'.json'),sealed(event))


def reviewed_failure(bridge,auth,task,current):
    if read_policy(bridge.root,bridge.plan,auth) is None:return False
    path=bridge.root/'item_errors'/(task['task_id']+'.json')
    if not path.exists():return False
    event=read_sealed(path)
    if (event.get('run_id')!=bridge.plan['run_id'] or event.get('task_id')!=task['task_id']
            or event.get('plan_sha256')!=digest(bridge.plan) or event.get('code_sha')!=auth['code_sha']
            or event.get('failed_not_successful') is not True or event.get('no_retry') is not True
            or event.get('no_family_acceptance') is not True):
        raise ValueError('ITEM_ERROR_LOG_SCOPE_CHANGED')
    _checked(event['checkpoint_reference'],bridge.root)
    original_auth={**auth,'item_error_continuation_reference':event['policy_reference']}
    read_policy(bridge.root,bridge.plan,original_auth)
    result=event['result']
    if any(current.get(k)!=v for k,v in result.items()):raise ValueError('ITEM_ERROR_RESULT_CHANGED')
    receipt=_validate_evidence(bridge.root,bridge.plan,task,result)
    if receipt.get('authorization_sha256')!=event.get('authorization_sha256'):
        raise ValueError('ITEM_ERROR_GENERATION_BINDING_CHANGED')
    return True


def pending_tasks(bridge,auth):
    from .identity_quarantine5500 import reviewed_identity_quarantines
    if read_policy(bridge.root,bridge.plan,auth) is None:
        from .detail_dual_runtime5500 import pending_tasks as original
        return original(bridge,auth)
    known=reviewed_identity_quarantines(bridge.root,bridge.plan,auth);pending=[]
    for task in bridge.plan['tasks']:
        current=bridge.outcomes.get(task['task_id'])
        if current is None:pending.append(task)
        elif current['status'] in SUCCESS:bridge.verify_saved_success(task,current)
        elif task['task_id'] not in known and not reviewed_failure(bridge,auth,task,current):
            raise ValueError('UNREVIEWED_FAILURE_BLOCKS_CONTINUATION: '+task['task_id'])
    return pending
