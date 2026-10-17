"""Real transport-failure receipt; no live requests in tests."""
import copy,json
from pathlib import Path
import pytest
from amazon_es_bestseller.collection import detail_recovery5500 as mod
KIND='USER_REPORTED_RESTORED_NET_ERR_FAILED_NO_HTTP_V1'
def real():
 v=json.loads((Path(__file__).parent/'fixtures/detail_bridge5500/actual_net_ERR_FAILED_no_HTTP.json').read_text(encoding='utf8'))['payload']
 return v,{'task_id':v['task_id'],'run_id':v['run_id'],'request_url':v['request_url']}
def approval():return {'expected_failure_state':'UNKNOWN','original_failure_kind':KIND,'user_reported_page_restored':True,'user_quotes':['网络问题我刷新后又恢复了']}
def test_real_reported_restored_transport_is_separate_from_challenge():
 r,t=real();assert mod.review_failure_state(approval(),r,t)=='UNKNOWN'
@pytest.mark.parametrize('bad',['missing_approval','not_restored','generic_unknown','403','429','challenge','http_observed','wrong_url','wrong_task','wrong_run','different_error','normal_dom_url'])
def test_unapproved_or_other_failures_do_not_use_this_exception(bad):
 r,t=real();a=approval()
 if bad=='missing_approval':a={}
 elif bad=='not_restored':a['user_reported_page_restored']=False
 elif bad=='generic_unknown':a['original_failure_kind']='generic'
 elif bad in {'403','429'}:r['status_code']=int(bad)
 elif bad=='challenge':r['state']='CHALLENGE'
 elif bad=='http_observed':r['document_responses']=[{'status_code':200}]
 elif bad=='wrong_url':r['request_url']='https://www.amazon.es/'
 elif bad=='wrong_task':r['task_id']='other'
 elif bad=='wrong_run':r['run_id']='other'
 elif bad=='different_error':r['error']='timeout or restriction, not demonstrated ERR_FAILED'
 elif bad=='normal_dom_url':r['final_url']=r['request_url']
 with pytest.raises(ValueError):mod.review_failure_state(a,r,t)


def unknown_sandbox(tmp_path,monkeypatch,status=200):
 from test_detail5500_access_recovery import sandbox
 from amazon_es_bestseller.multisource.storage import atomic_json,read_sealed,sealed
 from amazon_es_bestseller.collection.quick_selection import file_sha
 session,bridge,auth,task,ref,calls,html=sandbox(tmp_path,monkeypatch,status)
 original=tmp_path/'receipts/original.json'
 r,_=real();r.update(task_id=task['task_id'],run_id=task['run_id'],request_url=task['request_url'],
  error='Error: Page.goto: net::ERR_FAILED at '+task['request_url']+'\nTEST_ONLY_SIMULATED_TRANSPORT')
 atomic_json(original,sealed(r))
 for name in ('access_gate.json','shared.json'):atomic_json(tmp_path/name,sealed({'state':'UNKNOWN','receipt_path':str(original)}))
 a={**read_sealed(ref['path']),**approval(),
  'original_failure_receipt_reference':{'path':str(original),'sha256':file_sha(original)},
  'recovery_evidence_name':'access_recovery_5',
  'gate_sha256':{str(tmp_path/name):file_sha(tmp_path/name) for name in ('access_gate.json','shared.json')}}
 atomic_json(ref['path'],sealed(a));ref['sha256']=file_sha(ref['path'])
 return session,bridge,auth,task,ref,calls,html

