from pathlib import Path
import gzip,json,hashlib
from bs4 import BeautifulSoup
import pytest
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page,VERSION

def fixture():
    d=Path(__file__).parent/'fixtures/highspeed_v4/cml_dossier';p=json.loads((d/'provenance.json').read_text('utf8'))
    html=gzip.decompress((d/'actual_health.html.gz').read_bytes());assert hashlib.sha256(html).hexdigest()==p['receipt']['sha256']
    return html.decode('utf8'),p
def parse(html,p,evidence_path='TEST_ONLY_saved_actual_health',**kw):
    r=p['receipt'];return parse_v4_page(html,source=p['source'],page_url=r['request_url'],run_id=r['run_id'],
        observed_at=r['observed_at'],evidence_path=evidence_path,http_status=200,origin='OFFLINE_FIXTURE',**kw)

def test_actual_health_grid_has_twenty_owned_products():
    html,p=fixture();x=parse(html,p)
    assert x['page']['status']=='VERIFIED_WITH_PRODUCTS' and len(x['observations'])==20
    assert all(v['product_title_es'] and v['raw_product_url'] for v in x['observations'])
    assert 'Plackers' in x['observations'][0]['product_title_es']
    assert x['page']['parser_version']=='multisource_v4.5.cml_dossier'

def test_legacy_profile_does_not_rewrite_old_zero_card_results():
    html,p=fixture();assert parse(html,p,parser_profile_version=VERSION)['observations']==[]

def test_generic_grid_without_cml_heading_is_not_a_favorites_source():
    html,p=fixture();html=html.replace('Los favoritos de los clientes','Unrelated source')
    assert not parse(html,p)['observations']

def test_foreign_and_history_grids_do_not_enter_owned_result():
    html,p=fixture();s=BeautifulSoup(html,'lxml');grid=s.select_one('.dcl-html-grid')
    history=BeautifulSoup('<div id="rhf">'+str(grid)+'</div>','lxml').div;s.body.append(history)
    assert len(parse(str(s),p)['observations'])==20

def test_new_profile_independent_central_dom_roundtrip(tmp_path):
    from amazon_es_bestseller.highspeed.v4_quality import audit_v4
    html,p=fixture();path=tmp_path/'actual.html';path.write_bytes(html.encode('utf8'))
    x=parse(html,p,evidence_path=str(path))
    x['page']['run_id']=p['receipt']['run_id']
    quality=audit_v4([x['page']],x['observations'],[p['source']],require_live=False)
    assert quality['central_full_dom_review_passed'] and quality['errors']==[],quality['errors']

def test_wrong_theme_does_not_upgrade_generic_dossier_grid():
    html,p=fixture();p['source']['navigation_evidence']['label_raw']='Wrong theme'
    assert parse(html,p)['observations']==[]
