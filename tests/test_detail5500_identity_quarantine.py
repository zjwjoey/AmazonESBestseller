import gzip,json,hashlib
from pathlib import Path
import pytest
from amazon_es_bestseller.multisource.storage import immutable_json,sealed,digest
from amazon_es_bestseller.collection.quick_selection import file_sha
F=Path(__file__).parent/'fixtures/detail_bridge5500'
def ready(root):
 p=json.loads((F/'actual_changed_child.provenance.json').read_text());raw=gzip.decompress((F/'actual_changed_child.html.gz').read_bytes());assert hashlib.sha256(raw).hexdigest()==p['sha256'];t=p['task'];plan={'run_id':t['run_id'],'tasks':[t]};hp=root/'real.html';hp.write_bytes(raw);mp=root/'meta.json';immutable_json(mp,p['metadata']);r={**p['receipt'],'html_path':str(hp)};rp=root/'receipt.json';immutable_json(rp,sealed(r));o={**p['checkpoint'],'html_path':str(hp),'receipt_path':str(rp),'receipt_sha256':file_sha(rp),'response_metadata_path':str(mp),'response_metadata_sha256':file_sha(mp)};cp=root/'checkpoint.json';immutable_json(cp,sealed(o))
 def ref(x):return {'path':str(x),'sha256':file_sha(x)}
 review={'approved':True,'approved_by':'agent_under_explicit_user_delegation','scope':'ISOLATED_ACTUAL_CHILD_CHANGE_NOT_A_SUCCESS','run_id':plan['run_id'],'plan_sha256':digest(plan),'no_replacement':True,'tasks':[{'task_id':t['task_id'],'checkpoint_reference':ref(cp),'receipt_reference':ref(rp),'HTML_reference':ref(hp),'metadata_reference':ref(mp)}]};vp=root/'review.json';immutable_json(vp,review);auth={'reviewed_identity_quarantines_reference':ref(vp)};return plan,auth,review,vp
@pytest.mark.parametrize('bad',[None,'unapproved','wrong_scope','wrong_plan','changed_HTML','claim_success','access','same_ASIN','outside_root'])
def test_actual_child_change_is_reviewed_failure_not_success(tmp_path,bad):
 from amazon_es_bestseller.collection.identity_quarantine5500 import reviewed_identity_quarantines
 p,a,v,vp=ready(tmp_path)
 if bad=='unapproved':v['approved']=False
 elif bad=='wrong_scope':v['scope']='ACCEPT_DIFFERENT_CHILD'
 elif bad=='wrong_plan':v['plan_sha256']='wrong'
 elif bad=='changed_HTML':Path(v['tasks'][0]['HTML_reference']['path']).write_text('changed')
 elif bad in {'claim_success','access','same_ASIN'}:
  cp=Path(v['tasks'][0]['checkpoint_reference']['path']);o=json.loads(cp.read_text())['payload'];o['status']='SUCCESS' if bad=='claim_success' else 'IDENTITY_REVIEW';o['reason']='SPAIN_DELIVERY_LOCATION_NOT_VERIFIED' if bad=='access' else o['reason'];o['observed_active_asins']=['B0HCYPT2L3'] if bad=='same_ASIN' else o['observed_active_asins'];cp.write_text(json.dumps(sealed(o)));v['tasks'][0]['checkpoint_reference']['sha256']=file_sha(cp)
 elif bad=='outside_root':v['tasks'][0]['HTML_reference']['path']=str(tmp_path.parent/'outside.html')
 vp.write_text(json.dumps(v));a['reviewed_identity_quarantines_reference']['sha256']=file_sha(vp)
 if bad:
  with pytest.raises((ValueError,FileNotFoundError)):reviewed_identity_quarantines(tmp_path,p,a)
 else:
  q=reviewed_identity_quarantines(tmp_path,p,a);assert set(q)=={p['tasks'][0]['task_id']};assert q[p['tasks'][0]['task_id']]['not_success'] is True

def test_T2_still_requires_100_true_successes_and_FINAL_keeps_failure(monkeypatch,tmp_path):
 from types import SimpleNamespace
 import amazon_es_bestseller.collection.identity_quarantine5500 as mod
 tasks=[{'task_id':str(i)} for i in range(5500)];b=SimpleNamespace(root=tmp_path,plan={'tasks':tasks})
 monkeypatch.setattr(mod,'reviewed_identity_quarantines',lambda *a:{'69':{'not_success':True}})
 chosen,q=mod.stage_tasks(b,'T2',100,{})
 assert len(chosen)==100 and chosen[-1]['task_id']=='100' and '69' not in {t['task_id'] for t in chosen}
 final,q=mod.stage_tasks(b,'FINAL',5500,{})
 assert len(final)==5500 and final[69]['task_id']=='69'
 monkeypatch.setattr(mod,'reviewed_identity_quarantines',lambda *a:{'0':{}})
 with pytest.raises(ValueError,match='T1_FIRST_THIRTY'):mod.stage_tasks(b,'T2',100,{})

