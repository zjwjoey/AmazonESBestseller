from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.contracts import new_plan
from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION


def test_v3_store_exact_identity_queries_and_tracking_resume(tmp_path):
    store = RunStore(tmp_path / 'store')
    plan = new_plan('v3', sources=[{'source_id':'source','source_type':'BEST_SELLERS',
        'page_urls':['https://www.amazon.es/gp/bestsellers/kitchen/1']}])
    plan.update(snapshot_contract_version=3, normalizer_version=NORMALIZER_VERSION)
    store.begin_run(plan, started_at='2026-10-14T00:00:00+00:00')
    page={'source_id':'source','source_type':'BEST_SELLERS','page_url':plan['sources'][0]['page_urls'][0],
        'access_status':'NORMAL','source_type_verified':True,'source_context_valid':True,
        'observed_at':'2026-10-14T00:00:00+00:00'}
    urls=['/dp/opaque/ref=zg_bs_1/123-1234567-1234567?psc=1&th=1',
        '/dp/opaque/ref=zg_bs_2/234-1234567-1234567?psc=1&th=1',
        '/dp/opaque?psc=1&th=2', '/dp/opaque?psc=1&th=2&unknown=a',
        '/dp/opaque?psc=1&th=2&unknown=b']
    rows=[{'observation_id':f'obs{i}','raw_product_url':url,'slot_position':i} for i,url in enumerate(urls)]
    store.commit_page('v3','page',page,rows)
    assert store.candidate_count('v3') == 4
    assert store.normalized_candidate_count('v3') == 4
    assert {r['raw_product_url'] for r in store.iter_observations('v3')} == set(urls)
    assert all(r['current_normalizer_version']==NORMALIZER_VERSION for r in store.iter_candidates('v3'))
    store.recover('v3')
    assert store.summary('v3')['raw_count']==5
    assert store.candidate_count('v3')==4
    assert store.summary('v3')['possible_duplicates']==4
    store.close()
