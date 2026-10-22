from amazon_es_bestseller.highspeed.category_mapping import map_category, namespace_from_url
from amazon_es_bestseller.highspeed.schedule import source_order
from amazon_es_bestseller.highspeed.sources import detailed_coverage_matrix


def test_unknown_namespace_never_guesses():
    assert map_category({"actual_source_url": "https://www.amazon.es/gp/bestsellers/beauty/123"}, [])['research_category'] is None


def test_five_type_round_robin_is_opt_in_and_exact_scope():
    rows=[{'source_id':'b1','source_type':'BEST_SELLERS'},{'source_id':'b2','source_type':'BEST_SELLERS'},{'source_id':'m','source_type':'MOVERS_SHAKERS'},{'source_id':'n','source_type':'NEW_RELEASES'}]
    assert source_order(rows)==rows
    assert [s['source_id'] for s in source_order(rows, policy='FIVE_TYPE_ROUND_ROBIN_V3')]==['b1','m','n','b2']


def test_historical_navigation_not_current_availability():
    sources=[{'source_id':'a','source_type':'BEST_SELLERS','research_category':'玩具','source_status':'AVAILABLE','discovery_evidence':[{'origin':'HISTORICAL_REPLAY'}]}]
    matrix=detailed_coverage_matrix(sources, [], [])
    cell=next(r for r in matrix if r['source_type']=='BEST_SELLERS' and r['research_category']=='玩具')
    assert len(matrix)==75
    assert cell['source_status']=='UNVERIFIED'
    assert cell['verified_source_count']==0
    assert cell['discovered_source_count']==1


def test_namespace_matches_whole_department():
    assert namespace_from_url('https://www.amazon.es/-/en/gp/new-releases/pet-supplies/123')=='pet-supplies'
    assert namespace_from_url('https://evil.test/gp/bestsellers/pet-supplies') is None


import json
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed.category_mapping import validate_mapping_rules


def _rules():
    return json.loads(Path('configs/highspeed/v3/category_mapping.reviewed.json').read_text(encoding='utf8'))['rules']


def test_real_saved_navigation_mapping_and_mixed_department():
    rules=validate_mapping_rules(_rules())
    mapped=map_category({'actual_source_url':'https://www.amazon.es/gp/new-releases/sports/123','amazon_category':'Patines'},rules)
    assert mapped['research_category']=='体育用品'
    assert mapped['original_amazon_category']=='Patines'
    assert mapped['browse_node_id']=='123'
    assert map_category({'actual_source_url':'https://www.amazon.es/gp/bestsellers/kitchen/123'},rules)['category_mapping_status']=='UNKNOWN'


def test_exact_prior_conflict_is_retained(tmp_path):
    import hashlib
    plan=tmp_path/"plan.json"
    plan.write_text(json.dumps({"categories":[{"research_category":"旅行","sources":[{"source_url":"https://www.amazon.es/gp/bestsellers/sports/123"}]}]}),encoding="utf8")
    proof={"plan_path":str(plan),"plan_sha256":hashlib.sha256(plan.read_bytes()).hexdigest(),"research_category":"旅行"}
    result=map_category({'actual_source_url':'https://www.amazon.es/gp/bestsellers/sports/123','research_category':'旅行','mapping_evidence':[proof]},validate_mapping_rules(_rules()))
    assert result['research_category'] is None
    assert result['source_conflict'] is True
    assert result['category_mapping_status']=='SOURCE_CONFLICT'


@pytest.mark.parametrize('mutation,reason',[
    ({'reviewed':False},'EXPLICIT_MAPPING_REVIEW_REQUIRED'),
    ({'namespace':'beauty-extra'},'DEPARTMENT_ROOT_PROOF_REQUIRED'),
    ({'scope':'TITLE_GUESS'},'MAPPING_SCOPE_REQUIRED')])
def test_invalid_review_policy_rejected(mutation,reason):
    rule=_rules()[0]; rule.update(mutation)
    with pytest.raises(ValueError,match=reason):validate_mapping_rules([rule])


def test_proof_tampering_rejected():
    rule=_rules()[0];rule['evidence']['label_raw']='Invented'
    with pytest.raises(ValueError,match='MAPPING_LITERAL_ANCHOR_MISSING'):validate_mapping_rules([rule])
    rule=_rules()[0];rule['evidence']['html_sha256']='0'*64
    with pytest.raises(ValueError,match='MAPPING_HTML_HASH_MISMATCH'):validate_mapping_rules([rule])


