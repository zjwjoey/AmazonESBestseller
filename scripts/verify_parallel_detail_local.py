"""Real Chromium/local HTTP benchmark using hash-bound saved product HTML.

External requests are blocked by CSP and an adapter-level loopback-only route.
Uses temporary profiles/stores and never writes to the saved input directory.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from amazon_es_bestseller.collection.detail_parallel_browser import ParallelBrowserCapture
from amazon_es_bestseller.collection.detail_parallel_runtime import ParallelDetailRunner
from amazon_es_bestseller.collection.detail_parallel_runtime import parse_detail_task
from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2
from amazon_es_bestseller.access.location import inspect_delivery_location


def injected_parser(task,body,metadata,postal_code):
    if task.get('local_test_parser_failure'):
        raise ValueError('LOCAL_TEST_INJECTED_ITEM_PARSE_FAILURE')
    return parse_detail_task(task,body,metadata,postal_code)


async def run(args):
    from playwright.async_api import async_playwright
    repo=Path(__file__).resolve().parents[1]
    code_files=[*sorted((repo/'src').rglob('*.py')),Path(__file__).resolve()]
    code_hashes={str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest() for p in code_files}
    sources=[]
    for line in args.inputs.read_text(encoding='utf8').splitlines():
        row=json.loads(line)
        if row['record'].get('identity_status')!='IDENTITY_MATCH':continue
        body=Path(row['path']).read_bytes()
        if hashlib.sha256(body).hexdigest()!=row['sha256']:raise ValueError('INPUT_CHANGED')
        if inspect_delivery_location(body.decode('utf8')).postal_code!=args.postal_code:continue
        sources.append((row['asin'],body,row['sha256']))
        if len(sources)==args.count:break
    if len(sources)!=args.count:raise ValueError('INSUFFICIENT_MATCHED_SAVED_PAGES')
    bodies={asin:body for asin,body,_ in sources}
    lock=threading.Lock();server_stats={'active':0,'peak':0,'starts':[]}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            asin=self.path.rsplit('/',1)[-1]
            if asin not in bodies:self.send_error(404);return
            with lock:
                server_stats['active']+=1
                server_stats['peak']=max(server_stats['peak'],server_stats['active'])
                server_stats['starts'].append({'asin':asin,'at':time.perf_counter()})
            try:
                time.sleep(args.delay)
                body=bodies[asin]
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; img-src data:")
                self.end_headers();self.wfile.write(body)
            finally:
                with lock:server_stats['active']-=1
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    tasks=[{'task_id':f'task_{asin}','candidate_id':f'candidate_{asin}',
            'selection_id':'same_saved_input','asin':asin,'request_url':base+'/dp/'+asin}
           for asin,_,_ in sources]
    plan={'run_id':'local_controlled_validation','tasks':tasks}
    if args.inject_parser_error:tasks[0]['local_test_parser_failure']=True
    metrics={'peak_rss_bytes':None,'cpu_seconds':None,
             'measurement_scope':'peak sampled process-tree totals, including setup; exited children may be missed'}
    stop_monitor=threading.Event()
    try:
        import psutil
        process=psutil.Process()
        def monitor():
            while not stop_monitor.is_set():
                rss=0;cpu=0
                for proc in [process,*process.children(recursive=True)]:
                    try:
                        rss+=proc.memory_info().rss;t=proc.cpu_times();cpu+=t.user+t.system
                    except psutil.Error:pass
                metrics['peak_rss_bytes']=max(metrics['peak_rss_bytes'] or 0,rss)
                metrics['cpu_seconds']=max(metrics['cpu_seconds'] or 0,cpu)
                stop_monitor.wait(.2)
        threading.Thread(target=monitor,daemon=True).start()
    except ImportError:pass
    try:
        with tempfile.TemporaryDirectory(prefix='detail_parallel_local_') as temp:
            async with async_playwright() as playwright:
                context=await playwright.chromium.launch_persistent_context(str(Path(temp)/'profile'),headless=False)
                try:
                    capture=ParallelBrowserCapture(context,lanes=args.lanes,
                        check_gate=lambda:None,localhost_only=True)
                    await capture.open()
                    runner=ParallelDetailRunner(Path(temp)/'store',plan,
                        {'max_requests':args.count,'prior_requests':0,'budget_holds':0},
                        capture=capture,check_gate=lambda:None,
                        stop_gate=lambda state:None,lanes=args.lanes,postal_code=args.postal_code,
                        process_parser=injected_parser if args.inject_parser_error else parse_detail_task)
                    start=time.perf_counter()
                    try:
                        summary=await runner.run()
                    finally:
                        evidence=args.output.parent/(args.output.stem+'_evidence')
                        shutil.copytree(runner.store.root,evidence)
                    elapsed=time.perf_counter()-start
                    outcomes=runner.store.outcomes()
                    pids=set();native=[];record_hashes={}
                    for row in outcomes:
                        task=row['task'];result=row['result']
                        from amazon_es_bestseller.multisource.storage import read_sealed
                        receipt=read_sealed(runner.store.root/result['receipt_path'])
                        native.append(receipt['capture']['native_document_start']['timestamp'])
                        if result['status']!='SUCCESS':
                            if (task.get('local_test_parser_failure') and result['status']=='PARSE_FAILED'
                                    and 'LOCAL_TEST_INJECTED_ITEM_PARSE_FAILURE' in result.get('reason','')):continue
                            raise AssertionError({'result':result,'browser_diagnostics':capture.diagnostics})
                        record=dict(result['record']);pids.add(record.pop('local_parser_pid'))
                        original=bodies[task['asin']].decode('utf8')
                        expected=parse_detail_evidence_v2(original,task['asin'],requested_url=task['request_url'],
                                                          final_url=task['request_url'],ranking_context=task)
                        if record!=expected:
                            raise AssertionError({'asin':task['asin'],'different_fields':[k for k in set(record)|set(expected) if record.get(k)!=expected.get(k)]})
                        normalized=json.dumps(record,ensure_ascii=True,sort_keys=True).replace(base,'LOCAL_TEST_ORIGIN')
                        record_hashes[task['asin']]=hashlib.sha256(normalized.encode()).hexdigest()
                    native.sort()
                    assert len(set(summary['local_parser_pids']))==args.lanes
                    assert len(capture.window_ids)==len(set(capture.window_ids))==args.lanes
                    assert all(b-a>=3.6 for a,b in zip(native,native[1:]))
                    # Resume against the same evidence, with no new requests.
                    count_before=len(server_stats['starts'])
                    await runner.run()
                    assert len(server_stats['starts'])==count_before
                    assert code_hashes=={str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest() for p in code_files}
                    return {'passed':True,'origin':'ISOLATED_REAL_CHROMIUM_LOCAL_HTTP_ONLY',
                        'lanes':args.lanes,'count':args.count,'delay_seconds':args.delay,
                        'elapsed_seconds':elapsed,'successful_details_per_hour':summary['successful']/elapsed*3600,
                        'injected_item_failures':int(args.inject_parser_error),
                        'full_records_equivalent':True,'window_ids':capture.window_ids,
                        'record_equivalence_scope':'successful_outputs_only',
                        'successful_records_compared':summary['successful'],
                        'parser_pids':sorted(pids),'native_start_min_gap':min((b-a for a,b in zip(native,native[1:])),default=None),
                        'server_http_peak':server_stats['peak'],'server_requests':len(server_stats['starts']),
                        'resume_new_requests':0,'production_profile_touched':False,'amazon_network_allowed':False,
                        'input_sha256':{asin:sha for asin,_,sha in sources},'runtime':summary,'resources':metrics,
                        'source_sha256':code_hashes,'normalized_full_record_sha256':record_hashes}
                finally:await context.close()
    finally:
        stop_monitor.set();server.shutdown();server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--lanes',type=int,choices=[1,3,5],default=5)
    parser.add_argument('--count',type=int,default=10)
    parser.add_argument('--delay',type=float,default=20)
    parser.add_argument('--postal-code',default='28071')
    parser.add_argument('--inject-parser-error',action='store_true')
    args=parser.parse_args()
    if args.output.exists():parser.error('output exists; choose a new file')
    try:
        result=asyncio.run(run(args))
    except Exception as exc:
        result={'passed':False,'error':type(exc).__name__+': '+str(exc)}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=True,indent=2),encoding='utf8')
        raise
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=True,indent=2),encoding='utf8')
    print(json.dumps({key:result[key] for key in ('passed','lanes','count','elapsed_seconds',
        'successful_details_per_hour','server_http_peak','runtime')},ensure_ascii=True))
