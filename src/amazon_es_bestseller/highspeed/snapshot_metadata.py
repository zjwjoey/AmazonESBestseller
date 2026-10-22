"""Versioned source context and optional identity hints for candidate snapshots.

No network, product identity promotion, title-based category guesses or changes
to raw evidence. A thematic source label is distinct from an Amazon department.
"""
import hashlib
import re
from pathlib import Path
from functools import lru_cache
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, urljoin, urlsplit

CONTRACT = 'SOURCE_CATEGORY_IDENTITY_V1'
_ASIN = re.compile(r'^[A-Z0-9]{10}$')
_ROUTE = re.compile(r'/(?:dp|gp/product|gp/aw/d)/([^/?#]+)', re.I)


@lru_cache(maxsize=8)
def _reviewed_context_node(body,selector,node_sha,statement):
    soup=BeautifulSoup(body,'lxml')
    matches=[n for n in soup.select(selector) if hashlib.sha256(str(n).encode('utf8')).hexdigest()==node_sha]
    return len(matches)==1 and matches[0].get_text(' ',strip=True)==statement


def _label(node):
    # Hidden "(Current)" descendants are accessibility annotations, not labels.
    return ' '.join(str(s).strip() for s in node.find_all(string=True, recursive=False)
                    if str(s).strip())


def _path(url):
    return urlsplit(url).path.split('/ref=')[0].rstrip('/')


def _proof(node, page_url, sha):
    return {'page_url': page_url, 'evidence_sha256': sha,
            'node_sha256': hashlib.sha256(str(node).encode('utf8')).hexdigest(),
            'label_raw': _label(node) or node.get_text(' ', strip=True)}


def source_category(soup, source, page_url, sha, card=None):
    result = {'source_category_l1_raw': None, 'source_category_status': 'UNKNOWN',
              'amazon_category_path': [], 'amazon_category_l1': None,
              'amazon_category_l2': None, 'amazon_category_l3': None,
              'amazon_category_leaf': None, 'category_evidence': []}
    kind = source['source_type']
    if kind in {'BEST_SELLERS', 'NEW_RELEASES'}:
        root = '/gp/bestsellers' if kind == 'BEST_SELLERS' else '/gp/new-releases'
        nav = soup.select_one('[aria-label="Árbol de navegación"], #zg_browseRoot, #zg-left-col')
        if nav is None:
            return result
        selected = nav.select('[aria-current="page"]')
        up = nav.select('a[href*="unv_"]')
        if len(selected) == 1 and any(_path(urljoin(page_url, a['href'])) == root for a in up):
            chain = [a for a in up if _path(urljoin(page_url, a['href'])) != root]
            nodes = chain + selected
            labels = [_label(n) or n.get_text(' ', strip=True) for n in nodes]
            if all(labels):
                result.update(source_category_l1_raw=labels[0], source_category_status='AMAZON_NAVIGATION',
                    amazon_category_path=labels, amazon_category_l1=labels[0],
                    amazon_category_l2=labels[1] if len(labels)>1 else None,
                    amazon_category_l3=labels[2] if len(labels)>2 else None,
                    amazon_category_leaf=labels[-1],
                    category_evidence=[_proof(n,page_url,sha) for n in nodes])
        elif _path(page_url) == root and card is not None:
            # Mixed root pages: bind each card's own carousel heading to an
            # exact department anchor in the root navigation, not the first h2.
            section = next((p for p in card.parents if
                str(p.get('data-card-metrics-id','')).startswith('p13n-zg-list-carousel')), None)
            if section is not None:
                anchors=[a for heading in section.select('h2') for a in heading.parent.select('a[href]')]
                for anchor in anchors:
                    target = _path(urljoin(page_url, anchor['href']))
                    matches = [a for a in nav.select('a[href]')
                               if _path(urljoin(page_url,a['href'])) == target and target != root]
                    if len(matches) == 1:
                        label = matches[0].get_text(' ',strip=True)
                        result.update(source_category_l1_raw=label,source_category_status='AMAZON_ROOT_SECTION',
                            amazon_category_path=[label],amazon_category_l1=label,amazon_category_leaf=label,
                            category_evidence=[_proof(anchor,page_url,sha),_proof(matches[0],page_url,sha)])
                        break
    else:
        # A Favorites theme supplies useful raw context, but does not prove
        # that the theme is a canonical top-level Amazon department.
        current=source
        for _ in range(50):
            ref=current.get('navigation_evidence') or {}
            parent=current.get('navigation_parent') or {}
            if (ref.get('relationship')=='THEME_LINK' and ref.get('label_raw')
                    and ref.get('evidence_sha256') and ref.get('raw_href')
                    and _path(parent.get('actual_source_url',''))=='/gp/browse.html'
                    and parse_qs(urlsplit(parent.get('actual_source_url','')).query).get('node')==['205103589031']):
                result.update(source_category_l1_raw=ref['label_raw'],source_category_status='FAVORITES_THEME_ONLY',
                              category_evidence=[dict(ref)])
                break
            if not parent:break
            current=parent
    if result['source_category_l1_raw'] is None and source.get('collection_category_evidence'):
        # An exact reviewed source context may be used when a favorites search
        # template omits the department heading. It remains source context,
        # never an invented canonical Amazon hierarchy.
        evidence=source['collection_category_evidence']
        p=Path(evidence['path']);body=p.read_bytes()
        if hashlib.sha256(body).hexdigest()!=evidence['sha256']:
            raise ValueError('REVIEWED_CATEGORY_EVIDENCE_SHA_MISMATCH')
        if not _reviewed_context_node(body,evidence['selector'],evidence['node_sha256'],evidence['statement_raw']):
            raise ValueError('REVIEWED_CATEGORY_CONTEXT_NODE_MISMATCH')
        result.update(source_category_l1_raw=evidence['category_label_raw'],source_category_status='REVIEWED_SOURCE_CONTEXT',
                      category_evidence=[dict(evidence)])
    return result


def annotate_observation(row, soup, source, page_url, sha, card=None):
    row.update(source_category(soup,source,page_url,sha,card))
    row['snapshot_metadata_contract']=CONTRACT
    row['snapshot_eligible']=True
    row['snapshot_exclusion_reasons']=[]
    if not row['source_category_l1_raw'] or not row['category_evidence']:
        row['snapshot_eligible']=False
        row['snapshot_exclusion_reasons'].append('CATEGORY_EVIDENCE_MISSING')
    raw=[]
    if card is not None:
        raw=[str(n.get('data-asin')) for n in [card,*card.select('[data-asin]')] if n.get('data-asin')]
    hints=sorted({s.upper() for s in raw if _ASIN.fullmatch(s.upper())})
    match=_ROUTE.search(row.get('raw_product_url') or '')
    url_hint=match.group(1).upper() if match and _ASIN.fullmatch(match.group(1).upper()) else None
    conflict=len(hints)>1 or bool(hints and url_hint and hints[0]!=url_hint)
    row.update(asin_card_raw=raw,asin_card_hints=hints,asin_url_hint=url_hint,
               identity_status='CONFLICT' if conflict else 'UNCONFIRMED',
               identity_observation_status='CONFLICT' if conflict else 'HINT_ONLY' if hints or url_hint else 'ABSENT',
               strict_buyable_confirmed=False)
    if conflict:
        row['snapshot_eligible']=False
        row['snapshot_exclusion_reasons'].append('CARD_URL_ASIN_CONFLICT')
        row['binding_doubts']=list(dict.fromkeys([*row.get('binding_doubts',[]),'CARD_URL_ASIN_CONFLICT']))
    return row
