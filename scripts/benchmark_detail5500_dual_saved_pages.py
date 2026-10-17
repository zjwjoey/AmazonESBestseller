"""Nine saved real pages, current three-lane and isolated two-lane execution paths, simulated transport only."""
import asyncio,json,statistics,sys,tempfile,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace as NS
W=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(W/'tests'))
from test_quick_selection_5500 import auth_for
from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
from amazon_es_bestseller.collection import detail_parallel_runtime5500 as mod
from amazon_es_bestseller.multisource.storage import immutable_json,read_sealed,sealed
B=Path(r'F:\AmazonESBestseller\runtime\quick_selection_detail_5500_batches\quick_detail5500_20261016_v0')
R=Path(r'F:\AmazonESBestseller\runtime\quick_selection_detail_5500_20261016')
def main():
 plan=json.loads((B/'detail_plan_5500.json').read_text(encoding='utf8'))
 owner=json.loads((B/'browser_timing75_current_owner.json').read_text(encoding='utf8'))
 liveauth=json.loads(Path(owner['authorization']['path']).read_text(encoding='utf8'))
 real=object.__new__(DetailBridge5500);real.root=B;real.plan=plan
 packets=[]
 for task in reversed(plan['tasks']):
  p=B/'tasks'/task['task_id']/'checkpoint.json'
  if not p.exists():continue
  cp=read_sealed(p)
  if cp['status']!='SUCCESS':continue
  packets.append((task,Path(cp['html_path']).read_text(encoding='utf8'),cp['record']))
  if len(packets)==9:break
 packets.reverse();assert len(packets)==9
 rows=[read_sealed(p) for p in (B/'receipts').glob('*.json')]
 durations=[]
 for r in sorted(rows,key=lambda r:r['started_at'])[-30:]:
  normal=[x for x in r.get('document_responses',[]) if x['status_code']==200]
  if r['state']=='NORMAL' and normal:
   d=(datetime.fromisoformat(normal[0]['observed_at'])-datetime.fromisoformat(r['started_at'])).total_seconds()
   if d>0:durations.append(d)
 delay=statistics.median(durations);results=[]
 for lanes in [3,2]:
  parallel=True
  if lanes==2:
   from amazon_es_bestseller.collection import detail_dual_runtime5500 as mod
  else:
   from amazon_es_bestseller.collection import detail_parallel_runtime5500 as mod
  with tempfile.TemporaryDirectory(prefix='detail-dual-benchmark-') as scratch:
   scratch=Path(scratch);tasks=[p[0] for p in packets]
   testplan={**plan,'tasks':tasks,'shared_gate_path':str(scratch/'shared_gate.json')}
   bridge=DetailBridge5500(scratch/'run',testplan);auth=auth_for(bridge,scratch)
   auth.update(max_requests=6050,max_page_requests=6050,local_processing_lanes=3,network_parallelism=1,
    deadline=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat())
   immutable_json(bridge.root/'access_gate.json',sealed({'state':'NORMAL'}))
   testvalidate=bridge.validate_authorization
   def validate(a):real.validate_authorization(liveauth);testvalidate(a)
   bridge.validate_authorization=validate
   starts=[];begin=time.monotonic()
   if not parallel:
    class Session:
     def __init__(self):self.page=NS(url='');self.document_responses=[]
     def prepare_task(self,t):self.task=t
     def goto(self,url):
      starts.append(time.monotonic());time.sleep(delay);self.page.url=url;return 200
     def wait_for_product_page(self):time.sleep(2)
     def wait_for_price_text(self):time.sleep(1)
    session=Session();session.page.content=lambda:next(p[1] for p in packets if p[0]['task_id']==session.task['task_id'])
    bridge.run(session,auth,until_completed=9,local_lanes=3)
    peak=1
   else:
    async def collect():
     c=mod.ParallelCollector(bridge,auth)
     from concurrent.futures import ProcessPoolExecutor
     from multiprocessing import get_context
     c.executors=[ProcessPoolExecutor(max_workers=1,mp_context=get_context('spawn')) for _ in range(lanes)]
     mod.validate_parallel_mode=lambda *a:None # No production authorization or store.
     class Page:
      def __init__(self,lane):self.lane=lane;self.main_frame=object();self.url='about:blank'
      async def goto(self,url,**kw):
       req=NS(url=url,frame=self.main_frame,resource_type='document',method='GET',redirected_from=None,is_navigation_request=lambda:True)
       async def proceed():starts.append(time.monotonic())
       async def abort():raise RuntimeError('UNEXPECTED_BENCHMARK_ABORT')
       await c.route(NS(request=req,continue_=proceed,abort=abort))
       await asyncio.sleep(delay);self.url=url
       response=NS(status=200,request=req,url=url);c.observe(self.lane,response);return response
      async def content(self):
       t=c.tokens[self.lane]['task'];return next(p[1] for p in packets if p[0]['task_id']==t['task_id'])
     c.pages=[Page(i) for i in range(lanes)]
     try:await c.scheduler.run(tasks)
     finally:
      for e in c.executors:e.shutdown(wait=True)
     return c.scheduler.peak_active
    peak=asyncio.run(collect())
   elapsed=time.monotonic()-begin
   assert bridge.progress()['success']==9
   for t,body,expected in packets:
    actual=bridge.outcomes[t['task_id']]['record']
    assert actual=={**expected,'collected_at':actual['collected_at']},t['task_id']
   minimum=min(b-a for a,b in zip(starts,starts[1:]));assert minimum>=(3.599 if lanes==2 else 2.999)
   results.append(dict(network_parallelism=lanes,elapsed_seconds=elapsed,tasks=9,
    all_full_records_equal_except_current_capture_timestamp=True,peak_capture_inflight=peak,
    parser_pids=sorted({r['local_parser_pid'] for r in bridge.outcomes.values()}),minimum_start_interval=minimum))
 ratio=results[0]['elapsed_seconds']/results[1]['elapsed_seconds']
 report=dict(at=datetime.now(timezone.utc).isoformat(),passed=True,results=results,dual_vs_three_throughput_ratio=ratio,
  transport_simulated_seconds=delay,transport_measurement_samples=len(durations),real_full_authorization_each_transaction=True,
  production_network_requests=0,production_checkpoint_writes=0,not_LIVE_speed=True,not_deployment_approval=True,
  limitation='One nine-page sample with simulated recent median document response latency; changing network and page mix remain uncertain.')
 target=sys.argv[1] if len(sys.argv)>1 else 'dual1000_nine_saved_pages_benchmark.json'
 assert Path(target).name==target
 immutable_json(R/target,report);print(json.dumps(report,ensure_ascii=True))
if __name__=='__main__':main()
