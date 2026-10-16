import gzip,json,hashlib
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from amazon_es_bestseller.collection.detail_bridge5500 import parse_candidate_detail
F=Path(__file__).parent/'fixtures/detail_bridge5500'
def evidence():
 raw=gzip.decompress((F/'actual_child_parent_canonical.html.gz').read_bytes());p=json.loads((F/'actual_child_parent_canonical.provenance.json').read_text());assert hashlib.sha256(raw).hexdigest()==p['sha256'];return raw.decode('utf8'),p['task'],p['metadata']
def test_real_selected_child_with_explicit_owned_parent_canonical():
 h,t,m=evidence();r=parse_candidate_detail(t,h,m);assert r['status']=='SUCCESS';v=r['record'];assert v['asin']=='B0H9NWFLHB' and v['parent_asin']=='B0H9NR1N85';assert v['active_child_canonical_parent_evidence']['current_asin']==v['asin'];assert v['canonical_urls'][0].endswith('/dp/B0H9NR1N85')
@pytest.mark.parametrize('bad',['current','landing','parent','missing_owned','canonical','active','wrong_requested','challenge','http403','missing_buybox','missing_display','conflicting_parent'])
def test_conflicting_or_unowned_parent_is_not_accepted(bad):
 h,t,m=evidence();s=BeautifulSoup(h,'lxml');owned=s.select_one('#twister_feature_div');script=next(e for e in owned.select('script') if "P.register('twister-js-init-dpx-data'" in e.get_text())
 if bad in {'current','landing','parent'}:
  import re
  key={'current':'currentAsin','landing':'landingAsin','parent':'parentAsin'}[bad];script.string=re.sub(r'("'+key+r'"\s*:\s*")[A-Z0-9]{10}',r'\g<1>B000000001',script.get_text())
 elif bad=='missing_owned':owned['id']='recommendations'
 elif bad=='canonical':s.select_one('link[rel=canonical]')['href']='https://www.amazon.es/dp/B000000001'
 elif bad=='active':s.select_one('input#ASIN')['value']='B000000001'
 elif bad=='wrong_requested':t={**t,'request_url':'https://www.amazon.es/dp/B000000001'}
 elif bad=='challenge':s.append(BeautifulSoup('<h1>Robot Check</h1>','lxml'))
 elif bad=='http403':m={**m,'status_code':403}
 elif bad=='missing_buybox':s.select_one('#buybox')['id']='recommendationBuybox'
 elif bad=='missing_display':script.string=script.get_text().replace('dimensionValuesDisplayData','unverifiedDisplay')
 elif bad=='conflicting_parent':s.select_one('#ppd').append(BeautifulSoup('<input id=parentASIN value=B000000001>','lxml'))
 assert parse_candidate_detail(t,str(s),m)['status']!='SUCCESS'

@pytest.mark.parametrize('name',['../authorization.bad.approved.json','other.json','authorization.bad.json','/authorization.bad.approved.json'])
def test_auth_revision_cannot_escape_run(tmp_path,monkeypatch,name):
 from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
 b=DetailBridge5500(tmp_path,{'tasks':[],'run_id':'test'})
 monkeypatch.setattr(b,'validate_authorization',lambda a:None)
 with pytest.raises(ValueError,match='AUTHORIZATION_REVISION_FILENAME_INVALID'):b.run(None,{},until_completed=0,authorization_filename=name)
def test_auth_revision_keeps_original_immutable_file(tmp_path,monkeypatch):
 from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
 from amazon_es_bestseller.multisource.storage import immutable_json
 old=tmp_path/'authorization.approved.json';immutable_json(old,{'original':True});before=old.read_bytes()
 b=DetailBridge5500(tmp_path,{'tasks':[],'run_id':'test','shared_gate_path':str(tmp_path/'gate.json')})
 monkeypatch.setattr(b,'validate_authorization',lambda a:None)
 b.run(None,{'revision':True},until_completed=0,authorization_filename='authorization.identity_parent.approved.json')
 assert old.read_bytes()==before and json.loads((tmp_path/'authorization.identity_parent.approved.json').read_text())=={'revision':True}
