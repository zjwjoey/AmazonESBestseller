from amazon_es_bestseller.highspeed.v3_capacity import capacity_evidence


def test_capacity_does_not_sum_duplicate_list_supply_or_claim_live():
    plan={'sources':[{'source_id':'old','source_type':'BEST_SELLERS','page_urls':['a','b']},
        {'source_id':'new','source_type':'MOST_WISHED_FOR','page_urls':['c']}],
        'max_pages_per_run':2,'max_requests_per_run':10,'target_unique_links':30000}
    observations=[{'source_type':kind,'raw_product_url':'/dp/B012345678'}
        for kind in ('BEST_SELLERS','MOST_WISHED_FOR')]
    report=capacity_evidence(plan,[{'source_type':'MOST_WISHED_FOR','status':'FAILED'},
        {'source_type':'BEST_SELLERS','source_empty':True,'status':'FAILED'}],observations,new_source_ids=['new'])
    assert report['historical_global_distinct_url_candidates']==1
    assert sum(r['distinct_link_candidates'] for r in report['historical_per_type'])==2
    assert report['first_new_source_in_each_type']['MOST_WISHED_FOR']['within_page_only_budget_prefix'] is False
    assert report['current_verified_unique_capacity'] is None
    assert report['current_capacity_upper_bound'] is None
    assert report['target_supply_sufficiency']=='UNVERIFIED'
    wish=next(r for r in report['historical_per_type'] if r['source_type']=='MOST_WISHED_FOR')
    assert wish['latest_page_state_counts']=={'FAILED':1}


def test_capacity_tracks_literal_source_priority_without_constructing_urls():
    plan={'sources':[{'source_id':'new','source_type':'MOST_GIFTED','page_urls':['literal']},
        {'source_id':'old','source_type':'MOST_GIFTED','page_urls':['other']}],
        'max_pages_per_run':1,'max_requests_per_run':10,'target_unique_links':5000}
    report=capacity_evidence(plan,[],[],new_source_ids=['new'])
    assert report['first_new_source_in_each_type']['MOST_GIFTED']['within_page_only_budget_prefix'] is True
    assert report['historical_global_distinct_url_candidates']==0
    assert report['live_requests']==0
