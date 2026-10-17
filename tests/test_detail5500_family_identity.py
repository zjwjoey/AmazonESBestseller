"""Real saved DOM; request/metadata changes below are synthetic offline cases."""
import copy,gzip,json
from pathlib import Path
import pytest
from amazon_es_bestseller.collection.detail_family_identity5500 import SCOPE,family_evidence,parse_family_detail
from amazon_es_bestseller.collection.detail_local_lanes5500 import parse_captured_detail
from amazon_es_bestseller.collection.detail_bridge5500 import parse_candidate_detail
F=Path(__file__).parent/'fixtures/detail_bridge5500'
def saved():
 p=json.loads((F/'actual_changed_child_parent_canonical.provenance.json').read_text(encoding='utf8'))
 return gzip.decompress((F/'actual_changed_child_parent_canonical.html.gz').read_bytes()).decode('utf8'),p['task'],p['metadata']
def parent_request():
 h,t,m=saved();t=copy.deepcopy(t);m=copy.deepcopy(m)
 t['request_url']='https://www.amazon.es/dp/B0HBTTH7J2';m['requested_url']=t['request_url'];m['final_url']=t['request_url']
 return h,t,m
def policy(t):return dict(approved=True,scope=SCOPE,run_id=t['run_id'],approval_reference={'path':'frozen-policy','sha256':'test-only'})
def test_local_parser_opt_in_accepts_explicit_parent_without_aliasing():
 h,t,m=parent_request();assert parse_candidate_detail(t,h,m)['status']=='IDENTITY_REVIEW'
 r=parse_captured_detail(t,h,m,True,policy(t))
 assert r['status']=='SUCCESS';r=r['record']
 assert r['asin']==r['resolved_asin']=='B0H79ZHJLT'
 assert r['requested_asin']==r['family_parent_asin']=='B0HBTTH7J2'
 assert r['source_memberships']==[] and r['requested_source_memberships']==t['source_memberships']
 assert r['source_rank_transfer_allowed'] is False

def test_landing_alone_does_not_prove_family_membership():
 h,t,m=saved();assert family_evidence(t,h,m) is None
 assert parse_family_detail(t,h,m,policy(t))==parse_candidate_detail(t,h,m)

def test_no_approval_retains_strict_result():
 h,t,m=parent_request()
 assert parse_family_detail(t,h,m,None)==parse_candidate_detail(t,h,m)

@pytest.mark.parametrize('status',[403,429,None])
def test_access_is_never_relaxed(status):
 h,t,m=parent_request();m['status_code']=status
 assert family_evidence(t,h,m) is None
 assert parse_family_detail(t,h,m,policy(t))['status']!='SUCCESS'


@pytest.mark.parametrize('bad',['current','parent','buybox','canonical','owner','extra_active','extra_canonical','challenge','metadata_restriction','wrong_request','unknown_member','missing_display'])
def test_unproved_or_restricted_family_is_rejected(bad):
 from bs4 import BeautifulSoup
 import re
 h,t,m=parent_request();s=BeautifulSoup(h,'lxml')
 owner=s.select_one('#ppd #twister_feature_div')
 script=next(x for x in owner.select('script') if 'twister-js-init-dpx-data' in x.get_text())
 if bad in {'current','parent'}:
  key={'current':'currentAsin','parent':'parentAsin'}[bad]
  script.string=re.sub(r'("'+key+r'"\s*:\s*")[A-Z0-9]{10}',r'\g<1>B000000001',script.get_text())
 elif bad=='buybox':
  for node in s.select('#ppd #buybox input#ASIN'):node['value']='B000000001'
 elif bad=='canonical':s.select_one('link[rel=canonical]')['href']='https://www.amazon.es/dp/B000000001'
 elif bad=='owner':owner['id']='recommendations'
 elif bad=='extra_active':s.body.append(BeautifulSoup('<input name=ASIN value=B000000001>','lxml').select_one('input'))
 elif bad=='extra_canonical':s.head.append(BeautifulSoup('<link rel=canonical href=https://www.amazon.es/dp/B000000001>','lxml').select_one('link'))
 elif bad=='challenge':s.body.append(BeautifulSoup('<form action=/errors_page/validateCaptcha></form>','lxml').select_one('form'))
 elif bad=='metadata_restriction':m['access_state']='CHALLENGE'
 elif bad=='wrong_request':m['requested_url']='https://www.amazon.es/dp/B000000001'
 elif bad=='unknown_member':t['request_url']='https://www.amazon.es/dp/B000000001';m['requested_url']=t['request_url']
 elif bad=='missing_display':script.string=script.get_text().replace('dimensionValuesDisplayData','missingDisplay')
 assert family_evidence(t,str(s),m) is None
 assert parse_family_detail(t,str(s),m,policy(t))['status']!='SUCCESS'


