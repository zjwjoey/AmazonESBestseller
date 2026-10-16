"""Actual failed PAGE fixture; no Amazon traffic or invented native payload."""
import gzip,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from amazon_es_bestseller.transport.playwright import PlaywrightTransport

FIX=Path(__file__).parent/'fixtures/highspeed_v4/native_grid_visibility'

def actual():
    proof=json.loads((FIX/'provenance.json').read_text('utf8'))
    html=gzip.decompress((FIX/'actual_new_hpc.html.gz').read_bytes()).decode('utf8')
    source=proof['source'];url=source['actual_source_url']
    result=parse_v4_page(html,source=source,page_url=url,run_id='fixture',
        observed_at=proof['receipt']['observed_at'],evidence_path=str(FIX/'actual_new_hpc.html.gz'),http_status=200)
    return result,url

def test_actual_nonempty_tail_remains_unresolved_without_native_contract():
    result,url=actual()
    assert len(result['observations'])==30
    descriptor=result['acp_pending'][0]
    assert descriptor['offset']==30 and descriptor['count']==4
    assert result['page']['completeness_status']=='UNKNOWN'
    assert descriptor['request_contract']=='NATIVE_REQUEST_REQUIRED'
    assert not descriptor.get('payload')

def test_native_discovery_targets_exact_grid_bottom_without_inventing_request():
    result,url=actual();seen=[]
    class Page:
        def evaluate(self,script,*args):
            seen.append(script)
            return {'matched':True}
        def wait_for_timeout(self,ms):pass
    page=Page();page.url=url
    t=PlaywrightTransport(SimpleNamespace(page=page));t._acp_capture_installed=True
    with pytest.raises(ValueError,match='ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED'):
        t.prepare_ajax_descriptor(result['acp_pending'][0],referer=url)
    # Existing card-only scrolling can leave the actual owner boundary unseen.
    assert any("grid.scrollIntoView" in s for s in seen)
    assert all("fetch(" not in s and '.click(' not in s for s in seen)
    assert not t._native_acp_requests

