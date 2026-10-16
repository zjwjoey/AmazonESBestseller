"""Real failed product fixture; this path never accepts its different child."""
import gzip,hashlib,json,re
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from amazon_es_bestseller.collection import identity_quarantine5500 as mod
from amazon_es_bestseller.collection.detail_bridge5500 import parse_candidate_detail
from amazon_es_bestseller.multisource.storage import digest,immutable_json,sealed
from amazon_es_bestseller.collection.quick_selection import file_sha

F=Path(__file__).parent/'fixtures/detail_bridge5500'
def evidence():
 p=json.loads((F/'actual_changed_child_parent_canonical.provenance.json').read_text())
 raw=gzip.decompress((F/'actual_changed_child_parent_canonical.html.gz').read_bytes());assert hashlib.sha256(raw).hexdigest()==p['sha256']
 return raw.decode('utf8'),p['task'],p['metadata']
def proof():
 return {'rule':'OWNED_CURRENT_CHILD_DIFFERS_REQUESTED_CANONICAL_EXPLICIT_PARENT_V1','requested_asin':'B0H7B9JJ5B','current_asin':'B0H79ZHJLT','parent_asin':'B0HBTTH7J2','landing_asin':'B0H7B9JJ5B','source':'#ppd #twister_feature_div / twister-js-init-dpx-data','buybox_input':'#ppd #buybox input#ASIN[name=ASIN]'}
def review(tmp_path,html,task,meta,declared=None):
 replay=parse_candidate_detail(task,html,meta)
 assert replay['status']=='IDENTITY_REVIEW'  # Never turn another child into success.
 hp=tmp_path/'body.html';hp.write_text(html,encoding='utf8');mp=tmp_path/'meta.json';immutable_json(mp,meta)
 rp=tmp_path/'receipt.json';immutable_json(rp,sealed({'state':'NORMAL','status_code':200,'request_role':'DETAIL','request_url':task['request_url'],'run_id':task['run_id'],'task_id':task['task_id'],'html_sha256':file_sha(hp)}))
 cp=tmp_path/'checkpoint.json';immutable_json(cp,sealed({**replay,'task_id':task['task_id'],'run_id':task['run_id'],'receipt_path':str(rp),'receipt_sha256':file_sha(rp),'html_path':str(hp),'html_sha256':file_sha(hp),'response_metadata_path':str(mp),'response_metadata_sha256':file_sha(mp)}))
 plan={'run_id':task['run_id'],'tasks':[task]}
 item={'task_id':task['task_id'],'checkpoint_reference':{'path':str(cp),'sha256':file_sha(cp)},'receipt_reference':{'path':str(rp),'sha256':file_sha(rp)},'HTML_reference':{'path':str(hp),'sha256':file_sha(hp)},'metadata_reference':{'path':str(mp),'sha256':file_sha(mp)},'owned_child_parent_change_evidence':proof() if declared is None else declared}
 if declared is False:item.pop('owned_child_parent_change_evidence')
 qp=tmp_path/'review.json';immutable_json(qp,{'approved':True,'approved_by':'agent_under_explicit_user_delegation','scope':'ISOLATED_ACTUAL_CHILD_CHANGE_NOT_A_SUCCESS','run_id':plan['run_id'],'plan_sha256':digest(plan),'no_replacement':True,'tasks':[item]})
 before=cp.read_bytes();q=mod.reviewed_identity_quarantines(tmp_path,plan,{'reviewed_identity_quarantines_reference':{'path':str(qp),'sha256':file_sha(qp)}})
 assert cp.read_bytes()==before
 return q

def test_real_owned_changed_child_parent_is_only_reviewed_failure(tmp_path):
 h,t,m=evidence();r=parse_candidate_detail(t,h,m)
 assert r=={'status':'IDENTITY_REVIEW','reason':'ACTIVE_PRODUCT_ASIN_NOT_UNAMBIGUOUS','observed_active_asins':['B0H79ZHJLT','B0HBTTH7J2']}
 q=review(tmp_path,h,t,m)
 assert list(q)==[t['task_id']] and q[t['task_id']]['not_success'] is True
 assert q[t['task_id']]['reason']=='ACTIVE_PRODUCT_ASIN_NOT_UNAMBIGUOUS'

@pytest.mark.parametrize('bad',['current','landing','parent','canonical','buybox','owner','display','extra_active','extra_canonical','challenge','403','declaration','missing_declaration'])
def test_unproved_or_restricted_conflicts_remain_blocked(tmp_path,bad):
 h,t,m=evidence();s=BeautifulSoup(h,'lxml');owned=s.select_one('#ppd #twister_feature_div');script=next(x for x in owned.select('script') if 'twister-js-init-dpx-data' in x.get_text())
 if bad in {'current','landing','parent'}:
  key={'current':'currentAsin','landing':'landingAsin','parent':'parentAsin'}[bad];script.string=re.sub(r'("'+key+r'"\s*:\s*")[A-Z0-9]{10}',r'\g<1>B000000001',script.get_text())
 elif bad=='canonical':s.select_one('link[rel=canonical]')['href']='https://www.amazon.es/dp/B000000001'
 elif bad=='buybox':
  for x in s.select('#ppd #buybox input#ASIN'):x['id']='unowned'
 elif bad=='owner':owned['id']='recommendations'
 elif bad=='display':script.string=script.get_text().replace('dimensionValuesDisplayData','unverifiedDisplay')
 elif bad=='extra_active':s.body.append(BeautifulSoup('<input id=ASIN name=ASIN value=B000000001>','lxml').select_one('input'))
 elif bad=='extra_canonical':s.head.append(BeautifulSoup('<link rel=canonical href="https://www.amazon.es/dp/B000000001">','lxml').select_one('link'))
 elif bad=='challenge':s.body.append(BeautifulSoup('<form action="/errors_page/validateCaptcha"></form>','lxml').select_one('form'))
 elif bad=='403':m={**m,'status_code':403}
 declared={**proof(),'current_asin':'B000000001'} if bad=='declaration' else False if bad=='missing_declaration' else None
 if bad not in {'declaration','missing_declaration'}:assert mod.owned_changed_child_parent_evidence(t,str(s),m) is None
 with pytest.raises((ValueError,AssertionError)):review(tmp_path,str(s),t,m,declared)
