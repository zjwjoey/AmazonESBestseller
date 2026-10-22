from types import SimpleNamespace
import pytest
from amazon_es_bestseller.transport.playwright import PlaywrightTransport

URL='https://www.amazon.es/acp/test'

class Page:
    url='https://www.amazon.es/gp/bestsellers'
    def __init__(self,error):self.error=error;self.listeners={};self.calls=0
    def on(self,event,callback):self.listeners.setdefault(event,[]).append(callback)
    def remove_listener(self,event,callback):self.listeners[event].remove(callback)
    def evaluate(self,script,args):
        self.calls+=1
        other=SimpleNamespace(url=URL,method='POST',post_data='unrelated',failure='net::ERR_ABORTED')
        req=SimpleNamespace(url=URL,method='POST',post_data='approved',failure='net::ERR_CONNECTION_RESET')
        for cb in self.listeners.get('requestfailed',[]):cb(other);cb(req)
        raise self.error

def test_browser_network_failure_is_exact_bound_preserved_without_retry():
    error=RuntimeError('Page.evaluate: TypeError: Failed to fetch');page=Page(error)
    t=PlaywrightTransport(SimpleNamespace(page=page))
    with pytest.raises(RuntimeError) as caught:t.fetch_ajax(URL,method='POST',payload='approved')
    assert caught.value is error and page.calls==1
    d=error.transport_diagnostics
    assert d['browser_failure']=='net::ERR_CONNECTION_RESET'
    assert d['HTTP_status_observed'] is None and d['network_requests_retried']==0
    assert all(not callbacks for callbacks in page.listeners.values())
    assert t._manual_acp_allowance is None

def test_missing_browser_failure_remains_unknown_and_original_exception():
    error=RuntimeError('Failed to fetch');page=Page(error)
    page.evaluate=lambda *args:(_ for _ in ()).throw(error)
    t=PlaywrightTransport(SimpleNamespace(page=page))
    with pytest.raises(RuntimeError) as caught:t.fetch_ajax(URL,method='POST',payload='approved')
    assert caught.value is error
    assert error.transport_diagnostics['browser_failure'] is None
    assert error.transport_diagnostics['route_decision'] is None

@pytest.mark.parametrize('valid', [True,False])
def test_route_diagnostic_observes_nonce_decision_without_relaxing_guard(valid):
    error=RuntimeError('Failed to fetch')
    class Routed(Page):
        def route(self,pattern,callback):self.capture=callback
        def evaluate(self,script,args):
            self.calls+=1
            headers=dict(args['headers'])
            if not valid:headers['x-codex-acp-request-nonce']='wrong'
            request=SimpleNamespace(url=URL,method='POST',post_data='approved',headers=headers,resource_type='fetch')
            self.wire=[];self.aborted=False
            route=SimpleNamespace(request=request,continue_=lambda **kw:self.wire.append(kw),abort=lambda:setattr(self,'aborted',True))
            self.capture(route)
            raise error
    page=Routed(error);t=PlaywrightTransport(SimpleNamespace(page=page));t.install_bounded_acp_capture()
    with pytest.raises(RuntimeError):t.fetch_ajax(URL,method='POST',payload='approved')
    assert page.calls==1 and bool(page.wire)==valid and page.aborted!=valid
    if valid:assert 'x-codex-acp-request-nonce' not in page.wire[0]['headers']
    assert error.transport_diagnostics['route_decision']==('CONTROLLED_REQUEST_CONTINUED' if valid else 'CONTROLLED_NONCE_MISMATCH_NOT_ALLOWED')

def test_runner_persists_diagnostics_and_keeps_network_stop(tmp_path):
    from test_highspeed_runner import plan,approval,Store,make_runner,NOW,parser
    p=plan(tmp_path,'T1');a=approval(p,tmp_path);s=Store(tmp_path/'store')
    error=RuntimeError('Failed to fetch')
    error.transport_diagnostics={'browser_failure':'net::ERR_CONNECTION_RESET','route_decision':'CONTROLLED_REQUEST_CONTINUED'}
    class Broken:
        def fetch_page(self,url):raise error
    result=make_runner(s,clock=lambda:NOW,parser=parser,gate_reader=lambda:{'state':'NORMAL'}).run(p,authorization=a,transport=Broken())
    assert len(s.responses)==1 and s.responses[0]['transport_diagnostics']==error.transport_diagnostics
    assert result['run']['stop_reason']=='NETWORK_ERROR'

@pytest.mark.parametrize('code,state',[(403,'BLOCKED'),(429,'RATE_LIMITED')])
def test_body_failure_never_masks_actual_restriction(tmp_path,code,state):
    from test_highspeed_runner import plan,approval,Store,make_runner,NOW,parser
    p=plan(tmp_path,'T1');a=approval(p,tmp_path);s=Store(tmp_path/'store')
    error=RuntimeError('Failed to fetch');error.transport_diagnostics={'HTTP_status_observed':code}
    class Broken:
        def fetch_page(self,url):raise error
    result=make_runner(s,clock=lambda:NOW,parser=parser,gate_reader=lambda:{'state':'NORMAL'}).run(p,authorization=a,transport=Broken())
    assert len(s.requests)==1 and s.responses[0]['status_code']==code
    assert result['run']['stop_reason']==state
