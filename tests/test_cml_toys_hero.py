from pathlib import Path
import gzip,json,pytest
from bs4 import BeautifulSoup
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page

def fixture():
 d=Path(__file__).parent/'fixtures/highspeed_v4/cml_toys_hero';return gzip.decompress((d/'actual_toys.html.gz').read_bytes()).decode('utf8'),json.loads((d/'provenance.json').read_text('utf8'))
def parse(h,p,evidence_path='actual_saved_toys',**kw):
 r=p['receipt'];return parse_v4_page(h,source=p['source'],page_url=r['request_url'],run_id=r['run_id'],observed_at=r['observed_at'],evidence_path=evidence_path,http_status=200,**kw)
def test_actual_toys_owned_hero_and_twenty_cards():
 h,p=fixture();x=parse(h,p);assert len(x['observations'])==19;assert x['page']['parser_version']=='multisource_v4.6.cml_hero'
def test_old_version_does_not_rewrite_toys():
 h,p=fixture()
 with pytest.raises(ValueError,match='V4_EXACT_CML_DOSSIER_CONTEXT_REQUIRED'):parse(h,p,parser_profile_version='multisource_v4.5.cml_dossier')
def test_navbar_theme_cannot_replace_owned_hero():
 h,p=fixture();s=BeautifulSoup(h,'lxml')
 for n in s.select('.template-renderer-wrapper #headline'):n.decompose()
 assert not parse(str(s),p)['observations']
def test_wrong_theme_rejected():
 h,p=fixture();p['source']['navigation_evidence']['label_raw']='Wrong theme';assert not parse(h,p)['observations']

def test_new_hero_central_dom_roundtrip(tmp_path):
 from amazon_es_bestseller.highspeed.v4_quality import audit_v4
 h,p=fixture();path=tmp_path/'actual.html';path.write_bytes(h.encode('utf8'));x=parse(h,p,evidence_path=str(path))
 x['page']['evidence_path']=str(path);x['page']['run_id']=p['receipt']['run_id']
 for row in x['observations']:row['evidence_path']=str(path)
 q=audit_v4([x['page']],x['observations'],[p['source']],require_live=False)
 assert q['central_full_dom_review_passed'] and not q['errors'],q['errors']
