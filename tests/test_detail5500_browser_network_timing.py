import copy
from types import SimpleNamespace as NS
import pytest
from amazon_es_bestseller.collection.browser_network_timing5500 import NetworkTimingRecorder,network_overlap

def token(url='https://www.amazon.es/dp/B000000001'):
 return dict(task=dict(task_id='t',request_url=url),navigation_requested=True)
def req(i='r',url='https://www.amazon.es/dp/B000000001',**kw):
 return dict(requestId=i,type='Document',frameId='main',timestamp=1000,wallTime=9000,request=dict(url=url,method='GET'),**kw)
def resp(i='r',start=10,send=0,headers=1000,**kw):
 return dict(requestId=i,timestamp=2000,response=dict(url='https://www.amazon.es/dp/B000000001',status=200,protocol='h2',fromDiskCache=False,fromServiceWorker=False,fromPrefetchCache=False,timing=dict(requestTime=start,sendStart=send,sendEnd=send+1,receiveHeadersStart=headers,receiveHeadersEnd=headers+1),**kw))
def example(start=10,lane=0):
 n=NetworkTimingRecorder();t=token();n.request(lane,'main',t,req());n.response(lane,resp(start=start));n.finished(lane,dict(requestId='r',timestamp=start+2));return n.snapshot(t)

def test_browser_times_are_not_python_callback_times():
 s=example();v=network_overlap([s]);assert v['request_to_first_byte_peak']==1
 row=v['intervals'][0];assert row['send_monotonic_seconds']==10 and row['first_byte_monotonic_seconds']==11
 assert row['body_end_monotonic_seconds']==12

def test_real_overlaps_count_across_lanes_one_browser_clock():
 n=NetworkTimingRecorder();snap=[]
 for i in range(3):
  t=token();n.request(i,'main',t,req(str(i)));n.response(i,resp(str(i),start=10+i*.2));n.finished(i,dict(requestId=str(i),timestamp=12+i*.2));snap.append(n.snapshot(t))
 v=network_overlap(snap);assert v['request_to_first_byte_peak']==v['request_to_body_end_peak']==3 and v['coverage_complete']

@pytest.mark.parametrize('bad',['wrong_frame','wrong_root','post','not_requested','subresource'])
def test_unowned_events_not_attached_to_candidate(bad):
 n=NetworkTimingRecorder();t=token();r=req()
 if bad=='wrong_frame':r['frameId']='other'
 elif bad=='wrong_root':r['request']['url']='https://www.amazon.es/dp/B000000002'
 elif bad=='post':r['request']['method']='POST'
 elif bad=='not_requested':t['navigation_requested']=False
 else:r['type']='Script'
 n.request(0,'main',t,r);assert n.snapshot(t)['requests']==[]

@pytest.mark.parametrize('bad',[-1,None,float('nan'),float('inf'),True,'0'])
def test_bad_browser_time_is_unknown_never_filled_from_callback(bad):
 s=example();s['requests'][0]['response_timing']['sendStart']=bad
 v=network_overlap([s]);assert v['unknown_http_requests']==1 and v['request_to_first_byte_peak'] is None

@pytest.mark.parametrize('flag',['fromDiskCache','fromServiceWorker','fromPrefetchCache'])
def test_cache_or_serviceworker_not_counted_as_network(flag):
 s=example();s['requests'][0]['cache'][flag]=True
 v=network_overlap([s]);assert v['excluded_cache_requests']==1 and v['request_to_first_byte_peak'] is None

def test_missing_body_completion_does_not_invent_end():
 s=example();s['requests'][0].pop('loading_finished_monotonic_seconds')
 v=network_overlap([s]);assert v['request_to_first_byte_peak']==1 and v['request_to_body_end_peak'] is None and v['body_coverage_complete'] is False

def test_snapshots_are_independent_of_late_callbacks():
 n=NetworkTimingRecorder();t=token();n.request(0,'main',t,req());s=n.snapshot(t);n.response(0,resp());assert 'response_timing' not in s['requests'][0]

def test_redirect_hops_keep_original_owner_and_separate_timings():
 n=NetworkTimingRecorder();t=token();n.request(0,'main',t,req())
 red=resp()['response'];r=req(url='https://www.amazon.es/dp/B000000002',redirectResponse=red);n.request(0,'main',t,r)
 s=n.snapshot(t);assert len(s['requests'])==2 and s['requests'][0]['url']!=s['requests'][1]['url'] and s['requests'][0]['task_id']==s['requests'][1]['task_id']=='t'

def test_different_browser_sessions_not_silently_combined():
 a=example();b=example();assert a['session_id']!=b['session_id']
 with pytest.raises(ValueError,match='CLOCK_SESSION'):network_overlap([a,b])

def test_duplicate_snapshots_do_not_inflate_network_peak():
 s=example();v=network_overlap([s,copy.deepcopy(s)])
 assert v['measured_http_requests']==1 and v['request_to_first_byte_peak']==1
 assert v['identical_duplicate_records_ignored']==1
 other=copy.deepcopy(s);other['requests'][0]['response_timing']['sendStart']=2
 with pytest.raises(ValueError,match='CONFLICTING'):network_overlap([s,other])

def test_passive_attachment_never_navigates_reloads_or_changes_network_policy():
 import asyncio
 events=[];commands=[]
 class CDP:
  async def send(self,name):
   commands.append(name)
   return {'frameTree':{'frame':{'id':'main'}}} if name=='Page.getFrameTree' else {}
  def on(self,name,callback):events.append(name)
 n=NetworkTimingRecorder();asyncio.run(n.attach(CDP(),0,lambda:None))
 assert commands==['Page.getFrameTree','Network.enable']
 assert set(events)=={'Network.requestWillBeSent','Network.responseReceived','Network.loadingFinished','Network.loadingFailed','Network.requestServedFromCache'}

def test_response_headers_and_credentials_are_not_retained():
 n=NetworkTimingRecorder();t=token();n.request(0,'main',t,req());r=resp()
 r['response']['headers']={'Set-Cookie':'private'};r['response']['securityDetails']={'certificateId':123}
 n.response(0,r);row=n.snapshot(t)['requests'][0]
 assert 'headers' not in row and 'securityDetails' not in row
