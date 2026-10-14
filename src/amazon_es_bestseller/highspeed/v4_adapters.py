"""Separate DOM card adapters sharing V3 URL, ranking and access primitives.

Pure offline parsing. A rendered DOM is not an HTTP receipt or capacity proof.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status
from .v3_links import normalize_product_url_v3
from .v4_sources import RANKED, amazon_url, node_id

VERSION = 'multisource_v4.1.card_bound'
SELECTORS = {
    'DEALS_ZONE': '[data-testid="product-card"][data-asin][data-deal-id]',
    'LIGHTNING_DEAL_FILTER': '[data-testid="product-card"][data-asin][data-deal-id]',
    'CUSTOMERS_MOST_LOVED_CURATED_ZONE': 'div.dcl-product',
    'CUSTOMER_FAVORITES_CATEGORY_BROWSE': '[data-cy="asin-faceout-container"]',
    'CUSTOMER_FAVORITES_FILTERED_SEARCH': '[data-component-type="s-search-result"][data-asin]',
    'HAUL_LOW_PRICE_ZONE': 'div[data-asin][class*="_haul-cx-infinite-scroll-body_style_faceout__"]',
}
_RATING = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:de|out of)\s*5\s*(?:estrellas|stars)', re.I)
_COUNT = re.compile(r'([\d][\d. ,\u00a0\u202f]*)\s*(?:calificaciones|valoraciones|ratings)\b', re.I)
_MONTH = re.compile(r'(\d+(?:[.,]\d+)?(?:[. ]\d{3})*)\s*(mil|k)?\+\s*comprados? el mes pasado', re.I)
_MONEY = re.compile(r'(?<![\d.,])(\d+(?:\.\d{3})*(?:,\d{1,2})?)\s*€')


def _text(node):
    return node.get_text(' ', strip=True) if node is not None else None


def _sha(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()


def _eligible(node):
    return not any(p.get('id') == 'rhf' or p.get('data-ad-id')
                   or p.get('data-component-type') == 'sp-sponsored-result'
                   for p in [node, *node.parents])


def _proof(node, selector, attribute=None):
    return {'selector': selector, 'attribute': attribute, 'node_sha256': _sha(str(node)),
            'raw': node.get(attribute) if attribute else _text(node)}


def _title(card, subtype):
    selectors = {'DEALS_ZONE': ('p[id^="title-"] .a-truncate-full',),
                 'LIGHTNING_DEAL_FILTER': ('p[id^="title-"] .a-truncate-full',),
                 'CUSTOMERS_MOST_LOVED_CURATED_ZONE': ('.dcl-product-title',),
                 'HAUL_LOW_PRICE_ZONE': ('[data-title="true"] a[href]',)}.get(subtype, ('h2',))
    from .adapters import _non_title_text
    for selector in (*selectors, 'img[alt]'):
        for n in card.select(selector):
            attr = 'alt' if n.name == 'img' else None
            value = n.get(attr) if attr else _text(n)
            if not value or _non_title_text(value):
                continue
            anchor = n if n.name == 'a' else n.find_parent('a', href=True)
            if not anchor or anchor not in card.descendants:
                continue
            href = anchor.get('href')
            if not normalize_product_url_v3(href)['is_valid']:
                continue
            status = 'TRUNCATED' if re.search(r'(?:\.\.\.|…)\s*$', value) else 'IMAGE_ALT' if attr else 'SOURCE_VISIBLE'
            return value, href, status, _proof(n, selector, attr)
    return None, None, 'UNVERIFIED', None


def _fields(card):
    result = {}
    evidence = {}
    def choose(name, values, malformed=False):
        distinct = {v for _, v, _ in values if v is not None}
        status = ('SOURCE_CONFLICT' if len(distinct) > 1 else 'PARSE_FAILED' if malformed
                  else 'VALID' if distinct else 'NOT_DISPLAYED')
        result[name + '_raw'] = values[0][0] if values else None
        result[name + '_status'] = status
        result[{'rating': 'rating_value', 'monthly_bought': 'monthly_bought_min', 'price': 'current_price'}.get(name, name)] = next(iter(distinct)) if status == 'VALID' else None
        evidence[name] = [v[2] for v in values]

    ratings, counts, monthly = [], [], []
    # Attribute values are actual accessible text, never star CSS inference.
    for n in card.select('[aria-label], .a-icon-alt'):
        raw = n.get('aria-label') or _text(n) or ''
        m = _RATING.search(raw)
        if m:
            v = float(m.group(1).replace(',', '.'))
            ratings.append((m.group(0), v if 0 <= v <= 5 else None, _proof(n, '[aria-label], .a-icon-alt', 'aria-label' if n.get('aria-label') else None)))
        m = _COUNT.search(raw)
        if m:
            number = m.group(1).strip()
            valid = re.fullmatch(r'\d+|\d{1,3}(?:[. \u00a0\u202f]\d{3})+', number)
            counts.append((raw, int(re.sub(r'\D', '', number)) if valid else None, _proof(n, '[aria-label]', 'aria-label')))
    for n in card.select('span'):
        if n.select_one('span'):
            continue
        raw = _text(n) or ''
        m = _MONTH.fullmatch(raw.replace('\u00a0', ' ').replace('\u202f', ' '))
        if m:
            v = float(m.group(1).replace(',', '.')) * 1000 if m.group(2) else int(re.sub(r'[. ]', '', m.group(1)))
            monthly.append((raw, int(v), _proof(n, 'span')))
    choose('rating', ratings, any(v is None for _, v, _ in ratings))
    choose('rating_count', counts, any(v is None for _, v, _ in counts))
    choose('monthly_bought', monthly)

    prices, references = [], []
    for n in card.select('.a-price'):
        raw = _text(n.select_one('.a-offscreen'))
        if not raw:
            continue
        ref = 'a-text-price' in n.get('class', []) or n.get('data-a-strike') == 'true'
        # Unit prices (e.g. 1,98€/unidad) never override a whole-product price.
        if ref and n.get('data-a-strike') != 'true':
            continue
        m = _MONEY.fullmatch(raw.replace('\u00a0', ' ').strip())
        v = float(m.group(1).replace('.', '').replace(',', '.')) if m else None
        (references if ref else prices).append((raw, v, _proof(n, '.a-price .a-offscreen')))
    choose('price', prices, any(v is None for _, v, _ in prices))
    result['current_price_raw'] = result.pop('price_raw')
    result['reference_price_raw'] = references[0][0] if references else None
    result['reference_price_semantics'] = 'DISPLAYED_REFERENCE_NOT_CONFIRMED_PREVIOUS_PRICE' if references else None
    raw_text = _text(card) or ''
    discount = re.search(r'-\s*\d{1,2}\s*%', raw_text)
    result['discount_raw'] = discount.group(0) if discount else None
    # Discount rate is computed only from valid paired product prices.
    reference_values = {v for _, v, _ in references if v is not None}
    reference = next(iter(reference_values)) if len(reference_values) == 1 else None
    current = result['current_price']
    result['discount_rate'] = round(1 - current / reference, 6) if current is not None and reference and reference >= current else None
    result['promotion_badge_raw'] = list(dict.fromkeys(_text(n) for n in card.select('[data-component="dui-badge"], [data-specialized-badge], .rio-badge-label') if _text(n)))
    result['cart_interest_raw'] = next((m.group(0) for m in [re.search(r'En\s+[\d.,]+\s+carritos', raw_text, re.I)] if m), None)
    result.update(written_review_count=None, rating_count_semantics='EVALUATION_COUNT_NOT_WRITTEN_REVIEWS',
                  monthly_bought_semantics='PUBLIC_PURCHASE_LOWER_BOUND', field_evidence=evidence)
    return result


def _foreign_target(card, raw):
    """Opaque route token is a rejection guard only, never a dedup key."""
    route = re.compile(r'/(?:dp|gp/product|product-reviews)/([^/?#]+)', re.I)
    target = route.search(raw or '')
    if not target:
        return True
    for a in card.select('a[href]'):
        other = route.search(a['href'])
        if other and other.group(1) != target.group(1):
            return True
    return False


def _navigation(soup, source, page_url, sha):
    found, seen = [], set()
    selectors = ('a.s-pagination-next[href], .a-pagination a[href], a.s-pagination-item[href], '
                 '#zg_browseRoot a[href], [aria-label="Árbol de navegación"] a[href], '
                 'a[href*="/cml-"], a[href*="srs="], a[href*="ishaul=1"], a[href*="i=bazaar"], a[href*="bubble-id="]')
    for a in soup.select(selectors):
        if not _eligible(a):
            continue
        href = a['href']; url = urljoin(page_url, href)
        if not amazon_url(url) or normalize_product_url_v3(url)['is_valid'] or url in seen:
            continue
        seen.add(url)
        kind = 'PAGINATION_LINK' if ('pagination' in ' '.join(a.get('class', []))
                or a.find_parent(class_='a-pagination')) else 'NAVIGATION_LINK'
        found.append({'url': url, 'raw_href': href, 'label_raw': _text(a), 'kind': kind,
                      'page_url': page_url, 'source_type': source['source_type'],
                      'evidence_sha256': sha, 'status': 'DISCOVERED_NOT_VISITED', 'approved': False})
    return found


def parse_v4_page(html, *, source, page_url, run_id, observed_at, evidence_path,
                  http_status=None, origin='OFFLINE_FIXTURE', snapshot_id=None, page_number=1,
                  acp_responses=None):
    if origin not in {'OFFLINE_FIXTURE', 'LIVE_CAPTURE', 'HISTORICAL_REPLAY'}:
        raise ValueError('UNKNOWN_ORIGIN')
    if datetime.fromisoformat(observed_at.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('AWARE_CAPTURE_TIME_REQUIRED')
    if not amazon_url(page_url) or page_url != source['actual_source_url']:
        raise ValueError('V4_EXACT_PAGE_SOURCE_REQUIRED')
    if origin == 'LIVE_CAPTURE' and http_status is None:
        raise ValueError('V4_ACTUAL_HTTP_RECEIPT_REQUIRED')
    soup = BeautifulSoup(html or '', 'lxml'); sha = _sha(html or '')
    access = detect_access_status(http_status, html).value
    # DOM fixtures have no HTTP measurement. UNKNOWN is not upgraded to NORMAL.
    permitted = access == 'NORMAL' or origin != 'LIVE_CAPTURE' and http_status is None and access == 'UNKNOWN'
    page = {'source_type': source['source_type'], 'source_subtype': source['source_subtype'],
            'page_url': page_url, 'source_id': source['source_id'], 'origin': origin,
            'http_status': http_status, 'access_status': access, 'observed_at': observed_at,
            'evidence_path': evidence_path, 'evidence_sha256': sha, 'parser_version': VERSION,
            'completeness_status': 'UNKNOWN', 'status': 'UNVERIFIED'}
    rows = []
    if not permitted:
        page['status'] = 'ACCESS_RESTRICTED'
        return {'page': page, 'observations': [], 'pagination': [], 'discovered_sources': [], 'acp_pending': []}
    kind = source['source_type']
    if kind in RANKED:
        from .adapters import parse_page, _cards
        legacy = parse_page(html, source={**source, 'parser_profile': 'V3'}, page_url=page_url,
                            run_id=run_id, snapshot_id=snapshot_id or run_id, page_number=page_number,
                            observed_at=observed_at, evidence_path=evidence_path,
                            http_status=200 if http_status is None and origin != 'LIVE_CAPTURE' else http_status,
                            origin=origin, acp_responses=acp_responses)
        cards = _cards(soup)
        for old in legacy['observations']:
            row = dict(old)
            row.update(product_title_es=old.get('product_title_raw'), ranking_semantics=source['source_subtype'])
            if old['representation'] == 'SERVER' and old['representation_slot'] < len(cards):
                fields = _fields(cards[old['representation_slot']])
                # Preserve V3's stricter rank/title/rating binding; enrich other fields only.
                for k, v in fields.items():
                    if not k.startswith(('rating_', 'field_evidence')):
                        row[k] = v
                row.update(rating_count_raw=old.get('review_count_raw'), rating_count=old.get('review_count'),
                           rating_count_status=old.get('review_count_status'))
            rows.append(row)
        page['legacy_page'] = legacy['page']
    else:
        subtype = source['source_subtype']
        if subtype not in SELECTORS:
            raise ValueError('V4_UNSUPPORTED_SUBTYPE')
        cards = soup.select(SELECTORS[subtype])
        accepted = set()
        for card in cards:
            if not _eligible(card) or any(id(p) in accepted for p in card.parents):
                continue
            accepted.add(id(card))
            title, raw, status, title_proof = _title(card, subtype)
            link = normalize_product_url_v3(raw, base_url=page_url)
            slot = len(rows)
            proof = {'card_sha256': _sha(str(card)), 'raw_product_url': raw,
                     'card_selector': SELECTORS[subtype], 'slot': slot,
                     'evidence_path': evidence_path, 'evidence_sha256': sha, 'title': title_proof}
            fields = _fields(card)
            if _foreign_target(card, raw):
                for field, value_key in [('rating', 'rating_value'), ('rating_count', 'rating_count'),
                                         ('monthly_bought', 'monthly_bought_min'), ('price', 'current_price')]:
                    fields[field + '_status'] = 'UNVERIFIED'
                    fields[value_key] = None
                fields['binding_doubts'] = ['MULTIPLE_FOREIGN_PRODUCT_TARGETS_IN_CARD']
            rows.append({**link, **fields, 'product_title_es': title, 'product_title_raw': title,
                         'product_title_status': status, 'product_title_evidence': {**(title_proof or {}), 'raw_product_url': raw},
                         'ranking_position': None, 'ranking_semantics': subtype,
                         'representation': 'SERVER', 'representation_slot': slot,
                         'source_evidence': proof})
    for index, row in enumerate(rows):
        row.update(source_id=source['source_id'], source_type=kind, source_subtype=source['source_subtype'],
                   source_url=source['actual_source_url'], source_page_url=page_url, page_url=page_url,
                   run_id=run_id, snapshot_id=snapshot_id or run_id, batch_id=run_id,
                   observed_at=row.get('observed_at', observed_at), origin=origin, evidence_path=row.get('evidence_path', evidence_path),
                   evidence_sha256=row.get('evidence_sha256', sha), parser_version=VERSION,
                   observation_id='v4obs_' + _sha(f'{run_id}|{sha}|{index}|{page_url}')[:32],
                   source_node_id=node_id(page_url), amazon_category_path=[],
                   amazon_category_l1=None, amazon_category_l2=None, amazon_category_l3=None, amazon_category_leaf=None,
                   research_category=None, category_mapping_status='UNKNOWN', source_completeness_status='UNKNOWN',
                   first_observed_at=row.get('observed_at', observed_at), last_observed_at=row.get('observed_at', observed_at),
                   qa_status='PARSED_PENDING_CENTRAL_DOM_REVIEW')
        row.setdefault('source_evidence', {'evidence_path': row['evidence_path'], 'evidence_sha256': row['evidence_sha256'],
                                         'raw_product_url': row['raw_product_url'], 'title': row.get('product_title_evidence')})
        row.setdefault('rating_count_semantics', 'EVALUATION_COUNT_NOT_WRITTEN_REVIEWS')
        row.setdefault('written_review_count', None)
        row['source_memberships'] = [{'source_id': source['source_id'], 'source_type': kind, 'source_subtype': source['source_subtype']}]
    nav = _navigation(soup, source, page_url, sha)
    page['status'] = 'VERIFIED_WITH_PRODUCTS' if any(r.get('is_valid') for r in rows) else 'NAVIGATION_ONLY' if nav else 'UNVERIFIED'
    page['actual_card_count'] = len(rows)
    return {'page': page, 'observations': rows, 'pagination': [n for n in nav if n['kind'] == 'PAGINATION_LINK'],
            'discovered_sources': nav, 'acp_pending': legacy['acp_pending'] if kind in RANKED else []}
