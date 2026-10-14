"""DOM-backed discovery approval checks; no guessed URLs or automatic enabling."""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs

from bs4 import BeautifulSoup

from .v4_sources import amazon_url, RANKED


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