def test_explicit_visible_sibling_membership_is_allowed():
 from bs4 import BeautifulSoup
 from amazon_es_bestseller.collection.detail_v2 import _json_after
 h,t,m=saved();s=BeautifulSoup(h,'lxml')
 script=next(x.get_text() for x in s.select('#ppd #twister_feature_div script') if 'twister-js-init-dpx-data' in x.get_text())
 display=_json_after(script,'"dimensionValuesDisplayData"')
 current='B0H79ZHJLT';other=next((k for k,v in display.items() if k!=current and isinstance(v,list) and v),None)
 assert other is not None
 t['request_url']='https://www.amazon.es/dp/'+other;m['requested_url']=t['request_url'];m['final_url']=t['request_url']
 r=parse_family_detail(t,h,m,policy(t))
 assert r['status']=='SUCCESS'
 assert r['record']['family_identity_evidence']['membership_basis']=='EXPLICIT_VISIBLE_MEMBER_MAP'


def test_policy_bound_to_run_code_original_auth_and_immutable_bytes(tmp_path):
 from amazon_es_bestseller.collection.detail_family_identity5500 import read_policy
 from amazon_es_bestseller.multisource.storage import immutable_json,digest
 from amazon_es_bestseller.collection.quick_selection import file_sha
 plan={'run_id':'test','tasks':[]};base=tmp_path/'base.json';immutable_json(base,{'scope':'original'})
 ref=lambda p:{'path':str(p),'sha256':file_sha(p)}
 auth={'code_sha':'new-code','parallel_base_authorization_reference':ref(base)}
 value=dict(approved=True,scope=SCOPE,run_id='test',runtime_root=str(tmp_path.resolve()),
  approved_by='agent_under_explicit_user_delegation',explicit_user_instruction='family allowed',
  plan_sha256=digest(plan),code_sha='new-code',authorization_basis_reference=ref(base),
  preserve_requested_and_actual_ASIN=True,do_not_transfer_requested_rank=True,deduplicate_by_actual_ASIN=True)
 path=tmp_path/'policy.json';immutable_json(path,value);auth['family_identity_policy_reference']=ref(path)
 assert read_policy(tmp_path,plan,auth)['approved'] is True
 for key in ['code_sha','run_id','plan_sha256','runtime_root','scope','authorization_basis_reference']:
  wrong={**value,key:'wrong'};path.write_text(json.dumps(wrong),encoding='utf8');auth['family_identity_policy_reference']=ref(path)
  with pytest.raises(ValueError):read_policy(tmp_path,plan,auth)
 path.write_text(json.dumps(value),encoding='utf8');auth['family_identity_policy_reference']=ref(path)
 path.write_text('{}',encoding='utf8')
 with pytest.raises(ValueError):read_policy(tmp_path,plan,auth)


def test_new_policy_does_not_mutate_the_original_strict_parser():
 h,t,m=saved();old=parse_candidate_detail(t,h,m)
 parse_family_detail(t,h,m,policy(t))
 assert parse_candidate_detail(t,h,m)==old
 assert old['status']=='IDENTITY_REVIEW'


