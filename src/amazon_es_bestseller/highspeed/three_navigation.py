"""Owned, literal three-source navigation. Evidence graphs, never URL synthesis.

Unknown hierarchy and destination supply stay unknown. This module never fetches
anything, approves discovered destinations, or treats facets as categories.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qs
from bs4 import BeautifulSoup
from .v4_frontier import _excluded
from .v4_sources import amazon_url, RANKED
from .v4_navigation import verify_pagination_reference

FAMILIES = ('BEST_SELLERS', 'NEW_RELEASES', 'CUSTOMER_FAVORITES')
FAVORITES_ROOT = 'https://www.amazon.es/gp/browse.html?node=205103589031'
CONTRACT = 'V4_THREE_SOURCE_5001'


def category_key(url):
    """Category grouping only, not an executable URL or a product identity."""
    p = urlsplit(url)
    if p.path.startswith(('/gp/bestsellers', '/gp/new-releases')):
        path = re.sub(r'/ref=zg_(?:bs|bsnr)_[A-Za-z0-9_-]+/?$', '', p.path).rstrip('/')
        # Verified navigation-only paging/encoding tokens; every unknown byte
        # and all non-ranking URLs remain significant.
        tokens = [t for t in p.query.split('&') if t and t not in {'ie=UTF8','pg=1','pg=2'}]
        return urlunsplit((p.scheme, p.netloc, path, '&'.join(tokens), p.fragment))
    return url


def _body(page):
    body = Path(page['evidence_path']).read_bytes()
    if hashlib.sha256(body).hexdigest() != page['evidence_sha256']:
        raise ValueError('THREE_NAV_HTML_SHA_MISMATCH')
    return BeautifulSoup(body, 'lxml')


def _reference(a, soup, page, source, relationship):
    url = urljoin(page['page_url'], a['href'])
    parts = urlsplit(url)
    ids = re.findall(r'(?<=/)\d+(?=/|$)', parts.path)
    query = parse_qs(parts.query)
    node = (query.get('node') or query.get('srs') or ids[-1:] or [None])[0]
    all_anchors = soup.select('a[href]')
    return {'url': url, 'raw_href': a['href'], 'label_raw': a.get_text(' ', strip=True),
        'page_url': page['page_url'], 'parent_source_id': source['source_id'],
        'parent_source_type': source['source_type'], 'parent_source_subtype': source['source_subtype'],
        'evidence_path': page['evidence_path'], 'evidence_sha256': page['evidence_sha256'],
        'anchor_dom_index': next(i for i, x in enumerate(all_anchors) if x is a),
        'anchor_dom_sha256': hashlib.sha256(str(a).encode('utf8')).hexdigest(),
        'observed_at': page['observed_at'], 'parent_status': page['status'],
        'parent_http_status': page.get('http_status'), 'parent_access_status': page.get('access_status'),
        'relationship': relationship, 'browse_node_id': node,
        'status': 'PENDING_REVIEW' if page.get('http_status') == 200 and page.get('access_status') == 'NORMAL'
            and page['status'] in {'VERIFIED_WITH_PRODUCTS','NAVIGATION_ONLY','EXPLICIT_EMPTY'} else 'UNVERIFIED',
        'approved': False, 'destination_product_supply': 'UNVERIFIED'}


def discover_navigation(page, source):
    if (source['source_type'] not in FAMILIES or page['source_id'] != source['source_id']
            or page['source_type'] != source['source_type']
            or page['page_url'] not in source['page_urls']):
        raise ValueError('THREE_NAV_EXACT_SOURCE_REQUIRED')
    soup = _body(page)
    selectors = ('#zg-left-col a[href], #zg_browseRoot a[href], [aria-label="Árbol de navegación"] a[href], '
        '[data-card-metrics-id^="p13n-zg-list-carousel"] a[href], .a-pagination .a-last a[href]')
    if source['source_type'] == 'CUSTOMER_FAVORITES':
        selectors = '.dcl-iwt a[href], #s-refinements a[href], [class*="apbBrowseSearchResultsFooter"] a[href], a.s-pagination-next[href]'
    result = []
    selected = soup.select('#zg-left-col [aria-current="page"], #zg_browseRoot [aria-current="page"], '
                           '[aria-label="Árbol de navegación"] [aria-current="page"]')
    current_ul = next((n.find_parent('ul') for n in selected if n.find_parent('ul')), None)
    child_uls = []
    if current_ul:
        for li in current_ul.find_all('li', recursive=False):
            child_uls.extend(li.select('ul'))
    for a in soup.select(selectors):
        if _excluded(a) or not amazon_url(urljoin(page['page_url'], a['href'])):
            continue
        url = urljoin(page['page_url'], a['href']); parsed = urlsplit(url)
        if re.search(r'/(dp|gp/product|gp/aw/d|product-reviews)/', parsed.path):
            continue
        if not a.get_text(' ', strip=True):
            continue
        if 's-pagination-next' in a.get('class', []) or a.find_parent(class_='a-last'):
            relationship = 'NEXT_PAGE'
        elif source['source_type'] in RANKED:
            prefix = '/gp/bestsellers' if source['source_type'] == 'BEST_SELLERS' else '/gp/new-releases'
            if not (parsed.path == prefix or parsed.path.startswith(prefix + '/')):
                continue
            relationship = 'CHILD_CATEGORY' if any(u in a.parents for u in child_uls) else 'NAVIGATION_LINK'
        elif a.find_parent(class_='dcl-iwt'):
            relationship = 'THEME_LINK'
        elif a.find_parent(id='s-refinements'):
            relationship = 'FACET_LINK'
            # Search facets must retain the favorites collection identifier.
            parent_q = parse_qs(urlsplit(page['page_url']).query)
            node = (parent_q.get('srs') or parent_q.get('node') or [None])[0]
            if parsed.path != '/s' or not node or parse_qs(parsed.query).get('srs') != [node]:
                continue
        else:
            relationship = 'RESULTS_ENTRY'
        ref = _reference(a, soup, page, source, relationship)
        if relationship == 'NEXT_PAGE':
            try: verify_next(ref, source, parent_page_url=page['page_url'])
            except ValueError: ref['status'] = 'UNVERIFIED'
        result.append(ref)
    return result


def _verified_anchor(reference, source):
    if (reference['parent_source_id'] != source['source_id']
            or reference['parent_source_type'] != source['source_type']
            or reference['page_url'] not in source['page_urls'] or not amazon_url(reference['url'])):
        raise ValueError('THREE_NAV_PARENT_BINDING_REQUIRED')
    soup = _body(reference)
    anchors = soup.select('a[href]'); index = reference['anchor_dom_index']
    if type(index) is not int or not 0 <= index < len(anchors):
        raise ValueError('THREE_NAV_DOM_POSITION_REQUIRED')
    a = anchors[index]
    if (a['href'] != reference['raw_href'] or a.get_text(' ', strip=True) != reference['label_raw']
            or hashlib.sha256(str(a).encode('utf8')).hexdigest() != reference['anchor_dom_sha256']
            or urljoin(reference['page_url'], a['href']) != reference['url'] or _excluded(a)):
        raise ValueError('THREE_NAV_EXACT_OWNED_ANCHOR_REQUIRED')
    return soup, a


def verify_navigation_reference(reference, source):
    _verified_anchor(reference, source)
    page = {'source_id': source['source_id'], 'source_type': source['source_type'],
        'source_subtype': source['source_subtype'], 'page_url': reference['page_url'],
        'evidence_path': reference['evidence_path'], 'evidence_sha256': reference['evidence_sha256'],
        'observed_at': reference['observed_at'], 'http_status': reference['parent_http_status'],
        'access_status': reference['parent_access_status'], 'status': reference['parent_status']}
    if reference not in discover_navigation(page, source) or reference['status'] != 'PENDING_REVIEW':
        raise ValueError('THREE_NAV_OWNERSHIP_OR_PARENT_ACCESS_UNVERIFIED')
    return reference


def verify_next(reference, source, *, parent_page_url):
    # During discovery this check is pure and has no recursive rediscovery.
    _, a = _verified_anchor(reference, source)
    if reference['page_url'] != parent_page_url:
        raise ValueError('THREE_NEXT_EXACT_PARENT_REQUIRED')
    if source['source_type'] == 'CUSTOMER_FAVORITES':
        return verify_pagination_reference(reference, source, parent_page_url=parent_page_url)
    if source['source_type'] not in RANKED or not a.find_parent(class_='a-last'):
        raise ValueError('THREE_RANKED_NEXT_COMPONENT_REQUIRED')
    def context(url):
        p = urlsplit(url); pairs = {}; number = 1
        for token in p.query.split('&') if p.query else []:
            key, sep, value = token.partition('=')
            if not sep or key in pairs: raise ValueError('THREE_RANKED_PAGINATION_QUERY_UNVERIFIED')
            pairs[key] = value
        if 'pg' in pairs:
            value = pairs.pop('pg')
            if not value.isascii() or not value.isdigit() or value.startswith('0'):
                raise ValueError('THREE_RANKED_PAGE_NUMBER_INVALID')
            number = int(value)
        # Actual saved Next adds ie=UTF8; other encoding/filter bytes stay exact.
        if pairs.get('ie') == 'UTF8': pairs.pop('ie')
        path = re.sub(r'/ref=zg_(?:bs|bsnr)_[A-Za-z0-9_-]+$', '', p.path)
        return (p.scheme,p.netloc,path,p.fragment,pairs), number
    root, _ = context(source['actual_source_url']); parent, n = context(parent_page_url); dest, m = context(reference['url'])
    if root != parent or parent != dest or m != n + 1:
        raise ValueError('THREE_RANKED_CONSECUTIVE_SAME_CATEGORY_REQUIRED')
    return reference


def verify_chain(source):
    root = source['actual_source_url']; proofs = source.get('pagination_evidence', [])
    if source['page_urls'] != [root, *[p['url'] for p in proofs]] or len(set(source['page_urls'])) != len(source['page_urls']):
        raise ValueError('THREE_EXACT_ORDERED_PAGE_CHAIN_REQUIRED')
    parent = root
    for proof in proofs:
        verify_navigation_reference(proof, source)
        verify_next(proof, source, parent_page_url=parent)
        parent = proof['url']
    return source


def build_navigation_tree(pages, sources):
    by_id = {s['source_id']:s for s in sources}; nodes = {}; edges = []; pending = []
    def node(url, family, label=None, browse=None):
        key = family + '|' + category_key(url)
        n = nodes.setdefault(key, {'node_id':key, 'source_type':family,'actual_urls':[], 'name_es_raw':[],
            'browse_node_id':browse, 'parent_ids':[], 'paths':[], 'visit_status':'UNVISITED',
            'product_observations':None,'ranking_range':None,'page_completeness':'UNVERIFIED',
            'leaf_confirmed':False,'approved':False})
        if url not in n['actual_urls']:n['actual_urls'].append(url)
        if label and label not in n['name_es_raw']:n['name_es_raw'].append(label)
        return key
    for p in pages:
        s = by_id[p['source_id']]
        if s['source_type'] not in FAMILIES:continue
        root = node(p['page_url'], s['source_type'])
        nodes[root]['visit_status'] = p['status']; nodes[root]['page_completeness'] = p.get('completeness_status','UNKNOWN')
        nodes[root]['product_observations'] = p.get('actual_card_count')
        for r in discover_navigation(p,s):
            child = node(r['url'],s['source_type'],r['label_raw'],r['browse_node_id'])
            edges.append({'parent_context_id':root,'destination_id':child,'relationship':r['relationship'],'evidence':r})
            if r['relationship'] == 'CHILD_CATEGORY' and child != root:
                if root not in nodes[child]['parent_ids']:nodes[child]['parent_ids'].append(root)
            pending.append(r)
    # Paths reflect proven taxonomy edges only; multiple parents stay visible.
    def paths(key, visited):
        if key in visited:return []
        parents = nodes[key]['parent_ids']
        if not parents:return [[key]]
        return [p+[key] for parent in parents for p in paths(parent,visited|{key})]
    for key,n in nodes.items():n['paths'] = paths(key,set())
    return {'schema_version':1,'contract':CONTRACT,'nodes':list(nodes.values()),'edges':edges,
        'pending_navigation':pending,'global_tree_complete':False,'new_network_requests':0,
        'note':'Saved known scope only. No hierarchy inferred from URL depth; themes/facets/Next are separate edges.'}
