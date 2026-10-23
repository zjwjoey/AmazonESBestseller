"""Five real local browser windows, including a real 302 root-document chain."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from amazon_es_bestseller.collection.detail_parallel_browser import ParallelBrowserCapture
from amazon_es_bestseller.collection.detail_parallel_runtime import ParallelDetailRunner
from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2
from amazon_es_bestseller.multisource.storage import read_sealed
from amazon_es_bestseller.multisource.storage import immutable_json,sealed
from amazon_es_bestseller.collection.detail_parallel_authorization import SharedDetailGates


async def run(args):
    from playwright.async_api import async_playwright
    rows=[json.loads(s) for s in args.inputs.read_text(encoding='utf8').splitlines()][:5]
    bodies={row['asin']:Path(row['path']).read_bytes() for row in rows}
    for row in rows:assert hashlib.sha256(bodies[row['asin']]).hexdigest()==row['sha256']
    first=rows[0]['asin'];requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            asin=self.path.rsplit('/',1)[-1]
            if args.challenge_first and self.path=='/dp/'+first:
                body=b'<html><body>Robot Check<form action="/errors/validateCaptcha">Local test challenge</form></body></html>'
                self.send_response(200);self.send_header('Content-Length',str(len(body)))
                self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body);return
            if self.path=='/dp/'+first:
                self.send_response(302);self.send_header('Location','/resolved/dp/'+first)
                self.send_header('Content-Length','0');self.end_headers();return
            if asin not in bodies:self.send_error(404);return
            body=bodies[asin]
            self.send_response(200);self.send_header('Content-Length',str(len(body)))
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; img-src data:")
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    tasks=[{'task_id':r['asin'],'asin':r['asin'],'request_url':base+'/dp/'+r['asin'],
            'candidate_id':'local_'+r['asin'],'selection_id':'local_redirect'} for r in rows]
    try:
        with tempfile.TemporaryDirectory(prefix='detail_redirect_local_') as temp:
            paths=[Path(temp)/'gate_one.json',Path(temp)/'gate_two.json']
            for path in paths:immutable_json(path,sealed({'state':'NORMAL'}))
            gates=SharedDetailGates(paths)
            async with async_playwright() as playwright:
                context=await playwright.chromium.launch_persistent_context(str(Path(temp)/'profile'),headless=False)
                try:
                    capture=ParallelBrowserCapture(context,check_gate=gates.require_normal,localhost_only=True)
                    await capture.open()
                    runner=ParallelDetailRunner(Path(temp)/'store',{'run_id':'local_redirect','tasks':tasks},
                        {'max_requests':5,'prior_requests':0,'budget_holds':0},capture=capture,
                        check_gate=gates.require_normal,stop_gate=gates.stop,postal_code='28071')
                    try:summary=await runner.run()
                    finally:shutil.copytree(runner.store.root,args.output.parent/(args.output.stem+'_evidence'))
                    if args.challenge_first:
                        assert summary['state']=='ACCESS_STOP' and summary['successful']==0
                        documents=[path for path in requests if path.startswith('/dp/') or path.startswith('/resolved/dp/')]
                        assert len(documents)==1,requests
                        assert all(read_sealed(path)['state']=='CHALLENGE' for path in paths)
                        failed=next(row['result'] for row in runner.store.outcomes() if row['result']['status']=='ACCESS_STOP')
                        receipt=read_sealed(runner.store.root/failed['receipt_path'])['capture']
                        assert receipt['status_code']==200 and receipt['render_snapshot_count']==1
                        return {'passed':True,'origin':'ISOLATED_REAL_CHROMIUM_LOCAL_HTTP_ONLY',
                            'scenario':'HTTP_200_CHALLENGE_STOPS_ALL_NEW_REQUESTS',
                            'real_http_requests':len(requests),'real_document_requests':len(documents),
                            'request_paths':requests,'gate_states':[read_sealed(path)['state'] for path in paths],
                            'summary':summary,'challenge_receipt':receipt,
                            'production_profile_touched':False,'amazon_network_allowed':False}
                    assert summary['successful']==5 and summary['failed']==0
                    assert len(set(capture.window_ids))==5 and len(set(summary['local_parser_pids']))==5
                    starts=[];redirect=None
                    for outcome in runner.store.outcomes():
                        task=outcome['task'];result=outcome['result']
                        receipt=read_sealed(runner.store.root/result['receipt_path'])['capture']
                        starts.append(receipt['native_document_start']['timestamp'])
                        record=dict(result['record']);record.pop('local_parser_pid')
                        expected=parse_detail_evidence_v2(bodies[task['asin']].decode('utf8'),task['asin'],
                            requested_url=task['request_url'],final_url=receipt['final_url'],ranking_context=task)
                        assert record==expected
                        if task['asin']==first:
                            assert receipt['native_document_start']['url']==task['request_url']
                            assert [r['status_code'] for r in receipt['document_responses']]==[302,200]
                            redirect=receipt
                    starts.sort()
                    assert all(b-a>=3.6 for a,b in zip(starts,starts[1:]))
                    assert len(requests)==6
                    return {'passed':True,'origin':'ISOLATED_REAL_CHROMIUM_LOCAL_HTTP_ONLY',
                        'window_ids':capture.window_ids,'parser_pids':summary['local_parser_pids'],
                        'logical_tasks':5,'real_http_hops':len(requests),'native_root_starts':len(starts),
                        'min_initial_root_gap':min(b-a for a,b in zip(starts,starts[1:])),
                        'all_full_records_equal':True,'redirect_receipt':redirect,
                        'production_profile_touched':False,'amazon_network_allowed':False}
                finally:await context.close()
    finally:server.shutdown();server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--challenge-first',action='store_true')
    args=parser.parse_args()
    if args.output.exists():parser.error('choose a fresh output file')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    try:result=asyncio.run(run(args))
    except Exception as exc:
        args.output.write_text(json.dumps({'passed':False,'error':str(exc)},ensure_ascii=True),encoding='utf8')
        raise
    args.output.write_text(json.dumps(result,ensure_ascii=True,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('redirect_receipt','challenge_receipt')},ensure_ascii=True))
