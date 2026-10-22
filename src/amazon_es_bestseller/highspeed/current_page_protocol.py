"""Current three-source PAGE proof; never an eight-entry T1/T2 acceptance."""
import hashlib
import json
from pathlib import Path
from ..access.location import inspect_delivery_location
from ..multisource.storage import read_sealed, digest
from .v4_quality import audit_v4

CONTRACT='CURRENT_THREE_SOURCE_PAGE_PROTOCOL_V1'


def verify_current_protocol(reference):
    from .v4_authorization import _PREREQUISITES, validate_v4_authorization
    path=Path(reference['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=reference['sha256']:
        raise ValueError('CURRENT_PROTOCOL_REFERENCE_SHA_MISMATCH')
    cache=_PREREQUISITES.get()
    key=('CURRENT_PAGE_PROTOCOL',str(path.resolve()),reference['sha256'])
    old=cache.get(key) if cache is not None else None
    if old and all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in old['files']) and all(
            sorted(p.name for p in directory.glob('*.json'))==names for directory,names in old['directories']):
        return old['summary']
    value=json.loads(path.read_bytes())
    if value.get('contract')!=CONTRACT:
        raise ValueError('CURRENT_PAGE_PROTOCOL_REQUIRED')
    files={path.resolve()}
    def read(ref):
        p=Path(ref['path']).resolve();body=p.read_bytes()
        if hashlib.sha256(body).hexdigest()!=ref['sha256']:raise ValueError('CURRENT_PROTOCOL_DEPENDENCY_SHA_MISMATCH')
        files.add(p)
        return json.loads(body)
    plan=read(value['plan_reference']);auth=read(value['authorization_reference'])
    if plan['stage']!='T1' or plan.get('current_protocol_reference'):
        raise ValueError('INDEPENDENT_CURRENT_PAGE_PROBE_REQUIRED')
    root=Path(value['run_directory']).resolve()
    if read_sealed(root/'plan.json')!=plan:raise ValueError('CURRENT_PROTOCOL_IMMUTABLE_PLAN_MISMATCH')
    files.add(root/'plan.json')
    sources=plan['sources'];families={'BEST_SELLERS','NEW_RELEASES','CUSTOMER_FAVORITES'}
    if len(sources)!=3 or {s['source_type'] for s in sources}!=families:
        raise ValueError('CURRENT_PROTOCOL_THREE_TASK_FAMILIES_REQUIRED')
    if any(scope.get(k) for scope in auth['sources'].values() for k in
           ('allow_native_acp','allow_native_dynamic','allow_held_native_carousel')):
        raise ValueError('CURRENT_PROTOCOL_PAGE_ONLY_REQUIRED')
    requests=[read_sealed(p) for p in (root/'requests').glob('*.json')]
    receipts=[read_sealed(p) for p in (root/'responses').glob('*.json')]
    if len(requests)!=3 or len(receipts)!=3 or {r['request_id'] for r in requests}!={r['request_id'] for r in receipts}:
        raise ValueError('CURRENT_PROTOCOL_EXACT_COMPLETED_REQUEST_SET_REQUIRED')
    reservations={r['request_id']:r for r in requests}
    pages=[];rows=[]
    for p in (root/'pages').glob('*.json'):
        files.add(p);j=read_sealed(p)
        if j['run_id']!=plan['run_id']:raise ValueError('CURRENT_PROTOCOL_FOREIGN_JOURNAL')
        pages.append(j['page']);rows.extend(j['observations'])
    if len(pages)!=3 or {p['source_type'] for p in pages}!=families:
        raise ValueError('CURRENT_PROTOCOL_THREE_PAGE_FRAMES_REQUIRED')
    if not audit_v4(pages,rows,sources,require_live=True)['central_full_dom_review_passed']:
        raise ValueError('CURRENT_PROTOCOL_FULL_DOM_REPLAY_FAILED')
    for receipt in receipts:
        q=reservations[receipt['request_id']]
        if (q['plan_sha256']!=digest(plan) or q['run_id']!=plan['run_id'] or q['kind']!='PAGE'
                or q['url']!=receipt['request_url'] or q['source_id']!=receipt['source_id']):
            raise ValueError('CURRENT_PROTOCOL_RESERVATION_BINDING_MISMATCH')
        if (receipt.get('kind')!='PAGE' or receipt.get('request_method')!='GET'
                or receipt.get('status_code')!=200 or receipt.get('access_state')!='NORMAL'
                or receipt.get('run_id')!=plan['run_id']):
            raise ValueError('CURRENT_PROTOCOL_REAL_NORMAL_PAGE_RECEIPT_REQUIRED')
        validate_v4_authorization(plan,auth,{'state':'NORMAL'},now=receipt['observed_at'],store_root=root.parent.parent)
        body=Path(receipt['html_path']).read_bytes()
        if hashlib.sha256(body).hexdigest()!=receipt['sha256'] or inspect_delivery_location(body.decode('utf8')).is_spain is not True:
            raise ValueError('CURRENT_PROTOCOL_BODY_OR_SPAIN_DESTINATION_INVALID')
    def collect(obj):
        if isinstance(obj,dict):
            for k,v in obj.items():
                if isinstance(v,str) and (k=='path' or k.endswith('_path')) and Path(v).is_file():files.add(Path(v).resolve())
                elif isinstance(v,(list,dict)):collect(v)
        elif isinstance(obj,list):
            for v in obj:collect(v)
    collect([plan,auth,pages,receipts])
    for folder in ('requests','responses'):
        files.update((root/folder).glob('*.json'))
    summary={'contract':CONTRACT,'probe_run_id':plan['run_id'],'plan_sha256':digest(plan),
             'verified_source_families':sorted(families),'actual_PAGE_requests':3,
             'native_protocol_approved':False,'legacy_T1_T2_acceptance_claimed':False,
             'capture_times':[r['observed_at'] for r in receipts]}
    if cache is not None:
        cache[key]={'files':[(p,hashlib.sha256(p.read_bytes()).hexdigest()) for p in files],
                    'directories':[(root/f,sorted(p.name for p in (root/f).glob('*.json'))) for f in ('requests','responses','pages')],
                    'summary':summary}
    return summary