def test_failed_page_never_empty_or_complete():
    s={'source_id':'a','source_type':'MOST_GIFTED','research_category':'玩具'}
    p={'source_id':'a','status':'FAILED','access_status':'NORMAL','source_type_verified':True,'source_context_valid':True,'evidence_path':'x','evidence_sha256':'a'*64,'page_complete':False,'valid_link_count':0}
    row=next(r for r in detailed_coverage_matrix([s],[p],[]) if r['source_type']=='MOST_GIFTED' and r['research_category']=='玩具')
    assert row['source_status']=='UNVERIFIED'
    assert row['completed_page_count']==0


def test_coverage_candidate_count_uses_targetgroup_not_url():
    s={'source_id':'a','source_type':'BEST_SELLERS','research_category':'玩具'}
    obs=[{'source_id':'a','target_group_id':'same','link_valid':True},{'source_id':'a','target_group_id':'same','link_valid':True},{'source_id':'a','target_group_id':'other','link_valid':False}]
    row=next(r for r in detailed_coverage_matrix([s],[],obs) if r['source_type']=='BEST_SELLERS' and r['research_category']=='玩具')
    assert row['unique_link_candidate_count']==1
    assert row['extracted_observation_count']==3


def test_unproved_prior_does_not_upgrade_mapping():
    result=map_category({'actual_source_url':'https://www.amazon.es/gp/bestsellers/books/123','research_category':'兴趣爱好','mapping_evidence':[{'reviewed':True}]},[])
    assert result['category_mapping_status']=='UNKNOWN'



def test_all_812_real_source_pages_mapping_baseline():
    fixture=json.loads(Path('tests/fixtures/highspeed/v3_812_source_baseline.json').read_text(encoding='utf8'))
    assert len(fixture['sources'])==812
    assert sum(s['candidate_count'] for s in fixture['sources'])==30026
    assert sum(s['original_unmapped_count'] for s in fixture['sources'])==24541
    rules=validate_mapping_rules(_rules())
    counts={'VERIFIED':0,'UNKNOWN':0,'SOURCE_CONFLICT':0}
    for row in fixture['sources']:
        assert row['source'] is not None
        mapped=map_category(row['source'],rules)
        counts[mapped['category_mapping_status']]+=row['candidate_count']
    assert counts=={'VERIFIED':27773,'UNKNOWN':1955,'SOURCE_CONFLICT':298}



def test_discovery_never_constructs_sibling_types_from_best():
    from amazon_es_bestseller.highspeed.sources import discover_sources
    fixture=Path('tests/fixtures/highspeed/v3_real_department_navigation.html').read_text(encoding='utf8')
    best=discover_sources(fixture,'BEST_SELLERS','https://www.amazon.es/gp/bestsellers')
    assert len(best)==33
    assert discover_sources(fixture,'MOST_GIFTED','https://www.amazon.es/gp/most-gifted')==[]
    assert all(row['source_status']=='UNVERIFIED' for row in best)
    assert all(row['research_category'] is None for row in best)


def test_blocked_page_does_not_count_verified_even_with_context_flags():
    source={'source_id':'x','source_type':'MOST_WISHED_FOR','research_category':'玩具'}
    page={'source_id':'x','access_status':'BLOCKED','source_type_verified':True,'source_context_valid':True,'evidence_path':'x','evidence_sha256':'a'*64,'status':'COMPLETE','page_complete':True,'complete_evidence':[{'kind':'X'}],'valid_link_count':50}
    row=next(r for r in detailed_coverage_matrix([source],[page],[]) if r['source_type']=='MOST_WISHED_FOR' and r['research_category']=='玩具')
    assert row['verified_source_count']==0
    assert row['completed_page_count']==0
    assert row['source_status']=='UNVERIFIED'


def test_unknown_order_policy_and_kind_rejected():
    with pytest.raises(ValueError,match='UNKNOWN_SOURCE_ORDER_POLICY'):source_order([],policy='GUESS')
    with pytest.raises(ValueError,match='UNKNOWN_SOURCE_TYPE'):source_order([{'source_type':'GUESS'}],policy='FIVE_TYPE_ROUND_ROBIN_V3')



