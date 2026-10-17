"""Nine saved real pages, current three-lane and isolated two-lane execution paths, simulated transport only."""
import asyncio,json,statistics,sys,tempfile,time,os
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
 sample_count=int(sys.argv[2]) if len(sys.argv)>2 else 36
 assert 36<=sample_count<=1000
 target=sys.argv[1] if len(sys.argv)>1 else 'dual1000_sustained_saved_pages.json'
 assert Path(target).name==target
 assert not (R/target).exists() and not (R/(target+'.progress.json')).exists()
 # This offline process and its parser children yield CPU priority to collection.
 if os.name=='nt':
  import ctypes
  from ctypes import wintypes
  kernel=ctypes.windll.kernel32
  kernel.GetCurrentProcess.restype=wintypes.HANDLE
  kernel.SetPriorityClass.argtypes=[wintypes.HANDLE,wintypes.DWORD]
  assert kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x4000)
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
  if len(packets)==sample_count:break
 packets.reverse();assert len(packets)==sample_count
 rows=[read_sealed(p) for p in (B/'receipts').glob('*.json')]
 durations=[]
 for r in sorted(rows,key=lambda r:r['started_at'])[-30:]:
  normal=[x for x in r.get('document_responses',[]) if x['status_code']==200]
  if r['state']=='NORMAL' and normal:
   d=(datetime.fromisoformat(normal[0]['observed_at'])-datetime.fromisoformat(r['started_at'])).total_seconds()
   if d>0:durations.append(d)
 delay=statistics.median(durations);results=[]
 for lanes in [2]:
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
   starts=[];begin=time.perf_counter()
   if not parallel:
    class Session:
     def __init__(self):self.page=NS(url='');self.document_responses=[]
     def prepare_task(self,t):self.task=t
     def goto(self,url):
      starts.append(time.perf_counter());time.sleep(delay);self.page.url=url;return 200
     def wait_for_product_page(self):time.sleep(2)
     def wait_for_price_text(self):time.sleep(1)
    session=Session();session.page.content=lambda:next(p[1] for p in packets if p[0]['task_id']==session.task['task_id'])
    bridge.run(session,auth,until_completed=sample_count,local_lanes=3)
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
       async def proceed():
        starts.append(time.perf_counter())
        from amazon_es_bestseller.multisource.storage import atomic_json
        atomic_json(R/(target+'.progress.json'),dict(at=datetime.now(timezone.utc).isoformat(),
         phase='OFFLINE_SAVED_HTML_SIMULATED_TRANSPORT_RUNNING',dispatches=len(starts),
         requested_sample_count=sample_count,first_dispatch_elapsed_seconds=starts[-1]-starts[0],
         production_requests=0,production_checkpoint_writes=0,not_LIVE_speed=True))
       async def abort():raise RuntimeError('UNEXPECTED_BENCHMARK_ABORT')
       await c.route(NS(request=req,continue_=proceed,abort=abort))
       await asyncio.sleep(delay);self.url=url
       response=NS(status=200,request=req,url=url);c.observe(self.lane,response);return response
      async def content(self):
       t=c.tokens[self.lane]['task'];return next(p[1] for p in packets if p[0]['task_id']==t['task_id'])
     c.pages=[Page(i) for i in range(lanes)]
     try:
      await c.warm_parsers()
      await c.scheduler.run(tasks)
     finally:
      for e in c.executors:e.shutdown(wait=True)
     return c.scheduler.peak_active
    peak=asyncio.run(collect())
   elapsed=time.perf_counter()-begin
   assert bridge.progress()['success']==sample_count
   for t,body,expected in packets:
    actual=bridge.outcomes[t['task_id']]['record']
    assert actual=={**expected,'collected_at':actual['collected_at']},t['task_id']
   minimum=min(b-a for a,b in zip(starts,starts[1:]));assert minimum>=(3.59999 if lanes==2 else 2.999)
   results.append(dict(network_parallelism=lanes,elapsed_seconds=elapsed,tasks=sample_count,
    document_dispatch_monotonic_seconds=starts,first_to_last_dispatch_seconds=starts[-1]-starts[0],
    observed_request_dispatches_per_hour=3600*(len(starts)-1)/(starts[-1]-starts[0]),
    whole_pipeline_completed_records_per_hour=3600*sample_count/elapsed,
    all_full_records_equal_except_current_capture_timestamp=True,peak_capture_inflight=peak,
    parser_pids=sorted({r['local_parser_pid'] for r in bridge.outcomes.values()}),minimum_start_interval=minimum))
 ratio=None
 for row in results:
  stamps=row['document_dispatch_monotonic_seconds']
  row['last_24_dispatches_per_hour']=3600*23/(stamps[-1]-stamps[-24])
  row['last_12_dispatches_per_hour']=3600*11/(stamps[-1]-stamps[-12])
  row['request_count_first_3600_seconds']=sum(t<=stamps[0]+3600 for t in stamps)
  row['target_1000_requests_in_hour_verified']=None if sample_count<1000 else row['request_count_first_3600_seconds']>=1000
 report=dict(at=datetime.now(timezone.utc).isoformat(),passed=True,results=results,dual_vs_three_throughput_ratio=ratio,
  sample_count=sample_count,benchmark_below_normal_CPU_priority=(os.name=='nt'),
  source_sha256=__import__('hashlib').sha256((W/'src/amazon_es_bestseller/collection/detail_dual_runtime5500.py').read_bytes()).hexdigest(),
  access_source_sha256=__import__('hashlib').sha256((W/'src/amazon_es_bestseller/collection/detail_dual_access5500.py').read_bytes()).hexdigest(),
  scheduler_source_sha256=__import__('hashlib').sha256((W/'src/amazon_es_bestseller/collection/detail_dual5500.py').read_bytes()).hexdigest(),
  sample_html_sha256=[__import__('hashlib').sha256(p[1].encode('utf8')).hexdigest() for p in packets],transport_simulated_seconds=delay,transport_measurement_samples=len(durations),real_full_authorization_each_transaction=True,
  benchmark_clock_implementation=time.get_clock_info('perf_counter').implementation,benchmark_clock_resolution=time.get_clock_info('perf_counter').resolution,production_network_requests=0,production_checkpoint_writes=0,not_LIVE_speed=True,not_deployment_approval=True,
  limitation='One saved-page sample with simulated recent median document response latency; changing network and page mix remain uncertain.')
 immutable_json(R/target,report);print(json.dumps(report,ensure_ascii=True))
if __name__=='__main__':main()
