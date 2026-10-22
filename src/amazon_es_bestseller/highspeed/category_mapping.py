"""Offline reviewed source-category mapping; no product exclusion or network.

A namespace rule is an explicitly reviewed department-subtree policy, not a
heuristic inferred from a URL slug. Every policy must cite an actual root
navigation anchor and exact saved HTML hash. Mixed departments remain unknown.
"""
from __future__ import annotations
import hashlib
import json
from functools import lru_cache
from copy import deepcopy
import re
from pathlib import Path
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from ..multisource.contracts import RESEARCH_CATEGORIES, SOURCE_ROOTS
from ..multisource.sources import normalize_source_url, source_type_from_url, browse_node_id

VERSION = "reviewed_category_mapping_v3.2"

class _ReviewedRules(tuple):
    pass


def namespace_from_url(url):
    url=normalize_source_url(url)
    if not url or not source_type_from_url(url):
        return None
    path=re.sub(r"^/-/(?:en|es)(?=/)", "", urlsplit(url).path)
    prefix=urlsplit(SOURCE_ROOTS[source_type_from_url(url)]).path
    if not (path == prefix or path.startswith(prefix+'/')):
        return None
    if path == prefix:
        return None
    return path[len(prefix)+1:].split('/')[0] or None


def validate_mapping_rules(rules):
    """Validate reviewed policy plus literal anchor. Return immutable copies.

    Offline proof never implies current source availability. Node rules require
    an exact URL/node match; namespace policies require explicit subtree review.
    """
    result=[]
    cache={}
    for raw in rules:
        rule=dict(raw)
        if rule.get('research_category') not in RESEARCH_CATEGORIES:
            raise ValueError('UNKNOWN_RESEARCH_CATEGORY')
        if rule.get('scope') not in {'EXACT_URL','BROWSE_NODE','EXACT_NODE','REVIEWED_DEPARTMENT_SUBTREE'}:
            raise ValueError('MAPPING_SCOPE_REQUIRED')
        if not rule.get('reviewed') or not rule.get('reviewer') or not rule.get('review_reason'):
            raise ValueError('EXPLICIT_MAPPING_REVIEW_REQUIRED')
        proof=rule.get('evidence') or {}
        path=Path(proof.get('html_path') or '')
        sha=proof.get('html_sha256')
        if not path.is_file() or len(str(sha or '')) !=64:
            raise ValueError('MAPPING_HTML_EVIDENCE_REQUIRED')
        key=(str(path),sha)
        if key not in cache:
            body=path.read_bytes()
            if hashlib.sha256(body).hexdigest()!=sha:
                raise ValueError('MAPPING_HTML_HASH_MISMATCH')
            cache[key]=BeautifulSoup(body,'lxml')
        anchor=next((a for a in cache[key].select('a[href]')
                     if a.get('href')==proof.get('raw_href')
                     and a.get_text(' ',strip=True)==proof.get('label_raw')),None)
        if anchor is None:
            raise ValueError('MAPPING_LITERAL_ANCHOR_MISSING')
        target=normalize_source_url(proof['raw_href'],base_url=proof.get('page_url') or 'https://www.amazon.es')
        if not source_type_from_url(target):
            raise ValueError('MAPPING_SOURCE_ANCHOR_REQUIRED')
        if rule['scope']=='REVIEWED_DEPARTMENT_SUBTREE':
            namespace=namespace_from_url(target)
            # The proof must be the actual department root, not an arbitrary
            # descendant used to label unrelated siblings.
            clean=re.sub(r"^/-/(?:en|es)(?=/)",'',urlsplit(target).path)
            root=urlsplit(SOURCE_ROOTS[source_type_from_url(target)]).path
            if not namespace or clean != root+'/'+namespace or rule.get('namespace')!=namespace:
                raise ValueError('DEPARTMENT_ROOT_PROOF_REQUIRED')
        elif rule['scope']=='EXACT_URL':
            if normalize_source_url(rule.get('source_url'))!=target:
                raise ValueError('MAPPING_EXACT_URL_MISMATCH')
        elif not rule.get('browse_node_id') or str(rule['browse_node_id'])!=browse_node_id(target):
            raise ValueError('MAPPING_NODE_MISMATCH')
        if rule['scope']=='EXACT_NODE':
            heading=proof.get('parent_heading_raw')
            nav=cache[key].select_one('#zg_browseRoot, [class*="zg-browse-root"]')
            if not heading or not any(h.get_text(' ',strip=True)==heading for h in cache[key].select('h1')):
                raise ValueError('NODE_PARENT_CONTEXT_MISMATCH')
            if nav is None or nav.get_text(' ',strip=True)!=proof.get('navigation_text_raw') or not any(parent is nav for parent in anchor.parents):
                raise ValueError('NODE_NAVIGATION_CONTEXT_MISMATCH')
        result.append(rule)
    return _ReviewedRules(result)


