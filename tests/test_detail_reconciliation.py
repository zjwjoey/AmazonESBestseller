import pytest
import json
from pathlib import Path

from amazon_es_bestseller.collection.detail_reconciliation import reconcile_outcomes


def tasks():
    return [{'asin': 'B07B8D8RC5', 'action': 'REUSE_VALID_CACHE'},
            {'asin': 'B0G46PL7RN', 'action': 'REUSE_VALID_CACHE'},
            {'asin': 'B000000001', 'action': 'FETCH_NEW'},
            {'asin': 'B000000002', 'action': 'FETCH_NEW'}]


def test_held_cache_tasks_cannot_disappear_or_count_as_success():
    result = reconcile_outcomes(tasks(), {'B000000001': 'success', 'B000000002': 'failed'},
                                {'B07B8D8RC5': 'OFFLINE_OR_REVIEW_ACTION', 'B0G46PL7RN': 'OFFLINE_OR_REVIEW_ACTION'})
    assert result['target'] == result['accounted'] == 4
    assert result['success'] == 1
    assert result['counts'] == {'CACHE_REVIEW_REQUIRED': 2, 'success': 1, 'failed': 1}
    assert result['pending'] == 0
    assert result['status'] == 'PARTIAL'


def test_pending_and_access_stop_remain_separate_and_success_wins_over_old_hold():
    result = reconcile_outcomes(tasks(), {'B07B8D8RC5': 'reused', 'B000000001': 'access_stop'},
                                {'B07B8D8RC5': 'old hold'})
    assert result['success'] == 1
    assert result['pending'] == 1
    assert result['counts']['access_stop'] == 1
    assert result['counts']['CACHE_REVIEW_REQUIRED'] == 1
    assert result['status'] == 'PARTIAL'


def test_total_is_derived_from_unique_frozen_tasks():
    assert reconcile_outcomes([], {}, {})['status'] != 'COMPLETE'
    with pytest.raises(ValueError, match='DUPLICATE'):
        reconcile_outcomes(tasks() + [tasks()[0]], {}, {})
    with pytest.raises(ValueError, match='UNKNOWN'):
        reconcile_outcomes(tasks(), {'B000000099': 'success'}, {})


def test_reconciliation_cannot_promote_offline_action_to_success():
    result = reconcile_outcomes(tasks(), {}, {})
    assert result['success'] == 0
    assert result['pending'] == 2
    assert result['counts']['CACHE_REVIEW_REQUIRED'] == 2


def test_saved_real_cache_handoff_cannot_promote_variation_or_missing_evidence(tmp_path):
    from amazon_es_bestseller.collection.detail_executor import execute_detail_plan
    from amazon_es_bestseller.collection.planning import DetailState
    fixture = json.loads((Path(__file__).parent / 'fixtures/action5500_cache_handoff_20261023.json').read_text())
    records = fixture['records']
    state = DetailState(tmp_path / 'state/details_state.json')
    # Even if an older row carrying the plan's variation hint is present,
    # another child's resolved identity cannot be reused under this ASIN.
    state.update([{**row, 'title_es_raw': 'Cached title'} for row in records])
    state.save()
    def forbidden(*a, **kw):
        pytest.fail('no collection allowed')
    plan = {'snapshot_id': 'real_cache_regression', 'records': [
        {**row, 'ranking_asin': row['asin'], 'snapshot_id': 'real_cache_regression',
         'saved_html_dir': str(tmp_path / 'missing_html')} for row in records]}
    before = state.path.read_bytes()
    result = execute_detail_plan(plan, None, str(tmp_path), offline=True, collector=forbidden, parser_version='v2')
    assert result['requested_count'] == 0
    assert result['completed_count'] == 0
    assert all(row['status'] == 'PENDING' for row in result['records'])
    assert len(result['records']) == 2
    # Original evidence files remain untouched; DetailState's authority does
    # not gain a fabricated same-ASIN success.
    assert json.loads(before) == json.loads(state.path.read_bytes())


def test_verified_saved_product_can_still_reuse_without_browser_or_raw_change(tmp_path):
    import hashlib
    import shutil
    from amazon_es_bestseller.collection.detail_executor import execute_detail_plan
    asin = 'B07QHCNQ7M'
    source = Path(__file__).parent / 'fixtures/html/detail500_20261022' / (asin + '.html')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    saved = tmp_path / 'html'
    saved.mkdir()
    shutil.copy2(source, saved / source.name)
    plan = {'snapshot_id': 'saved_page_reuse', 'records': [{
        'asin': asin, 'ranking_asin': asin, 'snapshot_id': 'saved_page_reuse',
        'detail_action': 'REUSE_VALID_CACHE', 'saved_html_dir': str(saved)}]}
    def forbidden(*a, **kw):
        pytest.fail('verified cache reuse must not collect')
    result = execute_detail_plan(plan, None, str(tmp_path / 'run'), offline=True,
                                 collector=forbidden, parser_version='v2')
    assert result['requested_count'] == 0
    assert result['completed_count'] == 1
    assert result['records'][0]['status'] == 'REUSED'
    assert result['state_record_count'] == 1
    assert json.loads((tmp_path / 'run/details.json').read_text(encoding='utf-8'))[0]['asin'] == asin
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert hashlib.sha256((saved / source.name).read_bytes()).hexdigest() == digest
