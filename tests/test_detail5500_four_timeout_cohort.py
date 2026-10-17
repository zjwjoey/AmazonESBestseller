"""Four exact timeout rechecks: Gate remains stopped until all four pass."""
import copy
from types import SimpleNamespace
import pytest
from amazon_es_bestseller.collection import detail_recovery5500 as m
from amazon_es_bestseller.multisource.storage import atomic_json, immutable_json, sealed, read_sealed, digest
from amazon_es_bestseller.collection.quick_selection import file_sha
from test_detail5500_timeout_recovery import sandbox as single

def ref(p):return dict(path=str(p),sha256=file_sha(p))
def fixture(tmp_path,monkeypatch):
 s,b,a,t,ar,calls,h=single(tmp_path,monkeypatch)
 original_approval=read_sealed(ar['path']);original_receipt=read_sealed(tmp_path/'receipts/original.json')
 tasks=[];entries=[]
 for i in range(4):
  ti={**t,'task_id':'task_'+str(i),'request_url':t['request_url']+'&offline_cohort='+str(i)};tasks.append(ti)
  op=tmp_path/'receipts'/('original_'+str(i)+'.json');immutable_json(op,sealed({**original_receipt,'task_id':ti['task_id'],'request_url':ti['request_url'],'final_url':ti['request_url'],'document_responses':[dict(url=ti['request_url'],status_code=200)]}))
  cp=tmp_path/'tasks'/ti['task_id']/'checkpoint.json';immutable_json(cp,sealed(dict(status='ACCESS_STOP',receipt_path=str(op))))
  entries.append(dict(task_id=ti['task_id'],request_url=ti['request_url'],original_checkpoint_reference=ref(cp),original_failure_receipt_reference=ref(op)))
 b.plan['tasks']=tasks
 for p in (tmp_path/'access_gate.json',tmp_path/'shared.json'):atomic_json(p,sealed(dict(state='UNKNOWN',receipt_path=entries[0]['original_failure_receipt_reference']['path'])))
 cohort=dict(approved=True,scope='FOUR_EXACT_TIMEOUT_RECHECKS_ALL_NORMAL_BEFORE_QUEUE',run_id=b.plan['run_id'],runtime_root=str(tmp_path.resolve()),plan_sha256=digest(b.plan),code_sha=a['code_sha'],max_total_rechecks=4,max_attempts_per_task=1,user_quotes=['four exact tasks, once each'],gate_sha256={str(p):file_sha(p) for p in (tmp_path/'access_gate.json',tmp_path/'shared.json')},gate_original_receipt=entries[0]['original_failure_receipt_reference']['path'],entries=entries)
 co=tmp_path/'cohort.json';immutable_json(co,sealed(cohort));cr=ref(co);aps=[]
 for i,ti in enumerate(tasks):
  av={**original_approval,'task_id':ti['task_id'],'request_url':ti['request_url'],'plan_sha256':digest(b.plan),'gate_sha256':cohort['gate_sha256'],'original_failure_receipt_reference':entries[i]['original_failure_receipt_reference'],'recovery_evidence_name':'access_recovery_'+str(7+i),'timeout_cohort_reference':cr}
  ap=tmp_path/('approval_'+str(i)+'.json');immutable_json(ap,sealed(av));aps.append(ref(ap))
 prior=tmp_path/'tasks/prior/checkpoint.json';hp=tmp_path/'prior.html';hp.write_text('prior')
 immutable_json(prior,sealed(dict(status='SUCCESS',html_path=str(hp))));baseline={'prior':dict(checkpoint_sha256=file_sha(prior),html_sha256=file_sha(hp))}
 b.save_result=lambda task,result:atomic_json(tmp_path/'tasks'/task['task_id']/'checkpoint.json',sealed(result))
 def goto(url,**kw):s.page.url=url;calls.append(url);return SimpleNamespace(status=200)
 s.page.goto=goto
 monkeypatch.setattr(m.time,'sleep',lambda n:None)
 return s,b,a,tasks,aps,cr,baseline,calls

def accept_one(s,b,a,t,ap,baseline):
 rp,r,h=m.capture_once(s,b,a,t,ap)
 return m.accept_capture(b,t,rp,ap,baseline,recovery_name=read_sealed(ap['path'])['recovery_evidence_name'])