@lru_cache(maxsize=32)
def _prior_plan_memberships(path, sha):
    proof=Path(path)
    if not proof.is_file():
        return frozenset()
    body=proof.read_bytes()
    if hashlib.sha256(body).hexdigest()!=sha:
        return frozenset()
    try:
        data=json.loads(body)
    except (ValueError,UnicodeError):
        return frozenset()
    return frozenset((normalize_source_url(s.get('source_url') or ''), c.get('research_category'))
                     for c in data.get('categories',[]) for s in c.get('sources',[]))


def _verified_prior(source, url, category):
    for proof in source.get('mapping_evidence') or []:
        if not isinstance(proof,dict):
            continue
        if proof.get('research_category') != category:
            continue
        if (url,category) in _prior_plan_memberships(str(proof.get('plan_path') or ''),str(proof.get('plan_sha256') or '')):
            return True
    return False


def load_mapping_rules(config_path):
    """Load rules relative to the config's declared evidence base, not cwd."""
    config_path=Path(config_path).resolve()
    data=json.loads(config_path.read_text(encoding='utf8'))
    base=(config_path.parent/str(data.get('evidence_base') or '.')).resolve()
    rules=[]
    for raw in data.get('rules',[]):
        rule=dict(raw); rule['evidence']=dict(raw.get('evidence') or {})
        path=Path(rule['evidence'].get('html_path') or '')
        if not path.is_absolute():
            rule['evidence']['html_path']=str((base/path).resolve())
        rules.append(rule)
    return validate_mapping_rules(rules)


def map_category(source, rules):
    """Map using already validated rules; preserve all conflicting evidence.

    Consumers load rules via validate_mapping_rules once per batch. An exact
    reviewed source mapping is included only when its proof exists. No title,
    Action relevance, popularity, rating, or type filter is applied.
    """
    if not isinstance(rules, _ReviewedRules):
        rules=validate_mapping_rules(rules)
    url=normalize_source_url(source.get('actual_source_url') or source.get('source_url') or '')
    matches=[]
    original_prior=source.get('original_research_category') if 'original_research_category' in source else source.get('research_category')
    prior=original_prior if original_prior in RESEARCH_CATEGORIES and _verified_prior(source,url,original_prior) else source.get('research_category')
    if prior in RESEARCH_CATEGORIES and _verified_prior(source,url,prior):
        matches.append({'research_category':prior,'kind':'PRIOR_EXPLICIT_REVIEWED',
                        'evidence':source['mapping_evidence']})
    for rule in rules:
        if rule.get('source_types') and source_type_from_url(url) not in rule['source_types']:
            continue
        scope=rule.get('scope')
        match=(scope=='EXACT_URL' and normalize_source_url(rule.get('source_url'))==url
               or scope in {'BROWSE_NODE','EXACT_NODE'} and str(rule.get('browse_node_id'))==browse_node_id(url)
               and namespace_from_url(rule.get('evidence',{}).get('raw_href',''))==namespace_from_url(url)
               or scope=='REVIEWED_DEPARTMENT_SUBTREE' and rule.get('namespace')==namespace_from_url(url))
        if match:
            matches.append(rule)
    exact=[m for m in matches if m.get('scope') in {'EXACT_URL','BROWSE_NODE','EXACT_NODE'}]
    if exact:
        # Specific node policies supersede broad department policies, while
        # prior explicitly reviewed human planning remains conflict evidence.
        matches=[m for m in matches if m.get('scope')!='REVIEWED_DEPARTMENT_SUBTREE']
    categories=sorted({m['research_category'] for m in matches})
    return {'original_amazon_category':source.get('amazon_category') or source.get('source_category'),
            'category_path':list(source.get('category_path') or []),
            'browse_node_id':source.get('browse_node_id') or browse_node_id(url),
            'source_conflict':len(categories)>1,
            'research_category':categories[0] if len(categories)==1 else None,
            'category_mapping_status':'VERIFIED' if len(categories)==1 else 'SOURCE_CONFLICT' if categories else 'UNKNOWN',
            'category_mapping_version':VERSION,'category_mapping_evidence':matches,
            'category_mapping_candidates':categories,'original_research_category':original_prior}



