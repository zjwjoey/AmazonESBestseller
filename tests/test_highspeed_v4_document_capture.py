from types import SimpleNamespace
from amazon_es_bestseller.transport.playwright import PlaywrightTransport


def session(raw='<html>actual document</html>', dom='<html>initialized DOM</html>'):
    url = 'https://www.amazon.es/gp/bestsellers'
    return SimpleNamespace(page=SimpleNamespace(url=url, content=lambda: dom), goto=lambda u: 200,
                           last_document_response=SimpleNamespace(url=url, status=200, text=lambda: raw))


def test_opt_in_uses_buffered_document_and_preserves_rendered_frame():
    result = PlaywrightTransport(session(), preserve_document_body=True).fetch_page('https://www.amazon.es/gp/bestsellers')
    assert result['text'] == result['http_document_html'] == '<html>actual document</html>'
    assert result['rendered_dom_html'] == '<html>initialized DOM</html>'
    assert result['source_representation'] == 'HTTP_DOCUMENT_BODY'


def test_legacy_transport_keeps_rendered_default():
    result = PlaywrightTransport(session()).fetch_page('https://www.amazon.es/gp/bestsellers')
    assert result.text == '<html>initialized DOM</html>'


def test_rendered_challenge_is_not_hidden_by_normal_document():
    result = PlaywrightTransport(session(dom='<h1>Robot Check</h1>'), preserve_document_body=True).fetch_page('https://www.amazon.es/gp/bestsellers')
    assert result['access_state'] == 'CHALLENGE'
    assert 'Robot Check' in result['text']


def test_document_challenge_is_not_hidden_by_normal_dom():
    result = PlaywrightTransport(session(raw='<h1>Robot Check</h1>'), preserve_document_body=True).fetch_page('https://www.amazon.es/gp/bestsellers')
    assert result['access_state'] == 'CHALLENGE'
    assert 'Robot Check' in result['text']


def test_real_consumed_dom_keeps_strict_missing_owner_guard():
    from pathlib import Path
    import json, hashlib, pytest
    from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
    root = Path(__file__).parent / 'fixtures/highspeed_v4/native_consumed'
    proof = json.loads((root / 'provenance.json').read_text(encoding='utf8'))
    parent = root / 'parent.html'
    assert hashlib.sha256(parent.read_bytes()).hexdigest() == proof['evidence_sha256']
    source = {k: proof[k] for k in ('source_id', 'source_type', 'source_subtype')}
    source['actual_source_url'] = proof['page_url']
    result = parse_v4_page(parent.read_text(encoding='utf8'), source=source, page_url=proof['page_url'],
                           run_id='offline_consumed', observed_at=proof['observed_at'], evidence_path=str(parent))
    assert len(result['observations']) == 32
    descriptor = dict(result['acp_pending'][0], source_id=source['source_id'])
    with pytest.raises(ValueError, match='OWNER_EVIDENCE_UNVERIFIED'):
        PlaywrightTransport._saved_carousel_owner(descriptor, proof['page_url'])
