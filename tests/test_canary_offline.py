"""Saved repository HTML with TEST_ONLY Spain-header derivatives; no network."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest

from amazon_es_bestseller.canary import (CONTRACT, build_canary_execution_plan,
    review_saved_evidence, compare_detail_replay)
from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2
from amazon_es_bestseller.cli import main
from amazon_es_bestseller.multisource.storage import sealed, digest

FIXTURES = Path(__file__).parent/'fixtures'/'html'
URL = 'https://www.amazon.es/gp/bestsellers/kitchen'
ASINS = ['B078C6QR1C', 'B075JJRFVV', 'B07RN64P2R']
SPAIN = '<div id="glow-ingress-line2">Enviar a Madrid 28001</div>'


def ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def save(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    return ref(path)


def fixture(tmp_path, *, details=False):
    root = tmp_path/'evidence';root.mkdir()
    html = root/'ranking.html'
    html.write_text((FIXTURES/'bestsellers_grid.html').read_text('utf-8').replace('<body>', '<body>'+SPAIN), encoding='utf-8')
    records = [{'asin': asin, 'bestseller_rank': i+1, 'ranking_source_url': URL,
                'ranking_page_url': URL, 'ranking_page_number': 1} for i, asin in enumerate(ASINS)]
    page = {'url': URL, 'page_number': 1, 'expected_count': 3, 'http_status': 200,
            'observed_at': '2026-10-29T00:00:00Z', 'html': ref(html),
            'observed_records': save(root/'records.json', records)}
    manifest = {'contract': CONTRACT, 'source_url': URL, 'pages': [page], 'details': []}
    if details:
        detail_html = root/'detail.html'
        detail_html.write_text((FIXTURES/'product_delonghi.html').read_text('utf-8').replace('<body>', '<body>'+SPAIN), encoding='utf-8')
        url = 'https://www.amazon.es/dp/'+ASINS[2]
        observed = parse_detail_evidence_v2(detail_html.read_text('utf-8'), ASINS[2], requested_url=url, final_url=url)
        manifest['details'] = [{'asin': ASINS[2], 'final_url': url, 'http_status': 200,
            'html': ref(detail_html), 'observed_detail': save(root/'detail.json', observed)}]
    path = root/'manifest.json';save(path, manifest)
    return path, manifest


def test_saved_ranking_and_detail_pass_without_granting_live_or_identity_authority(tmp_path):
    path, manifest = fixture(tmp_path, details=True)
    before = {p: p.read_bytes() for p in path.parent.iterdir()}
    report = review_saved_evidence(path, output=tmp_path/'external-report.json')
    assert report['passed'], report['checks']
    assert report['network_requests'] == 0 and not report['live_authorized']
    assert not report['ready_for_detail_plan'] and not report['source_completeness_claimed']
    assert report['production_acceptance'] == 'NOT_EVALUATED'
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize('pages,details', [(0,0),(3,0),(True,0),(1,6),(1,True)])
def test_scope_limits_fail_closed(pages, details):
    with pytest.raises(ValueError, match='SCOPE'):
        build_canary_execution_plan([URL], pages, details)


def test_scope_rejects_multiple_sources_and_foreign_urls():
    with pytest.raises(ValueError):build_canary_execution_plan([URL,URL])
    with pytest.raises(ValueError):build_canary_execution_plan(['https://example.invalid/gp/bestsellers/home'])


def test_two_pages_keep_cross_page_asin_observations_and_distinct_ranks(tmp_path):
    path, data = fixture(tmp_path)
    page = deepcopy(data['pages'][0]);page['page_number'] = 2;page['url'] = URL+'?pg=2'
    second = path.parent/'second.html'
    body = Path(page['html']['path']).read_text('utf-8')
    for rank in (3,2,1):body = body.replace('#'+str(rank), '#'+str(rank+3))
    second.write_text(body,encoding='utf-8');page['html'] = ref(second)
    rows = [{'asin': asin, 'bestseller_rank': i+4, 'ranking_source_url': URL,
             'ranking_page_url': page['url'], 'ranking_page_number': 2} for i,asin in enumerate(ASINS)]
    page['observed_records'] = save(path.parent/'second-records.json',rows)
    data['pages'].append(page);save(path,data)
    assert review_saved_evidence(path)['passed']
    assert len(review_saved_evidence(path)['pages']) == 2
    # Same page-1 ranks on a purported page 2 cannot pass the global slot gate.
    second.write_text(Path(data['pages'][0]['html']['path']).read_text('utf-8'),encoding='utf-8')
    page['html'] = ref(second);save(path,data)
    assert not review_saved_evidence(path)['passed']


def test_hash_tampering_rejected_before_output(tmp_path):
    path, data = fixture(tmp_path)
    Path(data['pages'][0]['html']['path']).write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='SHA'):
        review_saved_evidence(path, output=tmp_path/'must-not-exist.json')
    assert not (tmp_path/'must-not-exist.json').exists()


@pytest.mark.parametrize('change', ['France', 'missing', 'captcha', '403', '429', 'unknown'])
def test_access_and_destination_must_be_reverified_from_saved_page(tmp_path, change):
    path, data = fixture(tmp_path)
    page = data['pages'][0];html = Path(page['html']['path'])
    text = html.read_text('utf-8')
    if change == 'France':text = text.replace('Madrid 28001', 'France')
    if change == 'missing':text = text.replace(SPAIN, '')
    if change == 'captcha':text += '<div>Robot Check</div>'
    if change in {'403','429','unknown'}:page['http_status'] = None if change == 'unknown' else int(change)
    html.write_text(text, encoding='utf-8');page['html'] = ref(html);save(path,data)
    assert not review_saved_evidence(path)['passed']


def test_rank_tuple_mismatch_and_source_scope_rejected(tmp_path):
    path, data = fixture(tmp_path)
    records = Path(data['pages'][0]['observed_records']['path'])
    rows = json.loads(records.read_text('utf-8'));rows[0]['bestseller_rank'] = 99
    data['pages'][0]['observed_records'] = save(records, rows);save(path, data)
    assert not review_saved_evidence(path)['passed']
    data['pages'][0]['url'] = 'https://www.amazon.es/gp/bestsellers/tools';save(path,data)
    with pytest.raises(ValueError, match='OUTSIDE'):review_saved_evidence(path)


def test_duplicate_pages_and_unranked_details_rejected(tmp_path):
    path, data = fixture(tmp_path, details=True)
    data['pages'] *= 2;save(path,data)
    with pytest.raises(ValueError, match='ORDERED'):review_saved_evidence(path)
    data['pages'] = data['pages'][:1];data['details'][0]['asin'] = 'B000000000';save(path,data)
    with pytest.raises(ValueError, match='RANKING_AUTHORITY'):review_saved_evidence(path)


def test_duplicate_detail_asins_cannot_silently_collapse(tmp_path):
    path, data = fixture(tmp_path, details=True)
    data['details'] *= 2;save(path,data)
    with pytest.raises(ValueError, match='DUPLICATE'):review_saved_evidence(path)


def test_parent_and_ordered_evidence_changes_fail_detail_replay(tmp_path):
    path, data = fixture(tmp_path, details=True)
    observed_path = Path(data['details'][0]['observed_detail']['path'])
    observed = json.loads(observed_path.read_text('utf-8'));observed['parent_asin'] = 'B000000000'
    data['details'][0]['observed_detail'] = save(observed_path, observed);save(path,data)
    assert not review_saved_evidence(path)['passed']


def test_clock_hash_binding_is_read_only_and_not_live_authorization(tmp_path):
    path, data = fixture(tmp_path)
    plan = {'run_id':'TEST_ONLY','target':100000}
    clock = {'run_id':'TEST_ONLY','plan_sha256':digest(plan),'deadline':'2026-10-29T00:00:00Z'}
    data['task_plan'] = save(path.parent/'plan.json', sealed(plan))
    data['task_clock'] = save(path.parent/'clock.json', sealed(clock));save(path,data)
    before = (path.parent/'clock.json').read_bytes()
    assert review_saved_evidence(path)['passed']
    assert (path.parent/'clock.json').read_bytes() == before
    clock['plan_sha256'] = 'changed';data['task_clock'] = save(path.parent/'clock.json', sealed(clock));save(path,data)
    assert not review_saved_evidence(path)['passed']


def test_output_cannot_overlap_evidence(tmp_path):
    path,_ = fixture(tmp_path)
    with pytest.raises(ValueError,match='OUTSIDE_EVIDENCE'):
        review_saved_evidence(path, output=path.parent/'forbidden.json')
    assert not (path.parent/'forbidden.json').exists()


def test_cli_plan_and_review_are_always_offline_and_live_flag_is_absent(tmp_path,monkeypatch):
    from amazon_es_bestseller.access.browser import BrowserSession
    monkeypatch.setattr(BrowserSession,'__init__',lambda *a,**kw:pytest.fail('BROWSER_OPENED'))
    assert main(['--offline','canary','plan','--source-url',URL]) == 0
    path,_ = fixture(tmp_path)
    assert main(['--offline','canary','review','--manifest',str(path),'--out',str(tmp_path/'report.json')]) == 0
    with pytest.raises(SystemExit) as error:
        main(['canary','plan','--source-url',URL,'--execute-real-amazon'])
    assert error.value.code == 2
