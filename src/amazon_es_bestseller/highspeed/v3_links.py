"""Explicit V3 lexical URL normalization; V2 and historical stores untouched.

Only verified-format Amazon navigation tracking suffixes are removable. Unknown
path/query/fragment bytes stay opaque. Canonical equality proves a lexical alias,
not independent purchasable SKU identity. Coarse route groups are review only.
"""
from __future__ import annotations
import re
from urllib.parse import unquote_plus,urlsplit,urlunsplit
from .links import normalize_product_url,candidate_id

NORMALIZER_VERSION = "link_url_v3.1.lexical_tracking"
_TRACKING=frozenset({'ref','ref_','tag','linkCode','camp','creative','ascsubtag',
    'pd_rd_w','pd_rd_wg','pd_rd_r','pf_rd_p','pf_rd_r'})
_PRESERVED=frozenset({'psc','th','color','size','variation','variant','variationId',
    'smid','seller','language','quantity','condition','encoding','ie'})
_ROUTE=re.compile(r'/(?:dp|gp/product|gp/aw/d|product)/([^/?#]+)(?=/|$)',re.I)
_REF_SUFFIX=re.compile(r'/ref=(zg_(?:bs|bsnr|movers|mw|mg|most_wished_for|most_gifted)_[A-Za-z0-9_.=-]+)(?:/(\d{3}-\d{7}-\d{7}))?/?$')
_SESSION_SUFFIX=re.compile(r'/(\d{3}-\d{7}-\d{7})/?$')
_CONTENT_ID=re.compile(r'^amzn1\.sym\.[0-9a-fA-F-]{36}$')


def normalize_product_url_v3(raw_url,base_url="https://www.amazon.es"):
    result=normalize_product_url(raw_url,base_url)
    result.update(normalizer_version=NORMALIZER_VERSION,normalization_version=NORMALIZER_VERSION,
                  normalization_evidence=[],unknown_query_parameters=[],preserved_identity_parameters=[],
                  candidate_id=None,dedup_status='INVALID_PRODUCT_LINK',unreviewed_path_suffix=None)
    if not result['is_valid']:
        return result
    parts=urlsplit(result['absolute_product_url'])
    route=_ROUTE.search(parts.path)
    evidence=[]
    path=parts.path
    if route:
        suffix=_REF_SUFFIX.search(path)
        if suffix and suffix.start()>=route.end():
            evidence.append({'kind':'EXPLICIT_AMAZON_RANK_NAVIGATION_TRACKING_SUFFIX',
                'raw_text':suffix.group(0),'path_offset':suffix.start(),'rule_version':NORMALIZER_VERSION})
            path=path[:suffix.start()]
        else:
            session=_SESSION_SUFFIX.search(path)
            # A bare session token is removable only directly after an opaque
            # product route target; arbitrary nested variant paths stay intact.
            if session and session.start()==route.end():
                evidence.append({'kind':'EXPLICIT_AMAZON_SESSION_SUFFIX','raw_text':session.group(0),
                    'path_offset':session.start(),'rule_version':NORMALIZER_VERSION})
                path=path[:session.start()]
    kept=[];unknown=[];identity=[]
    for token in parts.query.split('&') if parts.query else []:
        raw_key,_,value=token.partition('=')
        key=unquote_plus(raw_key)
        tracking=(key in _TRACKING or key=='content-id' and bool(_CONTENT_ID.fullmatch(value))
                  or key=='pd_rd_i' and route is not None and value==route.group(1))
        if tracking:
            evidence.append({'kind':'EXPLICIT_TRACKING_QUERY_PARAMETER','raw_text':token,
                'parameter':key,'rule_version':NORMALIZER_VERSION})
            continue
        kept.append(token)
        if key in _PRESERVED:
            identity.append(key)
        elif key not in unknown:
            unknown.append(key)
    path_suffix=path[route.end():] if route and len(path)>route.end() else None
    normalized=urlunsplit((parts.scheme,'www.amazon.es',path,'&'.join(kept),parts.fragment))
    result.update(normalized_product_url=normalized,candidate_id=candidate_id(normalized),
                  normalization_evidence=evidence,unknown_query_parameters=unknown,
                  preserved_identity_parameters=identity,
                  dedup_status='POSSIBLE_DUPLICATE' if unknown or path_suffix or parts.fragment or result.get('reason') else 'UNIQUE_LINK',
                  unreviewed_path_suffix=path_suffix,
                  url_identity_status='IDENTITY_UNCONFIRMED')
    # This field remains explicitly coarse and must never be used to collapse
    # V3 canonical URLs with different variant or unknown query bytes.
    return result