def map_observation_category(row, parent_source, rules, *, parent_html_path,
                             parent_html_sha256, cache=None):
    """Map the exact card's literal category context, not a root-wide guess.

    SERVER requires raw href/rank/local slot in the hash-bound parent DOM.
    Carousel ACP requires an immutable actual receipt/native owner contract;
    unproved ACP context is UNKNOWN. Parent source identity/human planning is
    retained separately and never transplanted onto a different child node.
    Caller independently validates HTTP receipt/origin before accepting output.
    """
    from .adapters import _cards, _scope, _raw_href, _HEADINGS, _fold
    from ..multisource.sources import same_source_category
    from ..access.detector import detect_access_status
    from ..multisource.storage import read_sealed
    if not isinstance(rules,_ReviewedRules):
        rules=validate_mapping_rules(rules)
    path=Path(parent_html_path).resolve();stat=path.stat()
    page_url=row.get('page_url') or parent_source.get('actual_source_url')
    kind=parent_source.get('source_type');sid=parent_source.get('source_id')
    if row.get('source_type')!=kind or row.get('source_id')!=sid:
        raise ValueError('CATEGORY_OBSERVATION_SOURCE_MISMATCH')
    signature=json.dumps(parent_source,sort_keys=True,ensure_ascii=False,default=str)
    key=(str(path),parent_html_sha256,stat.st_size,stat.st_mtime_ns,page_url,signature,id(rules))
    cache={} if cache is None else cache
    if key not in cache:
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=parent_html_sha256:
            raise ValueError('CATEGORY_PARENT_HTML_HASH_MISMATCH')
        body=raw.decode('utf8');soup=BeautifulSoup(body,'lxml')
        headings=(soup.select("#zg-right-col [class*='card-title'] > h1") or soup.select("#zg h1[class*='style_zgH1Tag__']")
            or soup.select('#zg_banner_text, .zg-banner-text') or soup.select('h1') or soup.select('head > title'))
        heading=headings[0].get_text(' ',strip=True) if headings else None
        if (detect_access_status(200,body).value!='NORMAL' or kind not in _HEADINGS
                or source_type_from_url(page_url)!=kind
                or not same_source_category(page_url,parent_source.get('actual_source_url') or page_url)
                or not heading or not any(re.search(p,_fold(heading)) for p in _HEADINGS[kind])):
            raise ValueError('CATEGORY_PARENT_TYPE_OR_CONTEXT_UNVERIFIED')
        cards=[];contexts={}
        for slot,card in enumerate(_cards(soup)):
            context=_scope(card,page_url)
            badge=card.select_one('.zg-bdg-text, span.a-badge-text, [data-ranking-position]')
            rank_raw=badge.get_text(' ',strip=True) if badge else None
            match=re.search(r'#\s*(\d+)',rank_raw or '')
            rank=int(match.group(1)) if match else int(badge.get('data-ranking-position')) if badge and str(badge.get('data-ranking-position')).isdigit() else None
            wrapper=next((p for p in card.parents if p.get('data-card-metrics-id') or p.get('data-carousel-id') or p.get('data-category-url')),None)
            # A scope can only be promoted through its actual owner-local
            # navigation anchor/attribute. A synthetic ranking-group fragment
            # or a link elsewhere in the page is not a category identity.
            anchor=None;literal=None;label=None
            if wrapper is not None:
                anchor=next((a for a in wrapper.select('a[href]') if normalize_source_url(a['href'],base_url=page_url)==context and source_type_from_url(context)==kind),None)
                literal=anchor.get('href') if anchor is not None else wrapper.get('data-category-url')
                label=anchor.get_text(' ',strip=True) if anchor is not None else None
                h=wrapper.select_one('h2, h3')
                if h is not None:label=h.get_text(' ',strip=True)
            proof={'kind':'SERVER_CARD_OWNER_CATEGORY_CONTEXT','verification_status':'VERIFIED_CARD_OWNER_CONTEXT','ranking_context_url':context,'raw_context_href':literal,'category_heading_raw':label,
                'owner_id':wrapper.get('id') if wrapper else None,'owner_path':wrapper.get('data-acp-path') if wrapper else None,
                'card_raw_product_url':_raw_href(card),'representation_slot':slot,'rank_raw':rank_raw,'ranking_position':rank,
                'parent_page_url':page_url,'parent_html_path':str(path),'parent_html_sha256':parent_html_sha256}
            cards.append(proof)
            if literal and normalize_source_url(literal,base_url=page_url)==context:
                contexts.setdefault(context,[]).append(proof)
        cache[key]={'cards':cards,'contexts':contexts,'parent_mapping':map_category(parent_source,rules),'child_mappings':{}}
        while len(cache)>32:cache.pop(next(iter(cache)))
    saved=cache[key];context=row.get('rank_context')
    representation=row.get('representation')
    evidence=None
    if representation=='SERVER':
        if (str(Path(row.get('evidence_path') or '').resolve())!=str(path) or row.get('evidence_sha256')!=parent_html_sha256):
            raise ValueError('CATEGORY_SERVER_PARENT_REFERENCE_MISMATCH')
        slot=row.get('representation_slot')
        if type(slot) is not int or slot<0 or slot>=len(saved['cards']):
            raise ValueError('CATEGORY_SERVER_SLOT_MISMATCH')
        proof=saved['cards'][slot]
        if (proof['card_raw_product_url']!=row.get('raw_product_url') or proof['rank_raw']!=row.get('rank_raw')
                or proof['ranking_position']!=row.get('ranking_position') or proof['ranking_context_url']!=context):
            raise ValueError('CATEGORY_SERVER_CARD_CONTEXT_MISMATCH')
        evidence=proof
    elif re.fullmatch(r'ACP_\d+',representation or ''):
        if normalize_source_url(context)==normalize_source_url(page_url):
            # Grid ACP cannot inherit a category from a neighboring carousel.
            context=page_url
        else:
            reference=row.get('capture_receipt_reference') or {}
            receipt_path=Path(reference.get('path') or '')
            if not receipt_path.is_file() or hashlib.sha256(receipt_path.read_bytes()).hexdigest()!=reference.get('sha256'):
                raise ValueError('CATEGORY_ACP_CAPTURE_REFERENCE_UNVERIFIED')
            raw_receipt=json.loads(receipt_path.read_bytes())
            receipt=read_sealed(receipt_path) if 'payload' in raw_receipt else raw_receipt
            descriptor=receipt.get('native_acp_descriptor') or {}
            binding=receipt.get('ranking_context_binding') or descriptor.get('ranking_context_binding') or {}
            if (str(Path(receipt.get('html_path') or '').resolve())!=str(Path(row.get('evidence_path') or '').resolve())
                    or (receipt.get('html_sha256') or receipt.get('sha256'))!=row.get('evidence_sha256')):
                raise ValueError('CATEGORY_ACP_BODY_REFERENCE_MISMATCH')
            if (receipt.get('kind')!='ACP' or receipt.get('source_id')!=sid
                    or receipt.get('run_id')!=row.get('capture_run_id',row.get('run_id'))
                    or binding.get('source_id')!=sid or binding.get('ranking_context_url')!=context
                    or binding.get('page_evidence_sha256')!=parent_html_sha256):
                raise ValueError('CATEGORY_ACP_CAPTURE_CONTEXT_MISMATCH')
            if context not in saved['contexts']:
                raise ValueError('CATEGORY_ACP_OWNER_CONTEXT_NOT_IN_PARENT')
            from .authorization import validate_native_acp
            scope={'parser_profile':'V3','source_type':kind,'allow_native_acp':True,'page_urls':[page_url]}
            approval={'snapshot_contract_version':3,'run_id':receipt['run_id'],'sources':{sid:scope}}
            validate_native_acp(receipt.get('request_url'),descriptor,scope,approval,source_id=sid)
            owner=descriptor.get('native_carousel_owner',{})
            owned=[p for p in saved['contexts'][context] if p.get('owner_id')==owner.get('owner_id') and p.get('owner_path')==owner.get('owner_path')]
            if not owned:raise ValueError('CATEGORY_ACP_OWNER_NOT_IN_PARENT')
            evidence={**owned[0],'kind':'VERIFIED_NATIVE_ACP_OWNER_CATEGORY_CONTEXT','capture_receipt_reference':reference}
    else:
        raise ValueError('CATEGORY_REPRESENTATION_UNVERIFIED')
    parent_mapping=saved['parent_mapping']
    if normalize_source_url(context)==normalize_source_url(page_url):
        mapping=parent_mapping
    elif (not evidence or not evidence.get('raw_context_href') or not context or '#' in context
            or source_type_from_url(context)!=kind):
        mapping=map_category({'source_type':kind},rules)
        mapping['category_mapping_reason']='OWNER_LITERAL_CATEGORY_CONTEXT_UNVERIFIED'
    else:
        child={'actual_source_url':context,'source_type':kind,'amazon_category':evidence.get('category_heading_raw'),
            'category_path':[evidence['category_heading_raw']] if evidence.get('category_heading_raw') else [],
            'browse_node_id':browse_node_id(context)}
        if context not in saved['child_mappings']:
            saved['child_mappings'][context]=map_category(child,rules)
        mapping=saved['child_mappings'][context]
    mapping=deepcopy(mapping)
    evidence=deepcopy(evidence)
    return {**mapping,'category_mapping_parent_source_url':parent_source.get('actual_source_url'),
        'category_mapping_parent_page_url':page_url,'category_mapping_rank_context':context,
        'category_mapping_parent_prior':parent_mapping.get('original_research_category'),
        'category_mapping_context_evidence':evidence,
        'category_mapping_context_version':'card_owner_category_context_v3.1'}
