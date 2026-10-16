"""Separate explicit recoveries must retain the first proof byte for byte."""
import pytest
from test_detail5500_access_recovery import sandbox
from amazon_es_bestseller.collection import detail_recovery5500 as mod
from amazon_es_bestseller.collection.quick_selection import file_sha
from amazon_es_bestseller.multisource.storage import immutable_json,atomic_json,read_sealed,sealed

@pytest.mark.parametrize('name',['access_recovery_2','../outside','access_recovery_0','access_recovery_2.accepted.json','access_recovery_3','missing_binding'])
def test_new_proof_is_explicitly_bound_and_old_proof_never_overwritten(tmp_path,monkeypatch,name):
 s,b,a,t,ref,calls,h=sandbox(tmp_path,monkeypatch)
 approval=read_sealed(ref['path'])
 if name!='missing_binding':approval['recovery_evidence_name']='access_recovery_2'
 atomic_json(ref['path'],sealed(approval));ref['sha256']=file_sha(ref['path'])
 rp,r,h=mod.capture_once(s,b,a,t,ref)
 prior_html=b.root/'prior.html';prior_html.write_text('prior')
 prior=b.root/'tasks/prior/checkpoint.json';immutable_json(prior,sealed({'status':'SUCCESS','html_path':str(prior_html)}))
 baseline={'prior':{'checkpoint_sha256':file_sha(prior),'html_sha256':file_sha(prior_html)}}
 old=b.root/'access_recovery_1.accepted.json';immutable_json(old,sealed({'historical_proof':'must remain unchanged'}));oldbytes=old.read_bytes()
 gates={p:p.read_bytes() for p in [b.root/'access_gate.json',b.root/'shared.json']}
 saves=[];b.save_result=lambda task,result:saves.append(result)
 if name=='access_recovery_2':
  result=mod.accept_capture(b,t,rp,ref,baseline,recovery_name=name)
  assert result['normal_receipt_path']==str(rp) and len(saves)==1
  assert (b.root/'access_recovery_2.accepted.json').exists()
  assert (b.root/'tasks'/t['task_id']/'response_metadata.recovery_2.json').exists()
  for p in gates:assert read_sealed(p)['recovery_evidence_path']==str(b.root/'access_recovery_2.accepted.json')
 else:
  with pytest.raises(ValueError,match='RECOVERY_EVIDENCE_NAME'):
   mod.accept_capture(b,t,rp,ref,baseline,recovery_name='access_recovery_2' if name=='missing_binding' else name)
  assert not saves and all(p.read_bytes()==value for p,value in gates.items())
 assert old.read_bytes()==oldbytes
