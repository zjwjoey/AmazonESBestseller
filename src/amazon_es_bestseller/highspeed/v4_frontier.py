"""Offline inventory of literal navigation in a fully reviewed T2 evidence pack.

This is a review queue, never an execution plan, source approval or supply proof.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
import uuid

from bs4 import BeautifulSoup

from ..multisource.storage import immutable_json
from .budgets import aware_time
from .v4_navigation import verify_discovered_source, verify_pagination_reference
from .v4_quality import verify_stage_reference
from .v4_sources import amazon_url, RANKED

VERSION = 'v4_saved_navigation_inventory.1'
_RANK_SELECTORS = ('#zg-left-col a[href], #zg_browseRoot a[href], '
                   '[aria-label="Árbol de navegación"] a[href], '
                   '[data-card-metrics-id^="p13n-zg-list-carousel"] a[href]')
_FAVORITE_SELECTORS = ('#s-refinements a[href], .dcl-iwt a[href], '
                       '[class*="apbBrowseSearchResultsFooter"] a[href], '
                       'a.s-pagination-next[href], .a-pagination a[rel="next"][href]')
_DEAL_SELECTORS = ('#nav-subnav a[href], [data-testid*="filter"] a[href], '
                   '[data-testid*="bubble"] a[href], [data-testid*="Filter"] a[href]')
_HAUL_SELECTORS = ('#nav-subnav.haul-subnav a[href], '
                   '[class*="_haul-cx-images-carousel_"] a[href], '
                   '[class*="_h-isw-image-link_container_"] a[href]')
_PRODUCT_ROUTE = re.compile(r'/(?:dp|gp/product|product-reviews|gp/aw/d)/', re.I)


def _excluded(anchor):
    """Navigation ownership excludes products, ads, history and global footer."""
    for node in (anchor, *anchor.parents):
        attrs = getattr(node, 'attrs', {})
        identity = str(attrs.get('id', '')).lower()
        component = str(attrs.get('data-component-type', '')).lower()
        classes = ' '.join(attrs.get('class', [])).lower()
        if (node.name == 'footer' or identity.startswith(('rhf', 'navfooter'))
                or attrs.get('data-ad-id') or attrs.get('data-asin')
                or component in {'sp-sponsored-result', 's-search-result'}
                or 'sponsored' in classes or 'sponsored' in identity
                or 'dcl-product' in classes or 'style_faceout__' in classes
                or attrs.get('data-testid') == 'product-card'):
            return True
    return False


def _selectors(kind):
    if kind in RANKED:
        return _RANK_SELECTORS
    return {'CUSTOMER_FAVORITES': _FAVORITE_SELECTORS, 'DEALS': _DEAL_SELECTORS,
            'AMAZON_HAUL': _HAUL_SELECTORS}.get(kind, 'this-selector-is-never-present')


def _root_alias(url, kind):
    if kind not in RANKED:
        return False
    root = '/gp/bestsellers' if kind == 'BEST_SELLERS' else '/gp/new-releases'
    path = urlsplit(url).path
    return path.rstrip('/') == root or path.startswith(root + '/ref=')


def _classification(reference, source, page, observed_urls):
    if reference['url'] in observed_urls:
        return 'ALREADY_OBSERVED', None
    if _root_alias(reference['url'], source['source_type']):
        return 'IGNORED_ROOT_ALIAS', None
    if (page.get('http_status') != 200 or page.get('access_status') != 'NORMAL'
            or page.get('status') not in {'VERIFIED_WITH_PRODUCTS', 'EXPLICIT_EMPTY'}):
        return 'UNVERIFIED_CONTEXT', 'PARENT_SOURCE_STATE_UNVERIFIED'
    try:
        if reference['kind'] == 'PAGINATION_LINK':
            verify_pagination_reference(reference, source, parent_page_url=page['page_url'])
            return 'PENDING_PAGINATION_REVIEW', None
        subtype = source['source_subtype']
        if source['source_type'] == 'CUSTOMER_FAVORITES' and urlsplit(reference['url']).path == '/s':
            subtype = 'CUSTOMER_FAVORITES_FILTERED_SEARCH'
        verify_discovered_source({**source, 'actual_source_url': reference['url'],
            'source_subtype': subtype, 'navigation_evidence': reference})
        return 'PENDING_SOURCE_REVIEW', None
    except ValueError as exc:
        return 'UNVERIFIED_CONTEXT', str(exc)


def extract_page_navigation(page, source, *, observed_urls=()):
    """Extract only literal owned anchors; independently check saved HTML bytes.

    Public for offline inspection and regressions. Only review_navigation seals a
    deliverable, after independent central T2 DOM and capacity revalidation.
    """
    if (page['source_id'] != source['source_id']
            or page['source_type'] != source['source_type']
            or page['source_subtype'] != source['source_subtype']
            or page['page_url'] not in source.get('page_urls', [source['actual_source_url']])):
        raise ValueError('V4_FRONTIER_PAGE_SOURCE_BINDING_MISMATCH')
    body = Path(page['evidence_path']).read_bytes()
    if hashlib.sha256(body).hexdigest() != page['evidence_sha256']:
        raise ValueError('V4_FRONTIER_HTML_SHA_MISMATCH')
    soup = BeautifulSoup(body, 'lxml')
    positions = {id(a): index for index, a in enumerate(soup.select('a[href]'))}
    result, excluded = [], Counter()
    for a in soup.select(_selectors(source['source_type'])):
        if _excluded(a):
            excluded['PRODUCT_AD_HISTORY_OR_FOOTER'] += 1
            continue
        raw = a['href']
        url = urljoin(page['page_url'], raw)
        if not amazon_url(url) or not raw or raw.startswith('#'):
            excluded['EXTERNAL_OR_NON_HTTP_OR_FRAGMENT'] += 1
            continue
        if _PRODUCT_ROUTE.search(urlsplit(url).path):
            excluded['PRODUCT_LINK'] += 1
            continue
        label = a.get_text(' ', strip=True)
        is_next = ('s-pagination-next' in a.get('class', []) or 'next' in a.get('rel', []))
        ref = {'page_url': page['page_url'], 'url': url, 'raw_href': raw,
            'label_raw': label, 'evidence_path': page['evidence_path'],
            'evidence_sha256': page['evidence_sha256'],
            'kind': 'PAGINATION_LINK' if is_next else 'NAVIGATION_LINK'}
        status, reason = _classification(ref, source, page, set(observed_urls))
        result.append({**ref, 'parent_source_id': source['source_id'],
            'parent_source_type': source['source_type'], 'parent_source_subtype': source['source_subtype'],
            'parent_status': page['status'], 'parent_completeness_status': page.get('completeness_status', 'UNKNOWN'),
            'observed_at': page['observed_at'], 'anchor_dom_index': positions[id(a)],
            'anchor_dom_sha256': hashlib.sha256(str(a).encode('utf8')).hexdigest(),
            'status': status, 'context_review_reason': reason, 'approved': False,
            'live_enabled': False, 'destination_product_supply': 'UNVERIFIED'})
    return result, dict(excluded)


def _inventory_from_verified(stage, reference):
    sources = {s['source_id']: s for s in stage['sources']}
    # Keep only the latest saved PAGE for an identical source and exact URL.
    latest = {}
    for page in stage['pages']:
        key = (page['source_id'], page['page_url'])
        if key not in latest or aware_time(page['observed_at']) > aware_time(latest[key]['observed_at']):
            latest[key] = page
    observed = {p['page_url'] for p in latest.values()}
    entries, exclusions = {}, Counter()
    parents = []
    for key in sorted(latest):
        page = latest[key]
        rows, omitted = extract_page_navigation(page, sources[page['source_id']], observed_urls=observed)
        exclusions.update(omitted)
        parents.append({k: page.get(k) for k in ('source_id', 'source_type', 'source_subtype', 'page_url',
            'status', 'completeness_status', 'observed_at', 'http_status', 'access_status',
            'evidence_path', 'evidence_sha256')})
        for row in rows:
            entry = entries.setdefault(row['url'], {'exact_url': row['url'], 'provenance': [],
                'approved': False, 'live_enabled': False, 'destination_product_supply': 'UNVERIFIED'})
            entry['provenance'].append(row)
    for entry in entries.values():
        statuses = {p['status'] for p in entry['provenance']}
        entry['provenance_statuses'] = sorted(statuses)
        # Conflicting contexts stay visible and cannot be silently promoted.
        entry['status'] = (next(iter(statuses)) if len(statuses) == 1 else
                           'ALREADY_OBSERVED' if 'ALREADY_OBSERVED' in statuses else 'UNVERIFIED_CONTEXT')
    inventory = sorted(entries.values(), key=lambda e: e['exact_url'])
    return {'schema_version': 1, 'inventory_version': VERSION, 'kind': 'OFFLINE_NAVIGATION_REVIEW_QUEUE',
        'created_at': datetime.now(timezone.utc).isoformat(), 'source_run_id': stage['run_id'],
        't2_reference': reference, 'T2_DOM_and_capacity_reverified': True,
        'approved': False, 'live_enabled': False, 'T3_execution_enabled': False,
        'new_network_requests': 0, 'destination_product_supply': 'UNVERIFIED',
        'strict_independent_buyable_skus_confirmed': False,
        'thirty_thousand_supply_demonstrated': False, 'capacity_estimate': None,
        'sample_goal_met': stage['capacity_audit']['sample_goal_shortfall'] == 0,
        'sample_goal': stage['capacity_audit']['sample_goal'],
        'sample_goal_shortfall': stage['capacity_audit']['sample_goal_shortfall'],
        'accepted_empty_source_urls': stage.get('accepted_empty_source_urls', []),
        'prior_stage_excluded_sources': 'Not reclassified by this inventory; original prerequisite evidence remains authoritative.',
        'parent_pages': parents, 'exact_url_count': len(inventory),
        'status_counts': dict(Counter(e['status'] for e in inventory)),
        'excluded_anchor_counts': dict(exclusions), 'entries': inventory}


def review_navigation(t2_reference, output):
    """Revalidate formal T2, then create an immutable disabled navigation queue."""
    stage = verify_stage_reference(t2_reference, 'T2')
    inventory = _inventory_from_verified(stage, t2_reference)
    directory = Path(output) / ('navigation_review_' + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    immutable_json(directory / 'navigation_inventory.json', inventory)
    pending = [e['exact_url'] for e in inventory['entries']
               if e['status'] in {'PENDING_SOURCE_REVIEW', 'PENDING_PAGINATION_REVIEW'}]
    # Exclusive creation preserves every unknown query token and parameter order.
    with (directory / 'pending_exact_urls.txt').open('x', encoding='utf8', newline='\n') as fh:
        fh.write('\n'.join(pending) + ('\n' if pending else ''))
    files = [{'name': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(directory.iterdir())]
    immutable_json(directory / 'manifest.json', {'schema_version': 1, 'inventory_version': VERSION,
        't2_reference': t2_reference, 'files': files, 'approved': False, 'live_enabled': False,
        'T3_execution_enabled': False, 'new_network_requests': 0})
    return {'directory': str(directory.resolve()), 'exact_url_count': inventory['exact_url_count'],
        'pending_exact_url_count': len(pending), 'status_counts': inventory['status_counts'],
        'new_network_requests': 0, 'approved': False, 'live_enabled': False, 'T3_execution_enabled': False}
