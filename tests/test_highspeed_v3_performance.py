from amazon_es_bestseller.highspeed.performance import summarize_v3_metrics

def test_replayed_target_and_partial_qa_never_prove_fresh_t3_speed():
    old={'candidate_id':'old','normalized_product_url':'old','origin':'HISTORICAL_REPLAY','is_derivation':True}
    fresh={'candidate_id':'new','normalized_product_url':'new','origin':'LIVE_CAPTURE'}
    result=summarize_v3_metrics([{'stage':'navigation','duration_seconds':2}],observations=[old,fresh],
        started_at='2026-10-14T00:00:00Z',finished_at='2026-10-14T01:00:00Z',target=2,qa_passed=True)
    assert result['unique_per_hour']==1
    assert result['inherited_unique_links']==1
    assert not result['ten_hour_target_passed']
    assert result['timing_breakdown']['browser_wait_seconds'] is None
    assert result['timing_breakdown']['actual_network_seconds'] is None
    assert result['timing_breakdown']['fetch_transaction_seconds']==2
    result=summarize_v3_metrics([],observations=[fresh],origin='HISTORICAL_REPLAY',
        started_at='2026-10-14T00:00:00Z',finished_at='2026-10-14T00:00:01Z',target=1,qa_passed=True)
    assert result['unique_per_hour'] is None and not result['ten_hour_target_passed']

def test_fresh_target_requires_finished_passing_qa_full_wall():
    row={'candidate_id':'new','normalized_product_url':'new','origin':'LIVE_CAPTURE'}
    for passed in (False,True):
        result=summarize_v3_metrics([],observations=[row],started_at='2026-10-14T00:00:00Z',
            finished_at='2026-10-14T01:00:00Z',target=1,qa_passed=passed)
        assert result['ten_hour_target_passed'] is passed
