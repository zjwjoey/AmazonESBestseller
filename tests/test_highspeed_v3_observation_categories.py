import copy,hashlib
from pathlib import Path
from collections import OrderedDict,Counter
import pytest
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.category_mapping import load_mapping_rules,map_observation_category,map_category


def real():
    path=Path('tests/fixtures/highspeed/real_v3_new_releases_root.html').resolve()
    source={'source_id':'new','source_type':'NEW_RELEASES','actual_source_url':'https://www.amazon.es/gp/new-releases','parser_profile':'V3'}
    parsed=parse_page(path.read_text(encoding='utf8'),source=source,run_id='r',page_url=source['actual_source_url'],page_number=1,observed_at='2026-10-14T00:00:00Z',evidence_path=str(path))
    return path,source,parsed['observations'],load_mapping_rules('configs/highspeed/v3/category_mapping.reviewed.json')


def apply(row,path,source,rules,cache=None):
    return map_observation_category(row,source,rules,parent_html_path=path,parent_html_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),cache=cache)


def test_real_36_card_root_maps_each_actual_carousel_not_whole_parent():
    path,source,rows,rules=real();assert len(rows)==36
    assert map_category(source,rules)['research_category'] is None
    cache=OrderedDict();mapped=[apply(r,path,source,rules,cache) for r in rows]
    assert len(cache)==1
    assert len({r['rank_context'] for r in rows})>1
    assert len({m['research_category'] for m in mapped if m['research_category']})>1
    assert all(m['category_mapping_parent_source_url']==source['actual_source_url'] for m in mapped)
    assert all(m['category_mapping_rank_context']==r['rank_context'] for r,m in zip(rows,mapped))
    assert all(m['category_mapping_context_evidence']['card_raw_product_url']==r['raw_product_url'] for r,m in zip(rows,mapped))


@pytest.mark.parametrize('field,value',[('representation_slot',35),('rank_context','https://www.amazon.es/gp/new-releases/toys'),('raw_product_url','/dp/neighbor'),('source_id','neighbor'),('evidence_sha256','0'*64)])
def test_neighbor_or_unbound_parent_context_rejected(field,value):
    path,source,rows,rules=real();row=copy.deepcopy(rows[0]);row[field]=value
    with pytest.raises(ValueError):apply(row,path,source,rules)


def test_unknown_department_context_is_not_arbitrarily_filled():
    path,source,rows,rules=real()
    unknown=[r for r in rows if '/hpc' in r['rank_context'] or '/kitchen' in r['rank_context']]
    assert unknown
    assert all(apply(r,path,source,rules)['research_category'] is None for r in unknown)


