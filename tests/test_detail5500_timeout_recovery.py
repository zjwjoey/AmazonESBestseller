"""Actual timeout receipt; a new bounded user instruction is not old recovery approval."""
import copy,json
from pathlib import Path
import pytest
from amazon_es_bestseller.collection import detail_recovery5500 as mod
KIND='USER_AUTHORIZED_DOMCONTENTLOADED_TIMEOUT_RECHECK_V1'
AUTHORITY='EXPLICIT_USER_PAGE_CHECK_AND_CONDITIONAL_CONTINUATION'
def real():
 r=json.loads((Path(__file__).parent/'fixtures/detail_bridge5500/actual_domcontentloaded_timeout_200_unknown.json').read_text(encoding='utf8'))['payload']
 return r, {k:r[k] for k in ('task_id','run_id','request_url')}
def approval():return dict(expected_failure_state='UNKNOWN',original_failure_kind=KIND,authority=AUTHORITY,user_authorized_exact_page_recheck=True,user_quotes=['Check current page and resume only if normal'],max_attempts=1)
def test_actual_timeout_needs_its_own_explicit_bounded_approval():
 r,t=real();assert mod.review_failure_state(approval(),r,t)=='UNKNOWN'
@pytest.mark.parametrize('bad',['approval','authority','attempts','no_quote','missing_response','403','429','challenge','other_url','different_error','final_url','task','run','status','no_start'])
def test_other_failures_and_generic_approval_are_rejected(bad):
 r,t=real();a=approval()
 if bad=='approval':a['user_authorized_exact_page_recheck']=False
 elif bad=='authority':a['authority']='generic'
 elif bad=='attempts':a['max_attempts']=2
 elif bad=='no_quote':a['user_quotes']=[]
 elif bad=='missing_response':r['document_responses']=[]
 elif bad in ('403','429'):r['document_responses'][0]['status_code']=int(bad)
 elif bad=='challenge':r['state']='CHALLENGE'
 elif bad=='other_url':r['document_responses'][0]['url']='https://www.amazon.es/'
 elif bad=='different_error':r['error']='Error: net::ERR_FAILED'
 elif bad=='final_url':r['final_url']='chrome-error://chromewebdata/'
 elif bad=='task':r['task_id']='other'
 elif bad=='run':r['run_id']='other'
 elif bad=='status':r['status_code']=200
 elif bad=='no_start':r['document_start_event_observed']=False
 with pytest.raises(ValueError):mod.review_failure_state(a,r,t)

def sandbox(tmp_path,monkeypatch,status=200):
 from test_detail5500_access_recovery import sandbox as base
 from amazon_es_bestseller.multisource.storage import atomic_json,read_sealed,sealed
 from amazon_es_bestseller.collection.quick_selection import file_sha
 s,b,a,t,ref,calls,h=base(tmp_path,monkeypatch,status)
 r,_=real();r.update(**{k:t[k] for k in ('task_id','run_id','request_url')},final_url=t['request_url'],document_responses=[dict(url=t['request_url'],status_code=200)])
 original=tmp_path/'receipts/original.json';hp=tmp_path/'original.html';hp.write_text(h,encoding='utf8');r.update(html_path=str(hp),html_sha256=file_sha(hp));atomic_json(original,sealed(r))
 for n in ('access_gate.json','shared.json'):atomic_json(tmp_path/n,sealed(dict(state='UNKNOWN',receipt_path=str(original))))
 v=dict(read_sealed(ref['path']),**approval());v.update(original_failure_receipt_reference=dict(path=str(original),sha256=file_sha(original)),gate_sha256={str(tmp_path/n):file_sha(tmp_path/n) for n in ('access_gate.json','shared.json')},recovery_evidence_name='access_recovery_6')
 atomic_json(ref['path'],sealed(v));ref['sha256']=file_sha(ref['path']);return s,b,a,t,ref,calls,h
@pytest.mark.parametrize('status',[200,None,403,429])
def test_one_navigation_only_and_no_gate_acceptance_on_failure(tmp_path,monkeypatch,status):
 from amazon_es_bestseller.multisource.storage import immutable_json,read_sealed,sealed
 from amazon_es_bestseller.collection.quick_selection import file_sha
 s,b,a,t,ref,calls,h=sandbox(tmp_path,monkeypatch,status)
 cp=tmp_path/'tasks'/t['task_id']/'checkpoint.json';old=cp.read_bytes();prior=tmp_path/'tasks/prior/checkpoint.json';hp=tmp_path/'prior.html';hp.write_text('saved success')
 immutable_json(prior,sealed(dict(status='SUCCESS',html_path=str(hp))));baseline={'prior':dict(checkpoint_sha256=file_sha(prior),html_sha256=file_sha(hp))};saves=[];b.save_result=lambda t,r:saves.append(r)
 rp,r,html=mod.capture_once(s,b,a,t,ref);assert calls==[t['request_url']]
 if status==200:
  mod.accept_capture(b,t,rp,ref,baseline,recovery_name='access_recovery_6');assert saves[0]['status']=='SUCCESS';assert cp.with_name('checkpoint.network_unknown.original.json').read_bytes()==old
 else:
  with pytest.raises(ValueError):mod.accept_capture(b,t,rp,ref,baseline,recovery_name='access_recovery_6')
  assert not saves and read_sealed(tmp_path/'access_gate.json')['state']=='UNKNOWN'
 with pytest.raises(ValueError,match='ALREADY_CONSUMED'):mod.capture_once(s,b,a,t,ref)
 assert len(calls)==1
@pytest.mark.parametrize('bad',['tamper','captcha','wrong_location','missing_binding'])
def test_original_saved_evidence_must_be_bound_and_nonrestricted_before_get(tmp_path,monkeypatch,bad):
 from amazon_es_bestseller.multisource.storage import atomic_json,read_sealed,sealed
 from amazon_es_bestseller.collection.quick_selection import file_sha
 s,b,a,t,ref,calls,h=sandbox(tmp_path,monkeypatch)
 p=tmp_path/'original.html';original=tmp_path/'receipts/original.json'
 if bad=='tamper':p.write_text('changed',encoding='utf8')
 elif bad in ('captcha','wrong_location'):
  data=h+'<p>validateCaptcha</p>' if bad=='captcha' else h.replace('28001','99999')
  p.write_text(data,encoding='utf8');r=read_sealed(original);r['html_sha256']=file_sha(p);atomic_json(original,sealed(r));v=read_sealed(ref['path']);v['original_failure_receipt_reference']['sha256']=file_sha(original);atomic_json(ref['path'],sealed(v));ref['sha256']=file_sha(ref['path'])
 else:
  v=read_sealed(ref['path']);v.pop('original_failure_receipt_reference');atomic_json(ref['path'],sealed(v));ref['sha256']=file_sha(ref['path'])
 with pytest.raises(ValueError):mod.capture_once(s,b,a,t,ref)
 assert calls==[]
