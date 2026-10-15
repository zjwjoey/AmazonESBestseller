"""DOM-backed discovery approval checks; no guessed URLs or automatic enabling."""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs

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


def verify_pagination_reference(reference, source):
    """One literal Next href in the same favorites result context."""
    path=Path(reference['evidence_path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=reference['evidence_sha256']:
        raise ValueError('V4_PAGINATION_EVIDENCE_SHA_MISMATCH')
    parent=reference['page_url']; url=reference['url']
    soup=BeautifulSoup(path.read_bytes(),'lxml')
    anchors=[a for a in soup.select('a.s-pagination-next[href], .a-pagination a[href]')
             if a['href']==reference['raw_href'] and a.get_text(' ',strip=True)==reference['label_raw']]
    if (not anchors or urljoin(parent,reference['raw_href'])!=url or not amazon_url(url)
            or source['source_type']!='CUSTOMER_FAVORITES'
            or source['source_subtype']!='CUSTOMER_FAVORITES_FILTERED_SEARCH'
            or parent!=source['actual_source_url']):
        raise ValueError('V4_PAGINATION_EXACT_NEXT_HREF_REQUIRED')
    pq=parse_qs(urlsplit(parent).query); q=parse_qs(urlsplit(url).query)
    if (urlsplit(url).path!='/s' or not pq.get('srs')
            or any(pq.get(k)!=q.get(k) for k in ('i','srs','rh','s','fs'))
            or q.get('page')!=[str(int((pq.get('page') or ['1'])[0])+1)]):
        raise ValueError('V4_PAGINATION_SAME_CONTEXT_REQUIRED')
    return reference


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
