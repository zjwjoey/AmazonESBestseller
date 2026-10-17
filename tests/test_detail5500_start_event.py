"""Actual missing-start receipt plus explicitly synthetic cached-redirect guard cases."""
import asyncio,json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from amazon_es_bestseller.collection import detail_parallel_runtime5500 as mod

def collector(tmp_path):
 c=mod.ParallelCollector(NS(root=tmp_path,plan={}),{'request_start_interval_seconds':3})
 main=object();c.pages=[NS(main_frame=main)]
 url='https://www.amazon.es/dp/B000000001';sent=[]
 c.tokens[0]={'task':{'request_url':url},'receipt':{},'sent':False,'notify_sent':lambda:sent.append(True)}
 return c,main,url,sent

def test_first_observed_scoped_redirect_route_marks_one_start(tmp_path):
 c,frame,url,sent=collector(tmp_path);root=NS(url=url,redirected_from=None)
 req=NS(resource_type='document',method='GET',url=url,frame=frame,redirected_from=root,is_navigation_request=lambda:True)
 actions=[]
 async def proceed():actions.append('continue')
 async def abort():actions.append('abort')
 asyncio.run(c.route(NS(request=req,continue_=proceed,abort=abort)))
 assert sent==[True] and actions==['continue']
 assert c.tokens[0]['receipt']['document_start_evidence_kind']=='FIRST_OBSERVED_SCOPED_MAIN_DOCUMENT_ROUTE'
 # A later user reload must not become a second request using the same token.
 req.redirected_from=None
 asyncio.run(c.route(NS(request=req,continue_=proceed,abort=abort)))
 assert sent==[True] and actions==['continue','abort']

@pytest.mark.parametrize('bad',['foreign_root','foreign_frame','post','restriction'])
def test_same_guard_never_accepts_unsolicited_or_restricted_navigation(tmp_path,bad):
 c,frame,url,sent=collector(tmp_path);root=NS(url=url,redirected_from=None)
 req=NS(resource_type='document',method='GET',url=url,frame=frame,redirected_from=root,is_navigation_request=lambda:True)
 if bad=='foreign_root':root.url='https://www.amazon.es/dp/B000000002'
 elif bad=='foreign_frame':req.frame=object()
 elif bad=='post':req.method='POST'
 else:c.scheduler.stop.set()
 actions=[]
 async def proceed():actions.append('continue')
 async def abort():actions.append('abort')
 asyncio.run(c.route(NS(request=req,continue_=proceed,abort=abort)))
 assert not sent and actions==['abort']

def test_actual_receipt_remains_honest_about_missing_dispatch_evidence():
 p=Path(__file__).parent/'fixtures/detail_bridge5500/actual_parallel_unobserved_start_receipt.json'
 before=p.read_bytes();v=json.loads(before)['payload']
 assert v['state']=='NORMAL' and v['status_code']==200 and v['document_responses']
 assert 'document_started_at' not in v and v.get('document_response_at')
 assert p.read_bytes()==before  # Never retrofit a request start from its response.


def test_each_page_installs_its_own_guard_before_navigation(tmp_path):
 c,frame,url,sent=collector(tmp_path);events=[]
 class Page:
  async def route(self,pattern,handler):events.append(('guard',pattern,handler))
  def on(self,event,handler):events.append(('event',event))
 asyncio.run(c.configure_page(0,Page()))
 assert events[0]==('guard','**/*',c.route)
 assert ('event','response') in events and ('event','request') in events and ('event','requestfailed') in events


def test_request_event_releases_dispatch_without_inventing_response(tmp_path):
 c,frame,url,sent=collector(tmp_path)
 req=NS(resource_type='document',method='GET',url=url,frame=frame,redirected_from=None,is_navigation_request=lambda:True)
 c.observe_request(0,req,'REQUEST')
 assert sent==[True] and c.tokens[0]['receipt']['document_start_evidence_kind']=='PLAYWRIGHT_SCOPED_MAIN_DOCUMENT_REQUEST_EVENT'
 assert 'status_code' not in c.tokens[0]['receipt'] and 'document_response_at' not in c.tokens[0]['receipt']
 decisions=[]
 async def proceed():decisions.append('continue')
 async def abort():decisions.append('abort')
 asyncio.run(c.route(NS(request=req,continue_=proceed,abort=abort)))
 assert sent==[True] and decisions==['continue']
 other=NS(**vars(req))
 c.observe_request(0,other,'REQUEST')
 asyncio.run(c.route(NS(request=other,continue_=proceed,abort=abort)))
 assert sent==[True] and decisions==['continue','abort']

@pytest.mark.parametrize('bad',['not_requested','foreign_root','foreign_frame','post','restriction','failed_event'])
def test_request_event_cannot_release_unapproved_dispatch(tmp_path,bad):
 c,frame,url,sent=collector(tmp_path)
 req=NS(resource_type='document',method='GET',url=url,frame=frame,redirected_from=None,is_navigation_request=lambda:True)
 kind='REQUEST'
 if bad=='not_requested':c.tokens[0]['navigation_requested']=False
 elif bad=='foreign_root':req.url='https://www.amazon.es/dp/B000000002'
 elif bad=='foreign_frame':req.frame=object()
 elif bad=='post':req.method='POST'
 elif bad=='restriction':c.scheduler.stop.set()
 else:kind='REQUEST_FAILED'
 c.observe_request(0,req,kind)
 assert not sent and 'document_started_at' not in c.tokens[0]['receipt']
