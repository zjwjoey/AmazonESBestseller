from copy import deepcopy
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed import three_metrics as metrics
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from amazon_es_bestseller.highspeed.v4_sources import source_for_url, SEEDS


def real_rows():
    source=source_for_url(SEEDS[6]['url']);source['page_urls']=[source['actual_source_url']]
    p=Path(__file__).parent/'fixtures/highspeed_v4/S07_favorites_results_complete.html'
    parsed=parse_v4_page(p.read_text('utf8'),source=source,page_url=source['actual_source_url'],
        run_id='TEST_ONLY',observed_at='2026-10-15T01:00:00Z',evidence_path=str(p),http_status=200,origin='LIVE_CAPTURE')
    return parsed['observations']


def test_fresh_source_independent_counts_never_use_other_source_as_fill():
    rows=real_rows(); assert rows
    result=metrics.measure_three(rows,'TEST_ONLY')
    assert result['sources']['BEST_SELLERS']['E_qualified_review_groups']==0
    assert result['sources']['NEW_RELEASES']['E_qualified_review_groups']==0
    assert result['sources']['CUSTOMER_FAVORITES']['E_qualified_review_groups']>0
    assert result['target_met'] is False
    assert result['acceptance']=='NOT_ACCEPTED — THREE_SOURCE_5001_TARGET_NOT_MET'
    assert result['G_verified_independent_buyable_SKUs']==0 and result['SKU_identity_status']=='NOT_VERIFIED'


def test_history_and_foreign_runs_do_not_add_live_target():
    rows=real_rows();before=metrics.measure_three(rows,'TEST_ONLY')
    old=deepcopy(rows)
    for r in old:r.update(origin='HISTORICAL_REPLAY',run_id='old')
    after=metrics.measure_three(rows+old,'TEST_ONLY')
    assert after['F_global_conservative_review_groups']==before['F_global_conservative_review_groups']
    assert after['excluded_noncurrent_or_nonlive_observations']==len(old)


def test_variant_urls_are_preserved_but_one_conservative_group():
    row=real_rows()[0];rows=[]
    from amazon_es_bestseller.highspeed.v3_links import normalize_product_url_v3
    for i,query in enumerate(('?size=small','?size=large','?unknown=x')):
        r=deepcopy(row);raw=row['normalized_product_url'].split('?')[0]+query
        r.update(normalize_product_url_v3(raw));r.update(observation_id='test'+str(i),raw_product_url=raw)
        rows.append(r)
    result=metrics.measure_three(rows,'TEST_ONLY')
    own=result['sources']['CUSTOMER_FAVORITES']
    assert own['D_unique_normalized_URLs']==3 and own['E_qualified_review_groups']==1


def test_missing_title_and_identity_binding_conflicts_do_not_meet_threshold():
    rows=real_rows()
    for r in rows:r['binding_doubts']=['REAL_CONFLICT_RETAINED']
    result=metrics.measure_three(rows,'TEST_ONLY')
    assert result['sources']['CUSTOMER_FAVORITES']['E_qualified_review_groups']==0
    assert result['excluded_unqualified_observations']==len(rows)


def test_other_source_is_rejected_instead_of_silently_exported():
    rows=real_rows();rows[0]['source_type']='DEALS'
    with pytest.raises(ValueError,match='THREE_FOREIGN_SOURCE'):
        metrics.measure_three(rows,'TEST_ONLY')