def test_four_real_transport_calls_only_release_gate_after_all_acceptances(tmp_path,monkeypatch):
 s,b,a,ts,aps,cr,baseline,calls=fixture(tmp_path,monkeypatch)
 gates={p:p.read_bytes() for p in (tmp_path/'access_gate.json',tmp_path/'shared.json')}
 for i,t in enumerate(ts):
  accept_one(s,b,a,t,aps[i],baseline)
  assert all(p.read_bytes()==v for p,v in gates.items())
  if i<3:
   with pytest.raises(ValueError):m.finalize_timeout_cohort(b,cr,aps,baseline)
 assert len(calls)==4
 m.finalize_timeout_cohort(b,cr,aps,baseline)
 assert all(read_sealed(p)['state']=='NORMAL' for p in gates)
 for t,ap in zip(ts,aps):
  with pytest.raises(ValueError):m.capture_once(s,b,a,t,ap)
 assert len(calls)==4
 assert all((tmp_path/'tasks'/t['task_id']/'checkpoint.network_unknown.original.json').exists() for t in ts)

@pytest.mark.parametrize('bad',['size','duplicate','wrong_url','code','gate','cohort_hash','prior_success','foreign_approval'])
def test_tamper_cannot_admit_new_network(tmp_path,monkeypatch,bad):
 s,b,a,ts,aps,cr,baseline,calls=fixture(tmp_path,monkeypatch)
 co=read_sealed(cr['path']);av=read_sealed(aps[0]['path'])
 if bad=='size':co['entries']=co['entries'][:3]
 elif bad=='duplicate':co['entries'][1]=co['entries'][0]
 elif bad=='wrong_url':co['entries'][0]['request_url']='https://www.amazon.es/'
 elif bad=='code':co['code_sha']='foreign'
 elif bad=='gate':atomic_json(tmp_path/'shared.json',sealed(dict(state='BLOCKED')))
 elif bad=='cohort_hash':av['timeout_cohort_reference']['sha256']='bad'
 elif bad=='prior_success':atomic_json(tmp_path/'tasks'/ts[0]['task_id']/'checkpoint.json',sealed(dict(status='SUCCESS')))
 elif bad=='foreign_approval':av['task_id']='foreign'
 if bad not in ('gate','cohort_hash','prior_success','foreign_approval'):
  atomic_json(cr['path'],sealed(co));av['timeout_cohort_reference']=ref(__import__('pathlib').Path(cr['path']))
 atomic_json(aps[0]['path'],sealed(av));aps[0]=ref(__import__('pathlib').Path(aps[0]['path']))
 with pytest.raises(ValueError):m.capture_once(s,b,a,ts[0],aps[0])
 assert calls==[]

@pytest.mark.parametrize('status',[None,403,429])
def test_any_failed_recheck_cannot_release_gate_or_repeat(tmp_path,monkeypatch,status):
 s,b,a,ts,aps,cr,baseline,calls=fixture(tmp_path,monkeypatch)
 s.page.goto=lambda url,**kw:(calls.append(url) or (SimpleNamespace(status=status) if status else None))
 s.page.url=ts[0]['request_url']
 rp,r,h=m.capture_once(s,b,a,ts[0],aps[0])
 with pytest.raises(ValueError):m.accept_capture(b,ts[0],rp,aps[0],baseline,recovery_name='access_recovery_7')
 with pytest.raises(ValueError):m.finalize_timeout_cohort(b,cr,aps,baseline)
 with pytest.raises(ValueError):m.capture_once(s,b,a,ts[0],aps[0])
 assert len(calls)==1 and read_sealed(tmp_path/'access_gate.json')['state']=='UNKNOWN'

@pytest.mark.parametrize('bad',[None,'challenge','foreign_delivery'])
def test_explicit_cohort_incomplete_original_dom_requires_new_verified_location(tmp_path,monkeypatch,bad):
 from pathlib import Path
 s,b,a,ts,aps,cr,baseline,calls=fixture(tmp_path,monkeypatch)
 av=read_sealed(aps[0]['path']);co=read_sealed(cr['path']);op=Path(av['original_failure_receipt_reference']['path']);r=read_sealed(op)
 hp=tmp_path/'incomplete_original.html';html='<html><head><title>Loading</title></head><body></body></html>'
 if bad=='challenge':html+='<p>validateCaptcha</p>'
 elif bad=='foreign_delivery':html='<div id="glow-ingress-line2">Enviar a Taiwan 100</div>'
 hp.write_text(html,encoding='utf8');r.update(html_path=str(hp),html_sha256=file_sha(hp));atomic_json(op,sealed(r))
 av['original_failure_receipt_reference']=ref(op);co['entries'][0]['original_failure_receipt_reference']=ref(op)
 atomic_json(cr['path'],sealed(co));av['timeout_cohort_reference']=ref(Path(cr['path']));atomic_json(aps[0]['path'],sealed(av));aps[0]=ref(Path(aps[0]['path']))
 if bad:
  with pytest.raises(ValueError):m.capture_once(s,b,a,ts[0],aps[0])
  assert calls==[]
 else:
  rp,r,h=m.capture_once(s,b,a,ts[0],aps[0]);assert r['status_code']==200 and len(calls)==1
  assert m.validate_capture(ts[0],r,h)[0]['status']=='SUCCESS'