def test_config_loader_independent_of_working_directory(tmp_path,monkeypatch):
    from amazon_es_bestseller.highspeed.category_mapping import load_mapping_rules
    config=Path('configs/highspeed/v3/category_mapping.reviewed.json').resolve()
    monkeypatch.chdir(tmp_path)
    rules=load_mapping_rules(config)
    assert map_category({'actual_source_url':'https://www.amazon.es/gp/new-releases/office/123'},rules)['research_category']=='办公与文具'



def test_exact_node_requires_navigation_context_and_preserves_mixed_siblings(tmp_path):
    import hashlib
    body='<h1>Los más vendidos en Utensilios de cocina</h1><nav id="zg_browseRoot"><a href="/gp/bestsellers/kitchen/123">Afiladores de cuchillos</a></nav>'
    path=tmp_path/'nav.html';path.write_text(body,encoding='utf8')
    rule={'scope':'EXACT_NODE','browse_node_id':'123','research_category':'厨房用品','reviewed':True,'reviewer':'offline-test','review_reason':'Explicit leaf + parent context','evidence':{'html_path':str(path),'html_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'page_url':'https://www.amazon.es/gp/bestsellers/kitchen/456','raw_href':'/gp/bestsellers/kitchen/123','label_raw':'Afiladores de cuchillos','parent_heading_raw':'Los más vendidos en Utensilios de cocina','navigation_text_raw':'Afiladores de cuchillos'}}
    rules=validate_mapping_rules([rule])
    assert map_category({'actual_source_url':'https://www.amazon.es/gp/bestsellers/kitchen/123'},rules)['research_category']=='厨房用品'
    assert map_category({'actual_source_url':'https://www.amazon.es/gp/bestsellers/kitchen/124'},rules)['research_category'] is None
    rule['evidence']['parent_heading_raw']='Guessed unrelated parent'
    with pytest.raises(ValueError,match='NODE_PARENT_CONTEXT_MISMATCH'):validate_mapping_rules([rule])



def test_real_cleaning_context_resolves_ambiguous_gloves_and_poles_only_exact_nodes():
    from amazon_es_bestseller.highspeed.category_mapping import load_mapping_rules
    rules=load_mapping_rules('configs/highspeed/v3/category_mapping.reviewed.json')
    assert map_category({'actual_source_url':'https://www.amazon.es/gp/bestsellers/kitchen/56171433031'},rules)['research_category']=='家务用品'
    assert map_category({'actual_source_url':'https://www.amazon.es/gp/bestsellers/kitchen/3678031031'},rules)['research_category']=='家务用品'
    assert map_category({'actual_source_url':'https://www.amazon.es/gp/bestsellers/kitchen/99999999999'},rules)['research_category'] is None
    assert map_category({'actual_source_url':'https://www.amazon.es/gp/bestsellers/baby'},rules)['research_category'] is None


def test_exact_child_priority_keeps_old_human_mapping_conflict(tmp_path):
    import hashlib
    from amazon_es_bestseller.highspeed.category_mapping import load_mapping_rules
    rules=load_mapping_rules('configs/highspeed/v3/category_mapping.reviewed.json')
    # Existing toys policy conflicts must not be erased by the new node layer.
    f=json.loads(Path('tests/fixtures/highspeed/v3_812_source_baseline.json').read_text(encoding='utf8'))
    source=next(row['source'] for row in f['sources'] if row['source']['actual_source_url'].endswith('/1641736031'))
    assert map_category(source,rules)['category_mapping_status']=='SOURCE_CONFLICT'
    # Defensible node-specific precedence concerns broad namespace policy only.
    exact=next(rule for rule in _rules() if rule['scope']=='EXACT_NODE')
    exact=dict(exact);exact['research_category']='兴趣爱好'
    proof=exact['evidence'];namespace_rule=dict(_rules()[0])
    # The node has a mixed department, so construct a separately reviewed
    # contradictory namespace policy using the real kitchen root anchor.
    original=Path('tests/fixtures/highspeed/v3_real_department_navigation.html')
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(original.read_bytes(),'lxml')
    anchor=next(a for a in soup.select('a[href]') if a['href'].startswith('/gp/bestsellers/kitchen/ref='))
    namespace_rule.update(scope='REVIEWED_DEPARTMENT_SUBTREE',namespace='kitchen',research_category='居家生活',evidence={'html_path':str(original),'html_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'raw_href':anchor['href'],'label_raw':anchor.get_text(' ',strip=True),'page_url':'https://www.amazon.es/gp/bestsellers'})
    valid=validate_mapping_rules([namespace_rule,exact])
    mapped=map_category({'actual_source_url':proof['raw_href']},valid)
    assert mapped['research_category']=='兴趣爱好'
    assert not mapped['source_conflict']


def test_tampered_exact_node_navigation_text_rejected():
    rule=next(rule for rule in _rules() if rule['scope']=='EXACT_NODE')
    rule['evidence']['navigation_text_raw']='Invented node ancestry'
    with pytest.raises(ValueError,match='NODE_NAVIGATION_CONTEXT_MISMATCH'):validate_mapping_rules([rule])



def test_mapping_twice_cannot_wash_out_original_human_conflict():
    from amazon_es_bestseller.highspeed.category_mapping import load_mapping_rules
    fixture=json.loads(Path('tests/fixtures/highspeed/v3_812_source_baseline.json').read_text(encoding='utf8'))
    source=next(row['source'] for row in fixture['sources'] if row['source']['actual_source_url'].endswith('/1641736031'))
    rules=load_mapping_rules('configs/highspeed/v3/category_mapping.reviewed.json')
    first=map_category(source,rules)
    assert first['category_mapping_status']=='SOURCE_CONFLICT'
    second=map_category({**source,**first},rules)
    assert second==first


def test_prior_plan_fixture_preserves_original_bytes_and_rejects_newline_rewrite(tmp_path):
    import hashlib
    from amazon_es_bestseller.highspeed.category_mapping import load_mapping_rules
    fixture_path=Path('tests/fixtures/highspeed/v3_prior_category_plan.json')
    provenance=json.loads(fixture_path.with_suffix('.provenance.json').read_text(encoding='utf8'))
    body=fixture_path.read_bytes()
    assert provenance['immutable_copy'] is True
    assert hashlib.sha256(body).hexdigest()==provenance['original_sha256']
    assert b'\r\n' in body
    baseline=json.loads(Path('tests/fixtures/highspeed/v3_812_source_baseline.json').read_text(encoding='utf8'))
    source=next(row['source'] for row in baseline['sources'] if row['source']['actual_source_url'].endswith('/1641736031'))
    source=json.loads(json.dumps(source))
    rules=load_mapping_rules('configs/highspeed/v3/category_mapping.reviewed.json')
    assert map_category(source,rules)['category_mapping_status']=='SOURCE_CONFLICT'
    rewritten=tmp_path/'plan_lf.json'
    rewritten.write_bytes(body.replace(b'\r\n',b'\n'))
    for proof in source['mapping_evidence']:
        proof['plan_path']=str(rewritten)
    # Semantically equal JSON is insufficient: exact prior evidence bytes are
    # required and a silently converted checkout must not validate that proof.
    assert map_category(source,rules)['category_mapping_status']=='VERIFIED'



def test_unproved_original_prior_cannot_create_conflict_after_projection():
    source={'actual_source_url':'https://www.amazon.es/gp/bestsellers/toys/unknown','research_category':None,'original_research_category':'旅行','mapping_evidence':[{'reviewed':True}]}
    from amazon_es_bestseller.highspeed.category_mapping import load_mapping_rules
    result=map_category(source,load_mapping_rules('configs/highspeed/v3/category_mapping.reviewed.json'))
    assert result['research_category']=='玩具' and not result['source_conflict']



def test_real_saved_wish_department_discovers_deep_literal_nodes_without_live_claim():
    from amazon_es_bestseller.highspeed.sources import discover_sources
    fixture=Path('tests/fixtures/highspeed/v3_real_wish_department_navigation.html')
    proof=json.loads(fixture.with_suffix('.provenance.json').read_text(encoding='utf8'))
    sources=discover_sources(fixture.read_text(encoding='utf8'),'MOST_WISHED_FOR',proof['source_url'],evidence_path=str(fixture),origin='HISTORICAL_REPLAY')
    assert len(sources)==proof['literal_source_count']==62
    assert set(proof['actual_deep_nav_sample_urls'])<={s['actual_source_url'] for s in sources}
    assert any(s['browse_node_id'] for s in sources)
    assert all(s['source_status']=='UNVERIFIED' and s['enabled'] is False for s in sources)
    assert discover_sources(fixture.read_text(encoding='utf8'),'MOST_GIFTED',proof['source_url'])==[]
