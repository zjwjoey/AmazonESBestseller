"""Actual initialized DOM may consume ACP attributes; it cannot supply a POST."""
from pathlib import Path
import hashlib,json
from bs4 import BeautifulSoup
import pytest
from amazon_es_bestseller.transport.playwright import PlaywrightTransport
from amazon_es_bestseller.highspeed.adapters import _scope
from types import SimpleNamespace

def initialized_owner(phase='before'):
    path=Path('tests/fixtures/highspeed/real_v3_t1_initialized_carousel_'+phase+'.html')
    proof=json.loads(path.with_suffix('.provenance.json').read_text(encoding='utf8'))
    assert hashlib.sha256(path.read_bytes()).hexdigest()==proof['fixture_sha256']
    soup=BeautifulSoup(path.read_bytes(),'lxml');wrapper=soup.select_one('[data-card-metrics-id^="p13n-zg-list-carousel"]')
    node=wrapper.select_one('[data-a-carousel-options]');url=proof['source_url']
    owner={'owner_id':wrapper['id'],'owner_card_metrics_id':wrapper['data-card-metrics-id'],
        'carousel_options_raw':node['data-a-carousel-options'],'referer':url,
        'ranking_context_binding':{'ranking_context_url':_scope(wrapper.select_one('.zg-bdg-text'),url)},
        'owner_path':'/acp/protocol-test-only','owner_params':'opaque-protocol-test-only'}
    # Path/params here are protocol mocks. This test validates only the actual
    # DOM lifecycle, never an actual request or saved server-owner approval.
    return wrapper,owner

@pytest.mark.parametrize('phase',['before','after'])
def test_actual_initialized_owner_is_still_same_context(phase):
    wrapper,owner=initialized_owner(phase)
    assert wrapper.get('data-acp-path') is None and wrapper.get('data-acp-params') is None
    assert wrapper['data-mix-claimed']=='true'
    assert PlaywrightTransport._live_carousel_owner_verified(str(wrapper),owner) is True

@pytest.mark.parametrize('mutation',['id','metrics','options','mixed_missing','wrong_path','unclaimed','uninitialized','context'])
def test_initialized_state_never_weakens_exact_owner_identity(mutation):
    wrapper,owner=initialized_owner()
    if mutation=='id':wrapper['id']='different'
    elif mutation=='metrics':wrapper['data-card-metrics-id']='different'
    elif mutation=='options':wrapper.select_one('[data-a-carousel-options]')['data-a-carousel-options']='{}'
    elif mutation=='mixed_missing':wrapper['data-acp-path']=owner['owner_path']
    elif mutation=='wrong_path':wrapper['data-acp-path']='/acp/wrong';wrapper['data-acp-params']=owner['owner_params']
    elif mutation=='unclaimed':wrapper['data-mix-claimed']='false'
    elif mutation=='uninitialized':wrapper.select_one('[data-a-carousel-options]')['class']=[]
    elif mutation=='context':owner['ranking_context_binding']['ranking_context_url']='https://www.amazon.es/gp/bestsellers/toys'
    assert PlaywrightTransport._live_carousel_owner_verified(str(wrapper),owner) is False

@pytest.mark.parametrize('initializes',[True,False])
def test_below_fold_owner_scrolls_and_waits_before_normal_native_click(initializes):
    from test_highspeed_v3_native_preparation import saved_descriptor
    descriptor,url=saved_descriptor();owner=PlaywrightTransport._saved_carousel_owner(descriptor,url)
    class Page:
        def __init__(self):self.url=url;self.actions=[];self.waits=0
        def route(self,pattern,callback):self.capture=callback
        def wait_for_timeout(self,ms):self.waits+=1;self.actions.append('wait')
        def evaluate(self,script,arg):
            if 'wrapper.outerHTML' in script:
                assert 'scrollIntoView' in script;self.actions.append('scroll')
                soup=BeautifulSoup(Path(descriptor['evidence_path']).read_bytes(),'lxml');wrapper=soup.find(id=owner['owner_id'])
                if initializes and self.waits:
                    node=wrapper.select_one('[data-a-carousel-options]')
                    node['class']=list(node.get('class',[]))+['a-carousel-initialized','p13n-carousel-initialized']
                    wrapper.attrs.pop('data-acp-path',None);wrapper.attrs.pop('data-acp-params',None)
                    wrapper['data-mix-claimed']='true'
                return str(wrapper)
            assert 'next.click()' in script;self.actions.append('click')
            # Protocol mock only: no guessed endpoint/body is executed on Amazon.
            request=SimpleNamespace(url='https://www.amazon.es'+owner['owner_path'].rstrip('/')+'/mock-only',method='POST',post_data='opaque-protocol-body',resource_type='fetch',headers={'x-amz-acp-params':owner['owner_params']})
            self.capture(SimpleNamespace(request=request,abort=lambda:None,continue_=lambda **kw:pytest.fail('native must abort')))
            return True
    page=Page();transport=PlaywrightTransport(SimpleNamespace(page=page));transport.install_bounded_acp_capture()
    if initializes:
        result=transport.prepare_ajax_descriptor(descriptor,referer=url)
        assert result['payload']=='opaque-protocol-body'
        assert page.actions[:4]==['scroll','wait','scroll','click']
    else:
        with pytest.raises(ValueError,match='CONTRACT_NOT_OBSERVED'):transport.prepare_ajax_descriptor(descriptor,referer=url)
        assert page.waits==6 and 'click' not in page.actions and not transport._native_acp_requests
