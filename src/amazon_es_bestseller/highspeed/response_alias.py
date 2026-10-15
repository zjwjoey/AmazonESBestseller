"""Explicit, literal response aliases. Never authorize an extra initial URL."""
import hashlib,json
from pathlib import Path
from bs4 import BeautifulSoup
from .budgets import aware_time,utc_text
from .v4_authorization import authorize_v4_request
from .v4_sources import amazon_url
from ..access.detector import detect_access_status
from ..multisource.storage import read_sealed,digest,immutable_json,sealed,atomic_json,file_lock

def _record(auth,source,initial,final,at):
    if not isinstance(at,str):raise ValueError('ALIAS_CAPTURE_TIME_REQUIRED')
    ref=auth.get('response_alias_reference') or {};path=Path(ref.get('path','__missing__'))
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=ref.get('sha256'):
        raise ValueError('EXPLICIT_RESPONSE_ALIAS_REQUIRED')
    a=json.loads(path.read_text('utf8'))
    if (a.get('approved') is not True or not a.get('approved_by') or not a.get('approval_id')
            or a.get('run_id')!=auth['run_id'] or a.get('plan_sha256')!=auth['plan_sha256']
            or a.get('store_root')!=auth['store_root'] or a.get('source_id')!=source['source_id']
            or a.get('initial_url')!=initial or a.get('final_url')!=final or initial==final
            or not amazon_url(initial) or not amazon_url(final) or not amazon_url(a.get('canonical_url'))
            or source['source_type']!='CUSTOMER_FAVORITES'
            or a.get('expires_at')!=auth['expires_at']
            or not aware_time(a['reviewed_at'])<=aware_time(at)<aware_time(a['expires_at'])):
        raise ValueError('EXACT_RESPONSE_ALIAS_BINDING_REQUIRED')
    authorize_v4_request(initial,source,auth,kind='PAGE')
    return a

def _body(receipt,source,alias):
    from .v4_adapters import parse_v4_page
    if (receipt.get('kind')!='PAGE' or receipt.get('origin')!='LIVE_CAPTURE' or receipt.get('status_code')!=200 or receipt.get('access_state')!='NORMAL'
            or receipt.get('request_url')!=alias['initial_url'] or receipt.get('final_url')!=alias['final_url']
            or receipt.get('source_id')!=source['source_id'] or receipt.get('run_id')!=alias['run_id']):
        raise ValueError('ACTUAL_NORMAL_ALIAS_RECEIPT_REQUIRED')
    body=Path(receipt['html_path']).read_bytes()
    if hashlib.sha256(body).hexdigest()!=receipt['sha256']:raise ValueError('ALIAS_BODY_SHA_MISMATCH')
    if detect_access_status(200,body.decode('utf8')).value!='NORMAL':raise ValueError('ALIAS_ACCESS_NOT_NORMAL')
    soup=BeautifulSoup(body,'lxml')
    if [v.get('href') for v in soup.select('link[rel=canonical]')]!=[alias['canonical_url']]:
        raise ValueError('ALIAS_CANONICAL_MISMATCH')
    headings={h.get_text(' ',strip=True) for h in soup.select('h1,h2')}
    if not set(alias['required_headings']).issubset(headings):raise ValueError('ALIAS_SOURCE_HEADING_MISMATCH')
    parsed=parse_v4_page(body.decode('utf8'),source=source,page_url=alias['initial_url'],
        run_id=alias['run_id'],observed_at=receipt['observed_at'],evidence_path=receipt['html_path'],
        http_status=200,origin='OFFLINE_FIXTURE')
    if parsed['page']['status']!='VERIFIED_WITH_PRODUCTS' or not parsed['observations']:
        raise ValueError('ALIAS_SOURCE_PRODUCTS_NOT_VERIFIED')
    return parsed

