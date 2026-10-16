from test_three_source_runner import execution_scope,Advancing
from test_highspeed_v4_execution import NOW
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.three_runner import V4ThreeSourceRunner

def test_recoverable_stop_saves_checkpoint_without_reauditing_entire_run(tmp_path,monkeypatch):
 p,a=execution_scope(tmp_path,monkeypatch);store=RunStore(a['store_root']);clock=[NOW]
 class Failing(Advancing):
  def fetch_page(self,url):raise OSError('TEST_ONLY_TRANSPORT_FAILURE')
 def no_interim_full_audit(*args,**kw):raise AssertionError('Repeated whole-run audit at recoverable stop')
 monkeypatch.setattr('amazon_es_bestseller.highspeed.three_runner.audit_capacity',no_interim_full_audit)
 try:
  r=V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None).run(p,authorization=a,transport=Failing(p,a,clock))
  assert r['run']['status']=='STOPPED';assert r['run']['stop_reason']=='NETWORK_ERROR'
  assert not r['receipt_audit']['diagnostic_passed'];assert r['receipt_audit']['review_status']=='DEFERRED_UNTIL_TASK_BOUNDARY'
  assert len(list(store.requests(p['run_id'])))==1
 finally:store.close()
