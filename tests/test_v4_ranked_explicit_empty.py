from pathlib import Path
import json,gzip,pytest
from bs4 import BeautifulSoup
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page,VERSION

def fixture():
 d=Path(__file__).parent/'fixtures/highspeed_v4/ranked_explicit_empty';return gzip.decompress((d/'actual_new_empty.html.gz').read_bytes()).decode('utf8'),json.loads((d/'provenance.json').read_text('utf8'))
def parse(h,p,evidence_path='saved_actual_empty',**kw):
 r=p['receipt'];return parse_v4_page(h,source=p['source'],page_url=r['request_url'],run_id=r['run_id'],observed_at=r['observed_at'],evidence_path=evidence_path,http_status=200,**kw)
def test_actual_official_empty_not_native_loading():
 h,p=fixture();x=parse(h,p);assert x['page']['status']=='EXPLICIT_EMPTY';assert x['page']['completeness_status']=='UNKNOWN';assert not x['observations'];assert not x['acp_pending'];assert x['page']['parser_version']=='multisource_v4.7.ranked_empty'
def test_old_profile_retains_unresolved_frame():
 h,p=fixture();x=parse(h,p,parser_profile_version=VERSION);assert x['page']['status']!='EXPLICIT_EMPTY';assert x['acp_pending']
@pytest.mark.parametrize('change',['statement','end_marker','nonempty_metadata','product'])
def test_empty_guards_fail_closed(change):
 h,p=fixture();s=BeautifulSoup(h,'lxml');g=s.select_one('#zg-right-col .p13n-desktop-grid')
 if change=='statement':g.h4.decompose()
 if change=='end_marker':g.select_one('#endOfList').decompose()
 if change=='nonempty_metadata':g['data-client-recs-list']='[{"id":"B012345678"}]'
 if change=='product':g.append(BeautifulSoup('<a href="/dp/B012345678">Actual product</a>','lxml').a)
 assert parse(str(s),p)['page']['status']!='EXPLICIT_EMPTY'
def test_empty_central_dom_rechecks_actual_source(tmp_path):
 from amazon_es_bestseller.highspeed.v4_quality import audit_v4
 h,p=fixture();path=tmp_path/'empty.html';path.write_bytes(h.encode('utf8'));x=parse(h,p,evidence_path=str(path));x['page']['run_id']=p['receipt']['run_id'];q=audit_v4([x['page']],[],[p['source']],require_live=False);assert q['central_full_dom_review_passed'],q['errors']