def _proof(alias,source,auth):
    ref=alias['receipt_reference'];path=Path(ref['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256']:raise ValueError('ALIAS_RECEIPT_SHA_MISMATCH')
    receipt=read_sealed(path)
    if path.resolve()!=(Path(auth['store_root'])/'runs'/auth['run_id']/'responses'/(receipt['request_id']+'.json')).resolve():
        raise ValueError('ALIAS_PROOF_EXACT_STORE_REQUIRED')
    reservation=read_sealed(path.parent.parent/'requests'/(receipt['request_id']+'.reserved.json'))
    if (reservation['plan_sha256']!=auth['plan_sha256'] or reservation['run_id']!=auth['run_id']
            or reservation['source_id']!=source['source_id'] or reservation['url']!=alias['initial_url']
            or reservation['kind']!='PAGE' or reservation['observed_at']!=receipt['observed_at']):
        raise ValueError('ALIAS_RESERVATION_BINDING_MISMATCH')
    _body(receipt,source,alias)
    return receipt

def authorize_v4_response(receipt,source,auth):
    kind=receipt['kind'];initial=receipt['request_url'];final=receipt.get('final_url')
    authorize_v4_request(initial,source,auth,kind=kind,descriptor=receipt.get('native_acp_descriptor'))
    try:
        authorize_v4_request(final,source,auth,kind=kind,descriptor=receipt.get('native_acp_descriptor'))
        return None
    except ValueError:
        if kind!='PAGE' or not auth.get('response_alias_reference'):raise
    alias=_record(auth,source,initial,final,receipt['observed_at'])
    _proof(alias,source,auth);_body(receipt,source,alias)
    return alias['approval_id']

def restore_reviewed_alias_gate(store,plan,auth):
    """No network. Handle only the exact user-approved scope stop, not access."""
    shared=Path(plan['shared_access_gate_path'])
    if digest(plan)!=auth['plan_sha256']:raise ValueError('ALIAS_EXACT_PLAN_REQUIRED')
    with store.run_lock(plan['run_id']),file_lock(shared.parent/'highspeed_live.lock'):
        ref=auth['response_alias_reference'];raw=Path(ref['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('ALIAS_APPROVAL_SHA_MISMATCH')
        proposed=json.loads(raw);source=next(s for s in plan['sources'] if s['source_id']==proposed['source_id'])
        alias=_record(auth,source,proposed['initial_url'],proposed['final_url'],utc_text())
        receipt=_proof(alias,source,auth)
        local=read_sealed(store.root/'access_gate.json');prior=read_sealed(shared)
        if (digest(local)!=alias['local_gate_sha256'] or digest(prior)!=alias['shared_gate_sha256']
                or local.get('state')!='UNKNOWN' or prior.get('state')!='UNKNOWN'
                or local.get('reason')!='UNREVIEWED_REDIRECT'
                or local.get('evidence_path')!=receipt['evidence_path'] or prior.get('evidence_path')!=receipt['evidence_path']):
            raise ValueError('EXACT_SCOPE_GATE_REQUIRED_NO_ACCESS_OVERRIDE')
        if any(q['status']=='RESERVED' for q in store.requests(plan['run_id'])):
            raise ValueError('AMBIGUOUS_REQUEST_NO_GATE_HANDLING')
        # Carry forward only the prior recorded, successful network handling;
        # do not mark any new transport failure/restriction as handled.
        previous=local.get('previous_gate') or {};handled=previous.get('agent_handled_request_ids',[])
        if handled:
            evidence=read_sealed(previous['agent_recovery_evidence'])
            check=read_sealed(evidence['normal_verification_receipt'])
            if (previous.get('state')!='NORMAL' or check.get('status_code')!=200 or check.get('access_state')!='NORMAL'
                    or check.get('run_id')!=plan['run_id'] or check.get('origin')!='LIVE_CAPTURE'
                    or hashlib.sha256(Path(check['html_path']).read_bytes()).hexdigest()!=check['sha256']):
                raise ValueError('PRIOR_NETWORK_HANDLING_PROOF_REQUIRED')
        failures={q['request_id'] for q in store.requests(plan['run_id']) if q['status'] not in {'NORMAL','RESERVED'}}
        if failures-set(handled):raise ValueError('UNHANDLED_ACCESS_FAILURE_NOT_SCOPE_ALIAS')
        evidence={'kind':'USER_APPROVED_EXACT_RESPONSE_ALIAS_SCOPE_HANDLING','run_id':plan['run_id'],
            'approval_reference':ref,'old_local_gate':local,'old_shared_gate':prior,
            'receipt_reference':alias['receipt_reference'],'agent_handled':True,'human_handled':False,'observed_at':utc_text()}
        path=store.root/'gate_recoveries'/(digest(evidence)[:24]+'.json');immutable_json(path,sealed(evidence))
        normal={'state':'NORMAL','agent_handled_request_ids':handled,'scope_alias_handling_evidence':str(path),
            'approval_id':alias['approval_id'],'previous_gate':local,'observed_at':utc_text()}
        atomic_json(store.root/'access_gate.json',sealed(normal),backup=True)
        atomic_json(shared,sealed({**normal,'previous_gate':prior}),backup=True)
        store.append_event(plan['run_id'],{'kind':'AUTHORIZED_RESPONSE_ALIAS_SCOPE_HANDLING','observed_at':utc_text(),'evidence_path':str(path)})
        return evidence