def test_acp_context_dictionary_alone_does_not_map_child():
    path,source,rows,rules=real();row=copy.deepcopy(rows[0]);row['representation']='ACP_0'
    row['category_scope_evidence']={'ranking_context_url':row['rank_context'],'source_id':'new','page_evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(ValueError,match='CAPTURE_REFERENCE_UNVERIFIED'):apply(row,path,source,rules)


def test_parent_type_or_byte_tamper_is_rejected(tmp_path):
    path,source,rows,rules=real();bad=tmp_path/'bad.html';bad.write_text('<h1>Los más vendidos</h1>',encoding='utf8')
    with pytest.raises(ValueError,match='PARENT_TYPE_OR_CONTEXT'):apply(rows[0],bad,source,rules)



def acp_protocol(tmp_path):
    # Protocol mock with real saved parent DOM; not a LIVE native response.
    import json
    from bs4 import BeautifulSoup
    from amazon_es_bestseller.highspeed.adapters import _cards
    from test_highspeed_v3_native_authorization import carousel
    endpoint,descriptor,_,_=carousel(tmp_path)
    path,source,rows,rules=real();row=copy.deepcopy(rows[0])
    fragment=tmp_path/'fragment.html'
    fragment.write_text(str(_cards(BeautifulSoup(path.read_bytes(),'lxml'))[0]),encoding='utf8')
    row.update(representation='ACP_0',representation_slot=0,evidence_path=str(fragment),evidence_sha256=hashlib.sha256(fragment.read_bytes()).hexdigest(),capture_run_id='r')
    receipt={'kind':'ACP','run_id':'r','source_id':'new','request_url':endpoint,'native_acp_descriptor':descriptor,'ranking_context_binding':descriptor['ranking_context_binding'],'html_path':str(fragment),'html_sha256':row['evidence_sha256']}
    receipt_path=tmp_path/'response.json';receipt_path.write_text(json.dumps(receipt),encoding='utf8')
    row['capture_receipt_reference']={'path':str(receipt_path),'sha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest()}
    return path,source,row,rules,receipt_path,receipt


def test_acp_protocol_owner_and_parent_sha_are_independently_verified(tmp_path):
    path,source,row,rules,_,_=acp_protocol(tmp_path)
    mapped=apply(row,path,source,rules)
    # First real carousel is a mixed department: owner verification does not
    # invent a category. It must match the corresponding SERVER decision.
    _,_,server_rows,_=real()
    expected=apply(server_rows[0],path,source,rules)
    assert mapped['research_category']==expected['research_category']
    assert mapped['category_mapping_status']==expected['category_mapping_status']
    assert mapped['category_mapping_context_evidence']['kind']=='VERIFIED_NATIVE_ACP_OWNER_CATEGORY_CONTEXT'


@pytest.mark.parametrize('field,value',[('source_id','neighbor'),('capture_run_id','prior-run'),('rank_context','https://www.amazon.es/gp/new-releases/toys')])
def test_acp_foreign_source_owner_or_epoch_cannot_fill_category(tmp_path,field,value):
    path,source,row,rules,_,_=acp_protocol(tmp_path);row[field]=value
    with pytest.raises(ValueError):apply(row,path,source,rules)


def test_acp_forged_receipt_hash_is_rejected(tmp_path):
    path,source,row,rules,_,_=acp_protocol(tmp_path)
    row['capture_receipt_reference']['sha256']='0'*64
    with pytest.raises(ValueError,match='CAPTURE_REFERENCE_UNVERIFIED'):apply(row,path,source,rules)



def test_real_root_coverage_counts_verified_card_contexts_not_fake_child_pages():
    from amazon_es_bestseller.highspeed.sources import detailed_coverage_matrix
    path,source,rows,rules=real();cache={}
    mapped=[{**r,**apply(r,path,source,rules,cache), 'target_group_id':r['normalized_product_url'],'link_valid':r['is_valid']} for r in rows]
    parsed=parse_page(path.read_text(encoding='utf8'),source=source,run_id='r',page_url=source['actual_source_url'],page_number=1,observed_at='2026-10-14T00:00:00Z',evidence_path=str(path))
    matrix=detailed_coverage_matrix([source],[parsed['page']],mapped)
    assert len(matrix)==75
    assert sum(cell['extracted_observation_count'] for cell in matrix)>0
    assert any(cell['verified_card_owner_context_count']>0 for cell in matrix)
    assert all(cell['completed_page_count']==0 for cell in matrix)
    assert all(cell['current_availability_verified'] is False for cell in matrix)
    assert all(cell['source_and_page_counts_across_categories_non_additive'] for cell in matrix)
    assert sum(cell['extracted_observation_count'] for cell in matrix)==sum(r['research_category'] is not None for r in mapped)


def test_unverified_fake_root_category_never_counts_as_verified_source():
    from amazon_es_bestseller.highspeed.sources import detailed_coverage_matrix
    path,source,rows,rules=real();row=copy.deepcopy(rows[0]);row.update(research_category='玩具',category_mapping_status='VERIFIED')
    matrix=detailed_coverage_matrix([source],[],[row])
    cell=next(c for c in matrix if c['source_type']=='NEW_RELEASES' and c['research_category']=='玩具')
    assert cell['verified_source_count']==cell['verified_card_owner_context_count']==0
    assert cell['extracted_observation_count']==0
