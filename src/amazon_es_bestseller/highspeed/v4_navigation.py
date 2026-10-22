"""DOM-backed discovery approval checks; no guessed URLs or automatic enabling."""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs, unquote_plus

from bs4 import BeautifulSoup

from .v4_sources import amazon_url, RANKED


def exact_query_token_reordering(left,right):
    """Only reorder unique, byte-identical query tokens; never drop parameters."""
    a,b=urlsplit(left),urlsplit(right)
    if (not amazon_url(left) or not amazon_url(right) or
            (a.scheme,a.netloc,a.path,a.fragment)!=(b.scheme,b.netloc,b.path,b.fragment)):
        return False
    x=a.query.split('&');y=b.query.split('&')
    if any(not token or '=' not in token for token in [*x,*y]):return left==right
    if len({t.split('=',1)[0] for t in x})!=len(x) or len({t.split('=',1)[0] for t in y})!=len(y):
        return False
    return sorted(x)==sorted(y)


def _pagination_context(url):
    """Keep all unknown filter bytes; only observed navigation tokens may vary."""
    parsed = urlsplit(url)
    if not amazon_url(url) or parsed.path != '/s':
        raise ValueError('V4_PAGINATION_SAME_CONTEXT_REQUIRED')
    query = {}
    for token in parsed.query.split('&'):
        key, separator, value = token.partition('=')
        if (not separator or not key or key != unquote_plus(key) or key in query):
            raise ValueError('V4_PAGINATION_SAME_CONTEXT_REQUIRED')
        query[key] = value
    page = query.get('page', '1')
    if not page.isascii() or not page.isdigit() or page.startswith('0'):
        raise ValueError('V4_PAGINATION_SAME_CONTEXT_REQUIRED')
    if not query.get('srs'):
        raise ValueError('V4_PAGINATION_SAME_CONTEXT_REQUIRED')
    # These are the navigation-only query tokens observed in saved Favorites
    # Next links. Do not expand this set to unknown filters or search terms.
    context = {k: v for k, v in query.items() if k not in {'page', 'ref', 'ref_', 'qid', 'xpid'}}
    return (parsed.scheme, parsed.netloc, parsed.path, parsed.fragment, context), int(page)


def verify_pagination_reference(reference, source, *, parent_page_url=None):
    """One literal Next href from the declared parent in the same source context.

    Without an explicit parent this retains the original first-hop contract.
    Plans must additionally validate the complete ordered evidence chain.
    """
    path=Path(reference['evidence_path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=reference['evidence_sha256']:
        raise ValueError('V4_PAGINATION_EVIDENCE_SHA_MISMATCH')
    parent=reference['page_url']; url=reference['url']
    soup=BeautifulSoup(path.read_bytes(),'lxml')
    anchors=[a for a in soup.select('a.s-pagination-next[href], .a-pagination a[href]')
             if a['href']==reference['raw_href'] and a.get_text(' ',strip=True)==reference['label_raw']]
    expected_parent = source['actual_source_url'] if parent_page_url is None else parent_page_url
    if (not anchors or urljoin(parent,reference['raw_href'])!=url or not amazon_url(url)
            or source['source_type']!='CUSTOMER_FAVORITES'
            or source['source_subtype']!='CUSTOMER_FAVORITES_FILTERED_SEARCH'
            or parent!=expected_parent
            or expected_parent not in source.get('page_urls', [source['actual_source_url']])):
        raise ValueError('V4_PAGINATION_EXACT_NEXT_HREF_REQUIRED')
    root_context, _ = _pagination_context(source['actual_source_url'])
    parent_context, parent_number = _pagination_context(parent)
    next_context, next_number = _pagination_context(url)
    if root_context != parent_context or parent_context != next_context or next_number != parent_number + 1:
        raise ValueError('V4_PAGINATION_SAME_CONTEXT_REQUIRED')
    return reference


def verify_pagination_chain(source):
    """Verify every consecutive literal Next hop; this never grants LIVE scope."""
    root = source['actual_source_url']
    references = source.get('pagination_evidence', [])
    if (not isinstance(references, list) or
            source.get('page_urls') != [root, *[r['url'] for r in references]]):
        raise ValueError('V4_EXACT_EVIDENCED_SOURCE_PAGES_REQUIRED')
    parent = root
    visited = {root}
    for reference in references:
        if reference.get('page_url') != parent or reference.get('url') in visited:
            raise ValueError('V4_ORDERED_PAGINATION_CHAIN_REQUIRED')
        verify_pagination_reference(reference, source, parent_page_url=parent)
        parent = reference['url']
        visited.add(parent)
    return source


def verify_discovered_source(source):
    e = source.get('navigation_evidence') or {}
    path = Path(e.get('evidence_path') or '__missing__')
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != e.get('evidence_sha256'):
        raise ValueError('V4_NAVIGATION_DOM_EVIDENCE_REQUIRED')
    url = source['actual_source_url']; parent = e.get('page_url', '')
    if not amazon_url(parent) or not amazon_url(url) or urljoin(parent, e.get('raw_href', '')) != url:
        raise ValueError('V4_EXACT_DISCOVERED_HREF_REQUIRED')
    soup = BeautifulSoup(path.read_bytes(), 'lxml')
    matches = [a for a in soup.select('a[href]') if a['href'] == e.get('raw_href')]
    if not matches or not any(a.get_text(' ', strip=True) == e.get('label_raw') for a in matches):
        raise ValueError('V4_NAVIGATION_NOT_PRESENT_IN_DOM')
    p = urlsplit(url); q = parse_qs(p.query)
    kind = source['source_type']; subtype = source['source_subtype']
    if kind in RANKED:
        root = '/gp/bestsellers/' if kind == 'BEST_SELLERS' else '/gp/new-releases/'
        valid = p.path.startswith(root) and urlsplit(parent).path.startswith(root.rstrip('/'))
    elif kind == 'CUSTOMER_FAVORITES':
        # A real theme navigation link is not enough to authorize an arbitrary
        # search. Retain the evidenced favorites node on result pagination.
        pq = parse_qs(urlsplit(parent).query)
        node = (pq.get('srs') or pq.get('node') or [None])[0]
        valid = (subtype == 'CUSTOMER_FAVORITES_FILTERED_SEARCH' and p.path == '/s'
                 and bool(node) and q.get('srs') == [node])
    elif kind == 'AMAZON_HAUL':
        valid = subtype == 'HAUL_LOW_PRICE_ZONE' and (q.get('ishaul') == ['1'] or q.get('i') == ['bazaar'])
    elif kind == 'DEALS':
        valid = p.path == '/deals' and urlsplit(parent).path == '/deals' and subtype in {'DEALS_ZONE', 'LIGHTNING_DEAL_FILTER'}
    else:
        valid = False
    if not valid:
        raise ValueError('V4_DISCOVERED_SOURCE_CONTEXT_UNVERIFIED')
    return source
