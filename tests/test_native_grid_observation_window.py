"""Late local native events use literal saved request bytes, never a fetch."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_highspeed_v4_native_grid import grid_request
from amazon_es_bestseller.transport.playwright import PlaywrightTransport
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page


def local_transport(after_ms,*,invalid=False,redirect=False):
    descriptor,source,auth=grid_request()
    native=json.loads(Path(descriptor['native_contract_evidence_path']).read_text('utf8'))
    class Page:
        url=source['actual_source_url']
        elapsed=0
        def evaluate(self,*args):pass
        def wait_for_timeout(self,ms):
            assert ms==500
            self.elapsed+=ms
            if redirect:self.url='https://www.amazon.es/gp/bestsellers/books'
            if after_ms is not None and self.elapsed>=after_ms and not transport._native_acp_requests:
                transport._native_acp_requests.append({**deepcopy(native),'payload':'{}' if invalid else native['payload']})
    page=Page();transport=PlaywrightTransport(SimpleNamespace(page=page));transport._acp_capture_installed=True
    if after_ms==0:transport._native_acp_requests.append(deepcopy(native))
    return transport,page,descriptor,source,native


@pytest.mark.parametrize('after',[4000,11500])
def test_late_real_native_contract_is_observed_without_new_network(after):
    t,p,d,s,n=local_transport(after)
    value=t.prepare_ajax_descriptor(d,referer=s['actual_source_url'])
    assert value['payload']==n['payload'] and value['url']==n['url']
    assert p.elapsed==after and t._manual_acp_allowance is None


def test_immediate_contract_does_not_add_latency():
    t,p,d,s,n=local_transport(0)
    assert t.prepare_ajax_descriptor(d,referer=s['actual_source_url'])['payload']==n['payload']
    assert p.elapsed==0


def test_absent_native_contract_remains_bounded_and_unverified():
    t,p,d,s,n=local_transport(None)
    with pytest.raises(ValueError,match='ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED'):
        t.prepare_ajax_descriptor(d,referer=s['actual_source_url'])
    assert p.elapsed==12000 and not t._native_acp_requests and t._manual_acp_allowance is None


def test_delayed_wrong_literal_batch_is_still_rejected():
    t,p,d,s,n=local_transport(4000,invalid=True)
    with pytest.raises(ValueError,match='ACP_NATIVE_BATCH_PROOF_INVALID'):
        t.prepare_ajax_descriptor(d,referer=s['actual_source_url'])


def test_source_navigation_during_wait_does_not_reuse_old_contract():
    t,p,d,s,n=local_transport(4000,redirect=True)
    with pytest.raises(ValueError,match='ACP_NATIVE_CAPTURE_REQUIRES_CURRENT_SOURCE_PAGE'):
        t.prepare_ajax_descriptor(d,referer=s['actual_source_url'])


def test_actual_electronics_fixture_keeps_twenty_unobserved_slots():
    path=Path(__file__).parent/'fixtures/highspeed_v4/loading/real_electronics_grid_20261015.html'
    url='https://www.amazon.es/gp/bestsellers/electronics/16063350031'
    source={'source_id':'electronics','source_type':'BEST_SELLERS',
        'source_subtype':'BESTSELLER_SALES_RANKING','actual_source_url':url,
        'page_urls':[url],'parser_profile':'V4'}
    value=parse_v4_page(path.read_text('utf8'),source=source,page_url=url,run_id='TEST_ONLY',
        observed_at='2026-10-15T13:00:48.469643Z',evidence_path=str(path),http_status=200,
        origin='OFFLINE_FIXTURE')
    assert len(value['observations'])==30
    assert value['page']['completeness_status']=='UNKNOWN'
    assert len(value['acp_pending'])==1 and value['acp_pending'][0]['count']==20
    assert len(value['acp_pending'][0]['metadata']['entries'])==50
