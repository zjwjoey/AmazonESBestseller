"""Isolated localhost only. Never uses the production profile or collector store."""
import asyncio,json,sys,tempfile,time,threading,subprocess
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from datetime import datetime,timezone,timedelta
W=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(W/'tests'))
from test_quick_selection_5500 import auth_for
from amazon_es_bestseller.collection import detail_dual_runtime5500 as mod
from amazon_es_bestseller.collection import detail_five_runtime5500 as five
from amazon_es_bestseller.collection.detail_bridge5500 import DetailBridge5500
from amazon_es_bestseller.multisource.storage import immutable_json,read_sealed,sealed
ORIGINAL_LOCAL_PARSER=mod.parse_captured_detail
def delayed_local_fixture_parser(*args):
 time.sleep(20) # Test-only: let both fast HTTP captures precede the intentional non-product parser failure.
 return ORIGINAL_LOCAL_PARSER(*args)
R=Path(r'F:\AmazonESBestseller\runtime\quick_selection_detail_5500_20261016')
PREFIX=sys.argv[1] if len(sys.argv)>1 else 'true_parallel_local_http'
assert Path(PREFIX).name==PREFIX
events=[];lock=threading.Lock();active=0;peak=0
class Server(BaseHTTPRequestHandler):
 def do_GET(self):
  global active,peak
  if not self.path.startswith('/dp/'):
   self.send_response(404);self.send_header('Content-Length','0');self.end_headers();return
  with lock:
   active+=1;peak=max(peak,active);events.append(dict(path=self.path,kind='start',at=time.monotonic()))
  time.sleep(float(sys.argv[2]) if len(sys.argv)>2 else 22.25)
  body=b'<html><title>Local asynchronous transport test</title><div id="ppd">Local fixture</div></html>'
  self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  with lock:
   active-=1;events.append(dict(path=self.path,kind='response',at=time.monotonic()))
 def log_message(self,*a):pass
async def smoke(base,port):
 from playwright.async_api import async_playwright
 tasks=[dict(task_id=f'local{i}',candidate_id=f'local{i}',selection_id='test',run_id='test',
  request_url=f'http://127.0.0.1:{port}/dp/B00000000{i}') for i in range(5)]
 bridge=DetailBridge5500(base/'run',dict(run_id='test',tasks=tasks,shared_gate_path=str(base/'shared_gate.json')))
 auth=auth_for(bridge,base);auth['deadline']=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat()
 immutable_json(bridge.root/'access_gate.json',sealed({'state':'NORMAL'}))
 # Test-only allowance has no CLI flag and no production authorization.
 mod.safe_url=lambda u:u.startswith(f'http://127.0.0.1:{port}/')
 mod.validate_parallel_mode=lambda *a:None
 if len(sys.argv)>2 and float(sys.argv[2])<1:mod.parse_captured_detail=delayed_local_fixture_parser
 five.validate_parallel_mode=lambda *a:None
 c=five.FiveCollector(bridge,auth)
 async with async_playwright() as pw:
  context=await pw.chromium.launch_persistent_context(str(base/'isolated-local-test-profile'),headless=False,service_workers='block')
  try:
   try:await c.run(context,tasks)
   except ValueError as e:
    assert 'SAFETY_REVIEW' in str(e),str(e)
   immutable_json(R/(PREFIX+'_smoke_observed.json'),dict(events=events,peak=peak,windows=c.windows))
   from amazon_es_bestseller.collection.browser_network_timing5500 import network_overlap
   assert len(set(c.windows))==5 and len(set(c.ready_parser_pids))==5
   expected_peak=5 if (float(sys.argv[2]) if len(sys.argv)>2 else 22.25)>3.6 else 1
   assert peak==expected_peak
   starts=[e['at'] for e in events if e['kind']=='start'];responses=[e['at'] for e in events if e['kind']=='response']
   assert len(starts)==len(responses)==5
   if expected_peak==5:assert starts[-1]<min(responses)
   assert min(b-a for a,b in zip(starts,starts[1:]))>=3.59
   rows=[read_sealed(p) for p in (bridge.root/'receipts').glob('*.json')]
   assert len(rows)==5 and all(r['status_code']==200 and r['state']!='RESERVED' for r in rows)
   measured=network_overlap([v['browser_network_timing'] for v in rows])
   assert measured['coverage_complete'] and measured['body_coverage_complete'],measured
   assert measured['request_to_first_byte_peak']==measured['request_to_body_end_peak']==peak,measured
   result=dict(at=datetime.now(timezone.utc).isoformat(),passed=True,production_requests=0,
    browser_origin_measurement=measured,exactly_five_local_document_requests=True,raw_browser_network_snapshots=[r["browser_network_timing"] for r in rows],
    offline_invalid_fixture_parser_delay_seconds=20 if len(sys.argv)>2 and float(sys.argv[2])<1 else 0,
    production_profile_used=False,production_checkpoint_writes=0,server_peak_real_HTTP_inflight=peak,
    both_HTTP_started_before_first_response=starts[-1]<min(responses),global_start_intervals=[b-a for a,b in zip(starts,starts[1:])],
    distinct_visible_window_ids=c.windows,independent_parser_pids=c.ready_parser_pids,processing_groups=[3,2],receipt_processing_groups=[r["processing_group"] for r in rows],events=events,both_raw_receipts_preserved=True,
    local_fixture_not_product_success=True,explicit_test_only_localhost_route_override=True,
    not_deployment_approval=True,code_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=W,text=True).strip())
   immutable_json(R/(PREFIX+'_chromium_smoke.json'),result)
   print(json.dumps(result,ensure_ascii=True))
  finally:await context.close()
def main():
 server=ThreadingHTTPServer(('127.0.0.1',0),Server)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:
  with tempfile.TemporaryDirectory(prefix='detail-five-local-http-') as p:asyncio.run(smoke(Path(p),server.server_port))
 finally:server.shutdown();server.server_close()
if __name__=='__main__':main()
