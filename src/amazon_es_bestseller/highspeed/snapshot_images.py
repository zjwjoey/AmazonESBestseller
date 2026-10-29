"""Opt-in, card-bound image observations. Never download or infer a variant."""
import hashlib
from urllib.parse import urljoin, urlsplit

from .v3_links import normalize_product_url_v3

CONTRACT = 'CARD_IMAGE_V1'


def card_image(card, raw_product_url, page_url):
    result = {'image_url': None, 'image_status': 'NOT_DISPLAYED', 'image_evidence': []}
    if card is None:
        result['image_status'] = 'UNVERIFIED'
        return result
    target = normalize_product_url_v3(raw_product_url, page_url)
    if not target['is_valid']:
        result['image_status'] = 'UNVERIFIED'
        return result
    matches = []
    for image in card.select('img[src]'):
        anchor = image.find_parent('a', href=True)
        if anchor is None or anchor not in card.descendants:
            continue
        linked = normalize_product_url_v3(anchor['href'], page_url)
        if not linked['is_valid'] or linked['normalized_product_url'] != target['normalized_product_url']:
            continue
        raw = str(image['src']).strip()
        url = urljoin(page_url, raw)
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.username or parts.password
                or parts.hostname not in {'m.media-amazon.com', 'images-na.ssl-images-amazon.com',
                                          'images-eu.ssl-images-amazon.com'}):
            continue
        matches.append((url, {'selector': 'img[src]', 'attribute': 'src', 'raw': raw,
            'raw_product_url': raw_product_url, 'node_sha256': hashlib.sha256(str(image).encode()).hexdigest()}))
    urls = {url for url, _ in matches}
    result['image_evidence'] = [proof for _, proof in matches]
    result['image_status'] = 'VALID' if len(urls) == 1 else 'SOURCE_CONFLICT' if urls else 'NOT_DISPLAYED'
    if len(urls) == 1:
        result['image_url'] = next(iter(urls))
    return result
