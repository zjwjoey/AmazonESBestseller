from amazon_es_bestseller.highspeed.v3_links import normalize_product_url_v3
from amazon_es_bestseller.highspeed.links import normalize_product_url


def test_real_zg_ref_session_suffix_removed_but_raw_preserved():
    raw='/Super-Sparrow-Botella-Inoxidable-est%C3%A1ndar/dp/B07XLXRWB9/ref=zg_bs_g_2928471031_d_sccl_1/524-6514838-9761305?psc=1'
    v3=normalize_product_url_v3(raw)
    assert v3['raw_product_url']==raw
    assert v3['normalized_product_url']=='https://www.amazon.es/Super-Sparrow-Botella-Inoxidable-est%C3%A1ndar/dp/B07XLXRWB9?psc=1'
    assert '/ref=' in normalize_product_url(raw)['normalized_product_url']


def test_unknown_query_and_variant_are_preserved_distinct():
    a=normalize_product_url_v3('/dp/Opaque?color=red&foo=1')
    b=normalize_product_url_v3('/dp/Opaque?color=blue&foo=1')
    assert a['normalized_product_url']!=b['normalized_product_url']
    assert a['dedup_status']=='POSSIBLE_DUPLICATE'
    assert a['unknown_query_parameters']==['foo']


def test_ambiguous_ref_middle_preserved():
    raw='/dp/Opaque/ref=some_identity/red'
    assert normalize_product_url_v3(raw)['normalized_product_url'].endswith(raw)
    assert normalize_product_url_v3(raw)['dedup_status']=='POSSIBLE_DUPLICATE'


import pytest


@pytest.mark.parametrize('suffix',[
 '/ref=unreviewed_tracking/524-6514838-9761305',
 '/ref=zg_bs_g_node_1/opaque-variant',
 '/ref=zg_bs_g_node_1/524-6514838-9761305/color/red',
 '/ref%3Dzg_bs_g_node_1/524-6514838-9761305'])
def test_unreviewed_or_midpath_suffix_not_removed(suffix):
    raw='/dp/Opaque'+suffix
    assert normalize_product_url_v3(raw)['normalized_product_url'].endswith(raw)
    assert normalize_product_url_v3(raw)['dedup_status']=='POSSIBLE_DUPLICATE'


def test_tracking_aliases_share_hash_without_asin_construction():
    one=normalize_product_url_v3('/Named/dp/opaque-id/ref=zg_bs_g_123_1/524-6514838-9761305?psc=1&tag=x')
    two=normalize_product_url_v3('/Named/dp/opaque-id/ref=zg_bsnr_g_456_4/111-2222222-3333333?psc=1')
    assert one['candidate_id']==two['candidate_id']
    assert '/Named/dp/opaque-id' in one['normalized_product_url']


def test_encoded_path_and_unknown_query_order_not_reencoded():
    raw='/T%C3%ADtulo%2Fsegmento/dp/%6Fpaque?x=%2F&color=rojo&x=2#choice'
    r=normalize_product_url_v3(raw)
    assert r['normalized_product_url'].endswith(raw)
    assert r['dedup_status']=='POSSIBLE_DUPLICATE'
    assert r['unknown_query_parameters']==['x']


def test_identity_parameters_never_dropped_or_coarse_group_merged():
    urls=['/dp/Opaque?th=1&color=red','/dp/Opaque?th=2&color=red','/dp/Opaque?color=blue','/dp/Opaque/variation/red','/dp/Opaque/variation/blue']
    parsed=[normalize_product_url_v3(u) for u in urls]
    assert len({r['candidate_id'] for r in parsed})==len(urls)
    assert len({r['possible_duplicate_key'] for r in parsed})==1


def test_product_context_id_mismatch_and_opaque_contentid_retained():
    r=normalize_product_url_v3('/dp/Opaque?pd_rd_i=Other&content-id=variation-blue&pd_rd_w=tracking')
    assert r['normalized_product_url'].endswith('/dp/Opaque?pd_rd_i=Other&content-id=variation-blue')
    assert r['dedup_status']=='POSSIBLE_DUPLICATE'


@pytest.mark.parametrize('raw',['https://evil.test/dp/Opaque','javascript:/dp/Opaque','/music/player/albums/x','/dp/Opaque bad','https://www.amazon.es:999/dp/Opaque'])
def test_existing_safety_validation_preserved(raw):
    assert not normalize_product_url_v3(raw)['is_valid']



def test_real_tracking_href_fixture_with_original_capture_provenance():
    import json
    from pathlib import Path
    data=json.loads(Path('tests/fixtures/highspeed/v3_real_tracking_links.json').read_text(encoding='utf8'))
    assert data['provenance']['source_url_count']==30026
    assert len(data['examples'])>=30
    for row in data['examples']:
        n=normalize_product_url_v3(row['raw_product_url'])
        assert n['raw_product_url']==row['raw_product_url']
        assert n['normalized_product_url']==row['expected_v3_url']
        assert '/ref=' not in n['normalized_product_url']
        assert n['normalized_product_url'].endswith('?psc=1')
        assert row['evidence_sha256'] and row['observation_id']


def test_valid_bare_session_only_exact_route_position():
    a=normalize_product_url_v3('/dp/Opaque/524-6514838-9761305')
    b=normalize_product_url_v3('/dp/Opaque/color/red/524-6514838-9761305')
    assert a['normalized_product_url'].endswith('/dp/Opaque')
    assert b['normalized_product_url'].endswith('/color/red/524-6514838-9761305')
