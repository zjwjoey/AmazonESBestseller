"""Receipt-bound navigation within reviewed department roots and finite caps."""
from copy import deepcopy
import hashlib
from pathlib import Path
from ..multisource.storage import digest,read_sealed
from .three_contract import verify_source,source_from_navigation

POLICY={'contract':'OWNED_CATEGORY_PAGE_V1','max_pages_per_source':160,
        'max_category_depth':6,'max_sources_per_department':120}
_RUNTIME={'page_urls','pagination_evidence','snapshot_admission_root','snapshot_navigation_receipts','favorite_budget_reference'}


def _proofs(source):
    refs=[];current=source
    for _ in range(60):
        if current.get('navigation_evidence'):refs.append(current['navigation_evidence'])
        refs.extend(current.get('pagination_evidence',[]))
        current=current.get('navigation_parent')
        if not current:break
    return refs


def validate_admitted_source(source,root,run_id,plan_sha256=None):
    from .favorite_budget import page_limit
    if source.get('favorite_budget_reference'):
        from .favorite_budget import read_amendment
        amendment=read_amendment(source['favorite_budget_reference'],run_id,plan_sha256,
            source.get('snapshot_admission_root',source['source_id']))
        plan_sha256=plan_sha256 or amendment['plan_sha256']
    if root.get('owned_page_navigation')!=POLICY:
        raise ValueError('EXACT_BOUNDED_OWNED_PAGE_POLICY_REQUIRED')
    if (source.get('snapshot_admission_root',source['source_id'])!=root['source_id']
            or source['source_type']!=root['source_type']
            or source.get('collection_category_key')!=root.get('collection_category_key')
            or source.get('collection_category_evidence')!=root.get('collection_category_evidence')
            or source.get('owned_page_navigation')!=POLICY
            or len(source['page_urls'])>page_limit(source,run_id,plan_sha256)):
        raise ValueError('RUNTIME_NAVIGATION_ROOT_OR_BUDGET_MISMATCH')
    node=source;depth=0;found=False
    while depth<=POLICY['max_category_depth']:
        if node['source_id']==root['source_id']:
            if {k:v for k,v in node.items() if k not in _RUNTIME}!={k:v for k,v in root.items() if k not in _RUNTIME}:
                raise ValueError('RUNTIME_NAVIGATION_APPROVED_ROOT_CHANGED')
            found=True;break
        node=node.get('navigation_parent')
        if not node:break
        depth+=1
    if not found:raise ValueError('RUNTIME_NAVIGATION_OUTSIDE_REVIEWED_DEPARTMENT')
    verify_source(source)
    frozen={digest(r) for r in _proofs(root)}
    receipts={r['navigation_sha256']:r['receipt_reference'] for r in source.get('snapshot_navigation_receipts',[])}
    for ref in _proofs(source):
        key=digest(ref)
        if key in frozen:continue
        receipt_ref=receipts.get(key)
        if not receipt_ref:raise ValueError('CURRENT_NAVIGATION_PARENT_RECEIPT_REQUIRED')
        p=Path(receipt_ref['path'])
        if hashlib.sha256(p.read_bytes()).hexdigest()!=receipt_ref['sha256']:
            raise ValueError('CURRENT_NAVIGATION_RECEIPT_SHA_MISMATCH')
        receipt=read_sealed(p)
        if (receipt.get('run_id')!=run_id or receipt.get('origin')!='LIVE_CAPTURE'
                or receipt.get('kind')!='PAGE' or receipt.get('status_code')!=200
                or receipt.get('access_state')!='NORMAL' or receipt.get('request_url')!=ref['page_url']
                or receipt.get('sha256')!=ref['evidence_sha256']):
            raise ValueError('CURRENT_NAVIGATION_NORMAL_SAME_RUN_PARENT_REQUIRED')
        reserved=read_sealed(p.parent.parent/'requests'/(receipt['request_id']+'.reserved.json'))
        if reserved['url']!=ref['page_url'] or reserved['source_id']!=receipt['source_id'] or (
                plan_sha256 is not None and reserved['plan_sha256']!=plan_sha256):
            raise ValueError('CURRENT_NAVIGATION_PARENT_RESERVATION_MISMATCH')
    return source


def extend_source(parent,reference,receipt_reference,root,*,favorite_budget_reference=None):
    refs=deepcopy(parent.get('snapshot_navigation_receipts',[]))
    refs.append({'navigation_sha256':digest(reference),'receipt_reference':receipt_reference})
    if reference['relationship']=='NEXT_PAGE':
        child=deepcopy(parent)
        if favorite_budget_reference and child['source_type']=='CUSTOMER_FAVORITES':
            child['favorite_budget_reference']=deepcopy(favorite_budget_reference)
        from .favorite_budget import page_limit
        if child.get('favorite_budget_reference'):
            limit=page_limit(child,read_sealed(receipt_reference['path'])['run_id'])
        else:limit=POLICY['max_pages_per_source']
        if len(parent['page_urls'])>=limit:return None
        child['page_urls'].append(reference['url'])
        child.setdefault('pagination_evidence',[]).append(deepcopy(reference))
    elif reference['relationship']=='CHILD_CATEGORY' and parent['source_type'] in {'BEST_SELLERS','NEW_RELEASES'}:
        current=parent;depth=0
        while current['source_id']!=root['source_id']:
            current=current.get('navigation_parent')
            if not current:return None
            depth+=1
        if depth>=POLICY['max_category_depth']:return None
        child=source_from_navigation(reference,parent)
    else:return None
    child.update(snapshot_admission_root=root['source_id'],snapshot_navigation_receipts=refs,
        collection_category_key=root['collection_category_key'],
        collection_category_evidence=deepcopy(root['collection_category_evidence']),owned_page_navigation=deepcopy(POLICY))
    return child