def test_final_family_full_record_replay_and_actual_asin_export_dedup(tmp_path):
 from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
 from amazon_es_bestseller.collection.detail5500_runtime import audit_stage
 from amazon_es_bestseller.collection.quick5500_cli import export
 from amazon_es_bestseller.multisource.storage import immutable_json,sealed,read_sealed,digest
 from amazon_es_bestseller.collection.quick_selection import file_sha
 h,t,m=parent_request();t2={**t,'task_id':'second-candidate','candidate_id':'second-candidate'}
 plan={'tasks':[t,t2],'run_id':t['run_id'],'shared_gate_path':str(tmp_path/'shared_gate.json')}
 bridge=DetailBridge5500(tmp_path,plan)
 for name in ('shared_gate.json','access_gate.json'):immutable_json(tmp_path/name,sealed({'state':'NORMAL'}))
 base=tmp_path/'base.json';immutable_json(base,{'scope':'test-only-original'})
 ref=lambda p:{'path':str(p),'sha256':file_sha(p)}
 auth={'code_sha':'test-code','parallel_base_authorization_reference':ref(base)}
 value=dict(approved=True,scope=SCOPE,run_id=plan['run_id'],runtime_root=str(tmp_path.resolve()),
  approved_by='agent_under_explicit_user_delegation',explicit_user_instruction='test-only family authorization',
  plan_sha256=digest(plan),code_sha='test-code',authorization_basis_reference=ref(base),
  preserve_requested_and_actual_ASIN=True,do_not_transfer_requested_rank=True,deduplicate_by_actual_ASIN=True)
 pp=tmp_path/'policy.json';immutable_json(pp,value);auth['family_identity_policy_reference']=ref(pp)
 from amazon_es_bestseller.collection.detail_family_identity5500 import read_policy
 pol=read_policy(tmp_path,plan,auth)
 for i,task in enumerate((t,t2)):
  slot=tmp_path/'tasks'/task['task_id'];slot.mkdir(parents=True)
  hp=slot/'capture.html';hp.write_text(h,encoding='utf8');mp=slot/'response_metadata.json';immutable_json(mp,m)
  rp=tmp_path/'receipts'/f'{i}.json'
  immutable_json(rp,sealed(dict(state='NORMAL',status_code=200,request_role='DETAIL',request_url=task['request_url'],
   run_id=task['run_id'],task_id=task['task_id'],html_sha256=file_sha(hp),started_at=f'2026-10-17T00:00:0{i*3}+00:00')))
  result=parse_family_detail(task,h,m,pol)
  result.update(html_path=str(hp),html_sha256=file_sha(hp),receipt_path=str(rp),receipt_sha256=file_sha(rp),
   response_metadata_path=str(mp),response_metadata_sha256=file_sha(mp))
  bridge.save_result(task,result)
 assert bridge.progress()['success']==2 and bridge.progress()['unique_resolved_asins']==1
 proof=audit_stage(bridge,'FINAL',2,auth)
 assert proof['unique_resolved_asins']==1 and proof['family_resolved_details']==2
 before=[(tmp_path/'tasks'/x['task_id']/'checkpoint.json').read_bytes() for x in (t,t2)]
 export(tmp_path)
 assert len((tmp_path/'details_5500_raw.jsonl').read_text(encoding='utf8').splitlines())==2
 assert len((tmp_path/'details_5500_unique_products.jsonl').read_text(encoding='utf8').splitlines())==1
 assert before==[(tmp_path/'tasks'/x['task_id']/'checkpoint.json').read_bytes() for x in (t,t2)]
 # An approved family result still needs its full independent field replay.
 cp=tmp_path/'tasks'/t['task_id']/'checkpoint.json';v=read_sealed(cp);v['record']['title_es_raw']='fabricated'
 cp.write_text(json.dumps(sealed(v)),encoding='utf8')
 with pytest.raises(ValueError,match='REPLAY_MISMATCH'):audit_stage(bridge,'FINAL',2,auth)
