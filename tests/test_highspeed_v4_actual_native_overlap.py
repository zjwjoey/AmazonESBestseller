from pathlib import Path
from types import SimpleNamespace
import json
import hashlib
import pytest

from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from amazon_es_bestseller.transport.playwright import PlaywrightTransport


def actual_contracts():
    root = Path(__file__).parent / 'fixtures/highspeed_v4/native_overlap'
    meta = json.loads((root / 'provenance.json').read_text(encoding='utf8'))
    parent = root / 'parent.html'
    assert hashlib.sha256(parent.read_bytes()).hexdigest() == meta['parent_fixture_sha256']
    source = {'source_id': 'v4_8f19989acb79894b1f797ebc', 'source_type': 'BEST_SELLERS',
              'source_subtype': 'BESTSELLER_SALES_RANKING', 'actual_source_url': meta['url']}
    parsed = parse_v4_page(parent.read_text(encoding='utf8'), source=source, page_url=meta['url'],
                          run_id='offline_overlap', observed_at=meta['observed_at'], evidence_path=str(parent))
    descriptor = dict(parsed['acp_pending'][0], source_id=source['source_id'])
    owner = PlaywrightTransport._saved_carousel_owner(descriptor, meta['url'])
    captures = []
    for ref in meta['captures']:
        body = (root / ref['file']).read_bytes()
        assert hashlib.sha256(body).hexdigest() == ref['sha256']
        captures.append(json.loads(body))
    captures.sort(key=lambda r: r['captured_at'])
    return descriptor, owner, captures, meta['url']


def test_real_overlapping_batches_are_fifo_byte_exact_not_ambiguous():
    d, owner, captures, url = actual_contracts()
    transport = PlaywrightTransport(SimpleNamespace(page=SimpleNamespace(url=url)))
    transport._acp_capture_installed = True
    transport._native_acp_requests = captures
    result = transport._prepare_carousel_descriptor(d, referer=url)
    assert result['payload'] == captures[0]['payload']
    assert json.loads(result['payload'])['indexes'] == [6, 7, 8, 9, 10, 11]


def test_same_range_changed_body_stays_ambiguous():
    _, owner, captures, _ = actual_contracts()
    changed = dict(captures[0], payload=captures[0]['payload'] + ' ')
    matches = [(('a', '1'), captures[0]), (('a', '2'), changed)]
    with pytest.raises(ValueError, match='AMBIGUOUS'):
        PlaywrightTransport._ordered_carousel_contracts(matches, owner)


def test_foreign_ids_do_not_get_fifo_exception():
    _, owner, captures, _ = actual_contracts()
    body = json.loads(captures[1]['payload']); body['ids'][0] = 'foreign'
    changed = dict(captures[1], payload=json.dumps(body))
    with pytest.raises(ValueError, match='AMBIGUOUS'):
        PlaywrightTransport._ordered_carousel_contracts([(('a', '1'), captures[0]), (('a', '2'), changed)], owner)
