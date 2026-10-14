from amazon_es_bestseller.highspeed.candidate_contract import build_candidate_records

def test_missing_ratings_does_not_delete_candidates():
    rows=build_candidate_records([{'observation_id':'a','normalized_product_url':'https://www.amazon.es/dp/B077T9VSNZ','raw_product_url':'/dp/B077T9VSNZ','observed_at':'2026-10-14T00:00:00Z'}],batch_id='b',snapshot_id='s')
    assert len(rows)==1 and rows[0]['rating_value'] is None
from copy import deepcopy

def observation(oid='a',time='2026-10-14T00:00:00Z',rating=4.2,count=10,source='BEST_SELLERS',url='https://www.amazon.es/dp/B077T9VSNZ'):
    return {'observation_id':oid,'normalized_product_url':url,'raw_product_url':'/dp/B077T9VSNZ','observed_at':time,'rating_status':'VALID','rating_value':rating,'rating_raw':str(rating),'review_count':count,'review_count_status':'VALID','review_count_raw':str(count),'source_type':source,'source_id':source,'source_url':'source-'+source,'product_title_raw':'Original name','product_title_status':'SOURCE_VISIBLE','product_title_evidence':{'raw_product_url':'/dp/B077T9VSNZ'},'research_category':None,'category_path':['Original'],'ranking_position':1,'parent_asin':'parent'}

def build(rows):return build_candidate_records(rows,batch_id='b',snapshot_id='s')

def test_latest_rating_and_reviews_come_from_same_observation():
    rows=[observation(),observation('b','2026-10-14T01:00:00Z',4.8,5,'NEW_RELEASES')]
    c=build(rows)[0]
    assert c['rating_value']==4.8 and c['review_count']==5
    assert c['rating_status']=='SOURCE_CONFLICT' and c['rating_chosen_observation_id']=='b'
    assert len(c['rating_history'])==len(c['source_memberships'])==2
    assert c['rating_selection_rule']=='LATEST_VALID_OBSERVATION_v1'

def test_same_time_conflicts_not_arbitrarily_selected():
    c=build([observation(),observation('b',rating=4.8,count=100)])[0]
    assert c['rating_status']=='SOURCE_CONFLICT' and c['rating_value'] is None
    assert c['rating_chosen_observation_id'] is None and c['review_count'] is None

def test_same_rating_same_time_different_count_is_conflict():
    c=build([observation(),observation('b',count=11)])[0]
    assert c['rating_status']=='SOURCE_CONFLICT' and c['rating_value'] is None

def test_same_title_parent_different_url_never_merged():
    assert len(build([observation(),observation('b',url='https://www.amazon.es/dp/B012345678')]))==2

def test_books_digital_low_rating_never_filtered():
    rows=[observation(rating=1.0),observation('b',rating=2.0,url='https://www.amazon.es/dp/B012345678')]
    rows[0]['amazon_category']='Books';rows[1]['amazon_category']='Digital'
    cs=build(rows)
    assert len(cs)==2 and all(c['selection_audit_status']=='NOT_RUN' and c['selection_rules_version'] is None for c in cs)

def test_unverified_title_not_exposed_as_product_name():
    r=observation();r['product_title_status']='UNVERIFIED'
    c=build([r])[0]
    assert c['product_title_es'] is None and c['raw_observations'][0]['product_title_raw']=='Original name'

def test_source_conflicts_and_raw_history_not_mutated():
    rows=[observation(),observation('b','2026-10-14T01:00:00Z')];original=deepcopy(rows)
    c=build(rows)[0]
    assert rows==original
    c['raw_observations'][0]['product_title_raw']='edit'
    assert rows==original

def test_unknown_category_stays_unknown_and_all_sources_preserved():
    cs=build([observation(str(i),source=s) for i,s in enumerate(['BEST_SELLERS','NEW_RELEASES','MOVERS_SHAKERS','MOST_WISHED_FOR','MOST_GIFTED'])])
    assert len(cs)==1 and len(cs[0]['source_memberships'])==5 and cs[0]['research_category'] is None

def test_timestamp_sort_uses_absolute_time():
    c=build([observation('a','2026-10-14T01:00:00+02:00'),observation('b','2026-10-14T00:00:00Z')])[0]
    assert c['rating_chosen_observation_id']=='b' and c['latest_observed_at']=='2026-10-14T00:00:00Z'

def test_no_rating_time_cannot_select_valid_rating():
    c=build([observation(time='2026-10-14T01:00:00')])[0]
    assert c['rating_value'] is None

def test_explicit_contract_all_required_keys_present():
    c=build([observation()])[0]
    fields='candidate_id product_title_es raw_product_url normalized_product_url url_identity_status dedup_status rating_raw rating_value rating_status review_count_raw review_count review_count_status source_type source_url ranking_position rank_semantics original_amazon_category category_path browse_node_id research_category category_mapping_status first_observed_at latest_observed_at observation_count source_memberships snapshot_id batch_id parser_version evidence_reference'.split()
    assert set(fields)<=set(c)

def test_identity_query_candidates_never_coarse_merged():
    from amazon_es_bestseller.highspeed.v3_links import normalize_product_url_v3
    a=observation(url=normalize_product_url_v3('/dp/B077T9VSNZ?variant=red')['normalized_product_url'])
    b=observation('b',url=normalize_product_url_v3('/dp/B077T9VSNZ?variant=blue')['normalized_product_url'])
    assert len(build([a,b]))==2


def test_confirmed_tracking_suffix_canonical_group():
    from amazon_es_bestseller.highspeed.v3_links import normalize_product_url_v3
    a=observation(url=normalize_product_url_v3('/dp/B077T9VSNZ/ref=zg_bs_g_1_d_sccl_1/524-6514838-9761305?psc=1')['normalized_product_url'])
    b=observation('b',url=normalize_product_url_v3('/dp/B077T9VSNZ?psc=1')['normalized_product_url'])
    assert len(build([a,b]))==1

def test_memberships_and_rating_history_retain_exact_card_binding():
    r=observation();r.update(representation='ACP_0',representation_slot=4,rank_raw='#5',rank_context='source',run_id='r',snapshot_id='s',parser_version='link_first_adapters_v3.card_bound')
    c=build([r])[0]
    for key in ('raw_product_url','normalized_product_url','representation','representation_slot','rank_raw','rank_context','run_id','snapshot_id','parser_version'):
        assert c['source_memberships'][0][key]==r[key]
        assert c['rating_history'][0][key]==r[key]
