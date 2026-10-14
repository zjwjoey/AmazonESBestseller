"""Offline reviewed source-category mapping; no product exclusion or network.

A namespace rule is an explicitly reviewed department-subtree policy, not a
heuristic inferred from a URL slug. Every policy must cite an actual root
navigation anchor and exact saved HTML hash. Mixed departments remain unknown.
"""
from __future__ import annotations
import hashlib
import json
from functools import lru_cache
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
    prior=source.get('research_category')
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
            'category_mapping_candidates':categories,'original_research_category':prior}