def test_scheduler_preserves_reviewed_failure_no_request_no_success_credit(tmp_path,monkeypatch):
 from types import SimpleNamespace
 from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
 from amazon_es_bestseller.multisource.storage import read_sealed
 tasks=[dict(task_id=str(i),candidate_id=str(i),selection_id=str(i),request_url='https://www.amazon.es/dp/B000000001') for i in range(33)]
 plan={'tasks':tasks,'run_id':'test','shared_gate_path':str(tmp_path/'gate.json')};(tmp_path/'receipts').mkdir()
 for i in range(31):
  cp=tmp_path/'tasks'/str(i)/'checkpoint.json';cp.parent.mkdir(parents=True);immutable_json(cp,sealed({'status':'SUCCESS' if i<30 else 'IDENTITY_REVIEW','task_id':str(i)}))
 old=(tmp_path/'tasks/30/checkpoint.json').read_bytes();b=DetailBridge5500(tmp_path,plan)
 monkeypatch.setattr(b,'validate_authorization',lambda a:None);monkeypatch.setattr(b,'validate_stage',lambda *a,**k:None);monkeypatch.setattr(b,'verify_saved_success',lambda *a:None)
 monkeypatch.setattr('amazon_es_bestseller.collection.identity_quarantine5500.reviewed_identity_quarantines',lambda *a:{'30':{'not_success':True}})
 monkeypatch.setattr('amazon_es_bestseller.collection.detail_bridge5500.parse_candidate_detail',lambda *a:{'status':'SUCCESS','record':None})
 html='<a id="nav-global-location-popover-link">Enviar a Madrid 28001</a>';calls=[];session=SimpleNamespace(page=SimpleNamespace(url=tasks[31]['request_url'],content=lambda:html),goto=lambda u:calls.append(u) or 200,wait_for_product_page=lambda:None,wait_for_price_text=lambda:None)
 auth={'max_requests':6050,'max_page_requests':6050,'request_start_interval_seconds':3}
 b.run(session,auth,until_completed=31)
 assert len(calls)==1 and b.progress()['success']==31 and (tmp_path/'tasks/30/checkpoint.json').read_bytes()==old
 assert not (tmp_path/'receipts/30_1.json').exists() and (tmp_path/'receipts/31_1.json').exists() and not (tmp_path/'receipts/32_1.json').exists()

@pytest.mark.parametrize('bad',[None,'prior_changed','prior_removed','cohort_changed','old_evidence_changed'])
def test_later_isolation_cannot_rewrite_accepted_T2_cohort(tmp_path,monkeypatch,bad):
 from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
 import amazon_es_bestseller.collection.identity_quarantine5500 as mod
 tasks=[{'task_id':'a'},{'task_id':'b'}];plan={'run_id':'test','tasks':tasks};b=DetailBridge5500(tmp_path,plan)
 hashes={}
 for t in tasks:
  cp=tmp_path/'tasks'/t['task_id']/'checkpoint.json';cp.parent.mkdir(parents=True);immutable_json(cp,sealed({'status':'SUCCESS'}));hashes[t['task_id']]=file_sha(cp)
 prior_ref={'path':'original_review','sha256':'original'};new_ref={'path':'extended_review','sha256':'extended'}
 prior={'failure1':{'item':'original','not_success':True}};q={**prior,'failure2':{'item':'new','not_success':True}}
 if bad=='prior_changed':q['failure1']={'item':'changed'}
 if bad=='prior_removed':q.pop('failure1')
 selected=[tasks[0],{'task_id':'replacement'}] if bad=='cohort_changed' else tasks
 monkeypatch.setattr(mod,'stage_tasks',lambda *a:(selected,q))
 def reviewed(root,plan,auth):
  if bad=='old_evidence_changed':raise ValueError('EVIDENCE_CHANGED')
  return prior
 monkeypatch.setattr(mod,'reviewed_identity_quarantines',reviewed);monkeypatch.setattr(b,'verify_saved_success',lambda *a:None)
 immutable_json(tmp_path/'T2_acceptance.json',sealed({'passed':True,'plan_sha256':digest(plan),'success_count':2,'successful_task_ids':['a','b'],'reviewed_identity_quarantines_reference':prior_ref,'checkpoint_sha256':hashes}))
 if bad:
  with pytest.raises(ValueError):b.validate_stage('T2',2,auth={'reviewed_identity_quarantines_reference':new_ref})
 else:b.validate_stage('T2',2,auth={'reviewed_identity_quarantines_reference':new_ref})
