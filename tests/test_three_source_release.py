from pathlib import Path
from bs4 import BeautifulSoup
from amazon_es_bestseller.highspeed.three_release import classify_card,release_signals
from amazon_es_bestseller.highspeed.v4_sources import source_for_url,SEEDS
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
import pytest


def test_real_page_future_release_banner_is_not_an_individual_product_claim():
    path=Path(__file__).parent/'fixtures/highspeed_v4/F02_new_root_complete.html'
    source=source_for_url(SEEDS[1]['url']);source['page_urls']=[source['actual_source_url']]
    p=parse_v4_page(path.read_text('utf8'),source=source,page_url=source['actual_source_url'],run_id='TEST_ONLY',
        observed_at='2026-10-15T01:00:00Z',evidence_path=str(path),http_status=200,origin='OFFLINE_FIXTURE')
    assert 'futuros lanzamientos' in path.read_text('utf8')
    values=release_signals(p['observations'])
    assert values and all(v['release_status'] in {'NOT_DISPLAYED','UNVERIFIED_CARD_BINDING'} for v in values.values())


@pytest.mark.parametrize('html,status',[('<span>Disponible el 20 de noviembre de 2026</span>','FUTURE_RELEASE_VISIBLE'),
    ('<span>Reservar ahora</span>','PREORDER_VISIBLE'),('<span>En stock</span>','AVAILABLE_NOW_VISIBLE'),
    ('<span>Disponible el 1 de enero de 2026</span>','SCHEDULED_DATE_NOT_CONFIRMED_FUTURE'),
    ('<a href="/dp/TEST_ONLY"><span>Reservar ahora</span></a>','NOT_DISPLAYED'),
    ('<span>En stock</span><span>Disponible el 20 de noviembre de 2026</span>','SOURCE_CONFLICT')])
def test_test_only_availability_snippets_stay_card_bound(html,status):
    # Synthetic availability spans test the pure classifier, not product supply.
    card=BeautifulSoup('<div>'+html+'</div>','lxml').div
    assert classify_card(card,'2026-10-15T01:00:00Z')['release_status']==status
