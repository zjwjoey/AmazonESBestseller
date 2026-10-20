"""V4 discovery semantics; unvisited navigation never becomes approved supply."""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, urlsplit

SEEDS = [
    {'source_type': 'BEST_SELLERS', 'source_subtype': 'BESTSELLER_SALES_RANKING',
     'url': 'https://www.amazon.es/gp/bestsellers'},
    {'source_type': 'NEW_RELEASES', 'source_subtype': 'NEW_RELEASE_SALES_RANKING',
     'url': 'https://www.amazon.es/gp/new-releases'},
    {'source_type': 'DEALS', 'source_subtype': 'DEALS_ZONE', 'url': 'https://www.amazon.es/deals'},
    {'source_type': 'DEALS', 'source_subtype': 'LIGHTNING_DEAL_FILTER',
     'url': 'https://www.amazon.es/deals?bubble-id=deals-collection-lightning-deals'},
    {'source_type': 'CUSTOMER_FAVORITES', 'source_subtype': 'CUSTOMERS_MOST_LOVED_CURATED_ZONE',
     'url': 'https://www.amazon.es/gp/browse.html?node=205103589031&ref_=nav_em_nav_CML_0_2_24_1'},
    {'source_type': 'CUSTOMER_FAVORITES', 'source_subtype': 'CUSTOMER_FAVORITES_CATEGORY_BROWSE',
     'url': 'https://www.amazon.es/b?ie=UTF8&node=88626813031'},
    {'source_type': 'CUSTOMER_FAVORITES', 'source_subtype': 'CUSTOMER_FAVORITES_FILTERED_SEARCH',
     'url': 'https://www.amazon.es/s?i=kitchen&srs=88626813031&rh=n%3A88626813031&s=popularity-rank&fs=true&ref=lp_88626813031_sar'},
    {'source_type': 'AMAZON_HAUL', 'source_subtype': 'HAUL_LOW_PRICE_ZONE',
     'url': 'https://www.amazon.es/-/en/haul/store'},
]
DISABLED_SUPPLY = {k: 'SOURCE_PRODUCT_SUPPLY_UNVERIFIED_OR_UNAVAILABLE'
                   for k in ('MOVERS_SHAKERS', 'MOST_WISHED_FOR', 'MOST_GIFTED')}
RANKED = {'BEST_SELLERS', 'NEW_RELEASES'}
# Explicit root in the separate three-source task. Preserve the original eight
# diagnostic seeds and their strict acceptance contract unchanged.
ADDITIONAL_ROOTS = [{'source_type':'CUSTOMER_FAVORITES',
    'source_subtype':'CUSTOMERS_MOST_LOVED_CURATED_ZONE',
    'url':'https://www.amazon.es/gp/browse.html?node=205103589031'}]


def amazon_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and p.hostname in {'amazon.es', 'www.amazon.es'}
                and p.port in {None, 443} and not p.username and not p.password
                and not any(ord(c) <= 32 or c == '\\' for c in url))
    except (ValueError, TypeError):
        return False


def source_for_url(url, *, navigation_evidence=None, source_type=None, source_subtype=None):
    if not amazon_url(url):
        raise ValueError('V4_AMAZON_SOURCE_URL_REQUIRED')
    found = next((s for s in [*SEEDS, *ADDITIONAL_ROOTS] if s['url'] == url), None)
    if found is None:
        # A non-seed is an explicit reviewed DOM discovery, never guessed paths.
        if not (navigation_evidence and navigation_evidence.get('raw_href')
                and navigation_evidence.get('evidence_sha256')
                and navigation_evidence.get('page_url') and source_type and source_subtype):
            raise ValueError('V4_REAL_NAVIGATION_EVIDENCE_REQUIRED')
        found = {'source_type': source_type, 'source_subtype': source_subtype, 'url': url}
    if source_type and found['source_type'] != source_type:
        raise ValueError('V4_SOURCE_TYPE_MISMATCH')
    return {**found, 'actual_source_url': url, 'source_id': 'v4_' + hashlib.sha256(url.encode()).hexdigest()[:24],
            'parser_profile': 'V4', 'navigation_evidence': navigation_evidence,
            'source_status': 'UNVERIFIED', 'enabled': False, 'research_category': None,
            'category_path': []}


def node_id(url):
    return parse_qs(urlsplit(url).query).get('node', [None])[0]
