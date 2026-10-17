"""Opt-in source-owned variation-family representative, never an ASIN alias.

The requested candidate and captured product remain different identities. The
source-owned current/parent chain is preserved explicitly. Only an explicit
parent or a listed member can resolve to the selected product.
"""
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup
from ..identity import asin_from_url
from ..multisource.storage import digest
from .quick_selection import file_sha

SCOPE='DETAIL_FAMILY_CONTEXT_REPRESENTATIVE_V1'


def read_policy(root, plan, auth):
    ref=(auth or {}).get('family_identity_policy_reference')
    if not ref:return None
    if (Path(ref['path']).resolve().parent!=Path(root).resolve()
            or file_sha(ref['path'])!=ref['sha256']):
        raise ValueError('FAMILY_POLICY_REFERENCE_CHANGED')
    value=json.loads(Path(ref['path']).read_text(encoding='utf8'))
    base=auth.get('parallel_base_authorization_reference')
    if not base or value.get('authorization_basis_reference')!=base or file_sha(base['path'])!=base['sha256']:
        raise ValueError('FAMILY_ORIGINAL_AUTHORIZATION_BINDING_REQUIRED')
    if (value.get('approved') is not True or not value.get('explicit_user_instruction')
            or value.get('approved_by')!='agent_under_explicit_user_delegation'
            or value.get('scope')!=SCOPE or value.get('run_id')!=plan['run_id']
            or value.get('runtime_root')!=str(Path(root).resolve())
            or value.get('plan_sha256')!=digest(plan) or value.get('code_sha')!=auth['code_sha']
            or value.get('preserve_requested_and_actual_ASIN') is not True
            or value.get('do_not_transfer_requested_rank') is not True
            or value.get('deduplicate_by_actual_ASIN') is not True):
        raise ValueError('EXPLICIT_FAMILY_IDENTITY_POLICY_REQUIRED')
    return {**value,'approval_reference':ref}


def family_evidence(task, html, meta):
    from ..access.detector import detect_access_status
    from .detail_bridge5500 import safe_url
    from .detail_v2 import _json_after
    requested=asin_from_url(task['request_url']);final=meta.get('final_url') or ''
    if (meta.get('status_code')!=200 or detect_access_status(200,html).value!='NORMAL'
            or meta.get('access_state') not in (None,'NORMAL')
            or meta.get('requested_url')!=task['request_url'] or not requested
            or not safe_url(task['request_url']) or not safe_url(final)):
        return None
    soup=BeautifulSoup(html,'lxml')
    if len(soup.select('#ppd'))!=1:return None
    inputs={str(e.get('value') or '').strip().upper() for e in
        soup.select('input#ASIN,input#productAsin,input[name=ASIN]') if e.get('value')}
    buybox={str(e.get('value') or '').strip().upper() for e in
        soup.select('#ppd #buybox input#ASIN[name=ASIN]')}
    if len(inputs)!=1 or inputs!=buybox:return None
    current=next(iter(inputs))
    if not re.fullmatch('[A-Z0-9]{10}',current):return None
    owners=soup.select('#ppd #twister_feature_div')
    if len(owners)!=1:return None
    scripts=[e.get_text() for e in owners[0].select('script') if
        re.search(r"P\.register\(['\"]twister-js-init-dpx-data['\"]\s*,",e.get_text())]
    if len(scripts)!=1:return None
    script=scripts[0]
    def unique(key):
        values=re.findall(r'"'+key+r'"\s*:\s*"([^"]*)"',script)
        return values[0] if values and len(set(values))==1 and re.fullmatch('[A-Z0-9]{10}',values[0]) else None
    parent=unique('parentAsin');landing=unique('landingAsin')
    display=_json_after(script,'"dimensionValuesDisplayData"')
    explicit={str(e.get('value') or '').strip().upper() for e in
        soup.select('input#parentASIN,input[name=parentASIN]')}
    canons=soup.select('link[rel=canonical]')
    if (unique('currentAsin')!=current or not parent or not landing
            or len(canons)!=1 or not safe_url(canons[0].get('href'))
            or asin_from_url(canons[0]['href']) not in {current,parent}
            or explicit and explicit!={parent} or not isinstance(display,dict)
            or current!=parent and (not isinstance(display.get(current),list) or not display[current])):
        return None
    # An old landing ASIN alone does not prove current family membership.
    linked=requested==parent or (isinstance(display.get(requested),list) and bool(display[requested]))
    if not linked or asin_from_url(final) not in {requested,current,parent}:return None
    from ..access.location import inspect_delivery_location
    location=inspect_delivery_location(html)
    if location.is_spain is not True or location.postal_code!='28001':return None
    return dict(rule='OWNED_TWISTER_FAMILY_CONTEXT_REPRESENTATIVE_V1',requested_asin=requested,
        current_asin=current,parent_asin=parent,landing_asin=landing,
        requested_listed_in_visible_member_map=requested in display,
        membership_basis=('EXPLICIT_PARENT' if requested==parent else
            'EXPLICIT_VISIBLE_MEMBER_MAP'),
        source='#ppd #twister_feature_div / twister-js-init-dpx-data',
        requested_attributes_not_assumed_equal_to_resolved=True)


def parse_family_detail(task, html, meta, policy):
    from .detail_bridge5500 import parse_candidate_detail,_extract_verified_detail
    result=parse_candidate_detail(task,html,meta)
    if result['status']!='IDENTITY_REVIEW':return result
    if (not policy or policy.get('approved') is not True or policy.get('scope')!=SCOPE
            or policy.get('run_id')!=task.get('run_id')):
        return result
    proof=family_evidence(task,html,meta)
    if not proof:return result
    soup=BeautifulSoup(html,'lxml');canonicals=[e.get('href') for e in soup.select('link[rel=canonical]')]
    result=_extract_verified_detail(task,html,meta,soup,proof['current_asin'],canonicals,
        proof,meta['final_url'])
    if result['status']!='SUCCESS':return result
    record=result['record'];record.pop('active_child_canonical_parent_evidence',None)
    record.update(identity_status='APPROVED_VARIATION_FAMILY_REPRESENTATIVE',
        requested_asin=proof['requested_asin'],resolved_asin=proof['current_asin'],
        family_parent_asin=proof['parent_asin'],family_identity_evidence=proof,
        family_identity_policy_reference=policy['approval_reference'],
        requested_source_memberships=record['source_memberships'],source_memberships=[],
        source_rank_transfer_allowed=False)
    if proof['current_asin']==proof['parent_asin']:record['parent_asin']=None
    result['identity_status']=record['identity_status']
    return result

