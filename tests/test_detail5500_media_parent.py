import ast,gzip,hashlib,json,re
from pathlib import Path
from bs4 import BeautifulSoup
import pytest
from amazon_es_bestseller.collection.detail_bridge5500 import parse_candidate_detail
F=Path(__file__).parent/'fixtures/detail_bridge5500'
def evidence():
 p=json.loads((F/'actual_media_child_parent_canonical.provenance.json').read_text());b=gzip.decompress((F/'actual_media_child_parent_canonical.html.gz').read_bytes());assert hashlib.sha256(b).hexdigest()==p['sha256'];return b.decode('utf8'),p['task'],p['metadata']
def test_real_empty_twister_owned_media_confirms_requested_child_and_parent():
 h,t,m=evidence();r=parse_candidate_detail(t,h,m)
 assert r['status']=='SUCCESS'
 assert r['record']['asin']=='B0HG8VF2PP' and r['record']['parent_asin']=='B0H264Q787'
 assert r['record']['parent_asin_evidence']['kind']=='OWNED_MEDIA_BLOCK_PARENT_STATE'
 assert r['record']['active_child_canonical_parent_evidence']['rule']=='OWNED_EMPTY_TWISTER_MEDIABLOCK_CHILD_CANONICAL_PARENT_V1'
 assert r['record']['title_es_raw'].startswith('Baby Rocker Manos Libres')
@pytest.mark.parametrize('bad',['media','parent','owner','owner_asin','owner_feature','buybox','twister_not_empty','twister_asin','missing_twister','canonical','extra_canonical','extra_owner','classic_conflict','duplicate_parent','wrong_final','http403','http429','challenge'])
def test_media_parent_exception_rejects_conflicting_or_unowned_evidence(bad):
 h,t,m=evidence();s=BeautifulSoup(h,'lxml');o=s.select_one('#mediaBlockVariations_feature_div');script=o.select_one('script');text=script.get_text()
 if bad in {'media','parent','duplicate_parent'}:
  pattern=r"jQuery\.parseJSON\(\s*('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")\s*\)";match=re.search(pattern,text);d=json.loads(ast.literal_eval(match.group(1)))
  if bad=='duplicate_parent':payload=json.dumps(d,ensure_ascii=True)[:-1]+',"parentAsin":"B000000001"}'
  else:d['mediaAsin' if bad=='media' else 'parentAsin']='B000000001';payload=json.dumps(d,ensure_ascii=True)
  literal="'"+payload.replace('\\','\\\\').replace("'","\\'")+"'";script.string=text[:match.start(1)]+literal+text[match.end(1):]
 elif bad=='owner':o['id']='recommendations'
 elif bad=='owner_asin':o['data-csa-c-asin']='B000000001'
 elif bad=='owner_feature':o['data-feature-name']='recommendations'
 elif bad=='buybox':s.select_one('#ppd #buybox input#ASIN')['value']='B000000001'
 elif bad=='twister_not_empty':s.select_one('#twister_feature_div').append(BeautifulSoup('<input name=variation value=x>','lxml').select_one('input'))
 elif bad=='twister_asin':s.select_one('#twister_feature_div')['data-csa-c-asin']='B000000001'
 elif bad=='missing_twister':s.select_one('#twister_feature_div')['id']='other'
 elif bad=='canonical':s.select_one('link[rel=canonical]')['href']='https://www.amazon.es/dp/B000000001'
 elif bad=='extra_canonical':s.head.append(BeautifulSoup('<link rel=canonical href="https://www.amazon.es/dp/B000000001">','lxml').select_one('link'))
 elif bad=='extra_owner':
  import copy
  o.parent.append(copy.deepcopy(o))
 elif bad=='classic_conflict':s.body.append(BeautifulSoup('<script>P.register("twister-js-init-dpx-data",function(){return {"currentAsin":"B000000001"};});</script>','lxml').select_one('script'))
 elif bad=='wrong_final':m={**m,'final_url':'https://www.amazon.es/dp/B000000001'}
 elif bad in {'http403','http429'}:m={**m,'status_code':int(bad[4:])}
 elif bad=='challenge':s.body.append(BeautifulSoup('<form action="/errors_page/validateCaptcha"></form>','lxml').select_one('form'))
 assert parse_candidate_detail(t,str(s),m)['status']!='SUCCESS'