@pytest.mark.parametrize('status',[200,None,403,429])
def test_bounded_transport_recovery_one_attempt_retains_all_history(tmp_path,monkeypatch,status):
 from amazon_es_bestseller.multisource.storage import immutable_json,read_sealed,sealed
 from amazon_es_bestseller.collection.quick_selection import file_sha
 s,b,a,t,ref,calls,h=unknown_sandbox(tmp_path,monkeypatch,status)
 oldcp=tmp_path/'tasks'/t['task_id']/'checkpoint.json';before=oldcp.read_bytes()
 oldproofs={}
 for i in range(1,5):
  p=tmp_path/f'access_recovery_{i}.accepted.json';immutable_json(p,sealed({'historical':i}));oldproofs[p]=p.read_bytes()
 prior=tmp_path/'tasks/prior/checkpoint.json';hp=tmp_path/'prior.html';hp.write_text('original successful HTML')
 immutable_json(prior,sealed({'status':'SUCCESS','html_path':str(hp)}))
 baseline={'prior':{'checkpoint_sha256':file_sha(prior),'html_sha256':file_sha(hp)}}
 saves=[];b.save_result=lambda task,result:saves.append(result)
 rp,r,h=mod.capture_once(s,b,a,t,ref)
 assert calls==[t['request_url']] and r['status_code']==status
 if status==200:
  proof=mod.accept_capture(b,t,rp,ref,baseline,recovery_name='access_recovery_5')
  assert len(saves)==1 and saves[0]['status']=='SUCCESS'
  assert oldcp.with_name('checkpoint.network_unknown.original.json').read_bytes()==before
  assert read_sealed(tmp_path/'access_gate.json')['previous_gate']['state']=='UNKNOWN'
 else:
  with pytest.raises(ValueError):mod.accept_capture(b,t,rp,ref,baseline,recovery_name='access_recovery_5')
  assert not saves and read_sealed(tmp_path/'access_gate.json')['state']=='UNKNOWN'
 with pytest.raises(ValueError,match='ALREADY_CONSUMED'):mod.capture_once(s,b,a,t,ref)
 assert len(calls)==1 and all(p.read_bytes()==v for p,v in oldproofs.items())

@pytest.mark.parametrize('bad',['receipt_changed','gate_changed','budget','missing_receipt_binding','missing_user_report'])
def test_new_exception_fails_before_any_transport(tmp_path,monkeypatch,bad):
 from amazon_es_bestseller.multisource.storage import atomic_json,read_sealed,sealed
 from amazon_es_bestseller.collection.quick_selection import file_sha
 s,b,a,t,ref,calls,h=unknown_sandbox(tmp_path,monkeypatch)
 if bad=='receipt_changed':atomic_json(tmp_path/'receipts/original.json',sealed({'state':'UNKNOWN'}))
 elif bad=='gate_changed':atomic_json(tmp_path/'shared.json',sealed({'state':'CHALLENGE'}))
 elif bad=='budget':a['max_requests']=3
 else:
  v=read_sealed(ref['path']);v.pop('original_failure_receipt_reference' if bad=='missing_receipt_binding' else 'user_reported_page_restored')
  atomic_json(ref['path'],sealed(v));ref['sha256']=file_sha(ref['path'])
 with pytest.raises((ValueError,KeyError)):mod.capture_once(s,b,a,t,ref)
 assert not calls and not list((tmp_path/'receipts').glob('*controlled_recovery*'))


def test_unobserved_human_refresh_is_a_hold_not_a_fabricated_receipt(tmp_path):
 from amazon_es_bestseller.collection.detail_budget5500 import unobserved_manual_page_holds,total_conservative_page_holds
 from amazon_es_bestseller.multisource.storage import immutable_json,sealed,digest
 from amazon_es_bestseller.collection.quick_selection import file_sha
 plan={'run_id':'test','tasks':[]};source=tmp_path/'reported.json';immutable_json(source,{'user_report':'I refreshed; the page recovered'})
 hold=tmp_path/'manual_PAGE_budget_holds/refresh5.json'
 v=dict(scope='USER_REPORTED_MANUAL_REFRESH_UNOBSERVED',run_id='test',plan_sha256=digest(plan),not_network_receipts=True,held_PAGE_requests=1,user_report_reference={'path':str(source),'sha256':file_sha(source)})
 immutable_json(hold,sealed(v));auth={'manual_PAGE_budget_hold_references':[{'path':str(hold),'sha256':file_sha(hold)}]}
 assert unobserved_manual_page_holds(tmp_path,plan,auth)==total_conservative_page_holds(tmp_path,plan,auth)==1
 assert not (tmp_path/'receipts').exists()
 with pytest.raises(ValueError,match='NOT_BOUND'):unobserved_manual_page_holds(tmp_path,plan,{})
 immutable_json(hold.with_name('duplicate.json'),sealed(v))
 with pytest.raises(ValueError,match='DUPLICATED'):unobserved_manual_page_holds(tmp_path,plan)
