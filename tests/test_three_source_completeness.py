"""Actual page-two metadata declares 51--100, not an inferred 1--50 window."""
from pathlib import Path
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from amazon_es_bestseller.highspeed.v4_sources import source_for_url,SEEDS
from amazon_es_bestseller.highspeed.three_report import completeness_three


def test_actual_page_two_metadata_preserves_missing_native_slots():
    path=Path(__file__).parent/'fixtures/highspeed/night5000_page2_missing_acp_path.html'
    url='https://www.amazon.es/gp/bestsellers/kitchen/2822691031?ie=UTF8&pg=2'
    source=source_for_url(SEEDS[0]['url']);source.update(actual_source_url=url,page_urls=[url])
    parsed=parse_v4_page(path.read_bytes().decode('utf8'),source=source,page_url=url,run_id='TEST_ONLY',
        observed_at='2026-10-15T01:00:00Z',evidence_path=str(path),http_status=200,origin='OFFLINE_FIXTURE')
    page=parsed['page'];page['execution_status']='APPROVED_SOURCE_PAGE_SCOPE_EXECUTED'
    result=completeness_three([page],[source],{'finite_component_coverage':[]},observations=parsed['observations'])
    component=result['pages'][0]['finite_component_coverage'][0]
    assert component['expected_metadata_ranks']==list(range(51,101))
    assert component['missing_ranks']==list(range(81,101))
    assert component['outside_metadata_ranks']==[] and component['status']=='PARTIAL'
    assert result['pages'][0]['status']=='PARTIAL' and result['global_source_completeness']=='UNKNOWN'
