"""Saved empty render + historical settled DOM; readiness only, no LIVE proof."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
from amazon_es_bestseller.highspeed.v4_loading import V4LoadingTransport
from amazon_es_bestseller.transport.playwright import PlaywrightTransport
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.v4_sources import source_for_url

ROOT=Path(__file__).parent/'fixtures/highspeed_v4'
URL='https://www.amazon.es/deals'
INITIAL=(ROOT/'loading/deals_t2_unsettled.html').read_text(encoding='utf8')
SETTLED=(ROOT/'F06b_deals_settled_complete.html').read_text(encoding='utf8')

def transport(tmp_path,monkeypatch,states,code=200):
    class Page:
        url=URL
        def __init__(self):self.waits=0
        def wait_for_timeout(self,n):self.waits+=1
        def content(self):return states[min(self.waits-1,len(states)-1)]
    page=Page();store=RunStore(tmp_path/'store')
    first={'status_code':code,'url':URL,'text':INITIAL,'rendered_dom_html':INITIAL,
        'http_document_html':INITIAL,'access_state':'NORMAL' if code==200 else 'BLOCKED'}
    monkeypatch.setattr(PlaywrightTransport,'fetch_page',lambda *a,**kw:deepcopy(first))
    source=source_for_url(URL);source['page_urls']=[URL]
    t=V4LoadingTransport(SimpleNamespace(page=page),store=store,plan={'run_id':'offline-render-only','sources':[source]})
    return t,page,store

def test_actual_unsettled_deals_waits_locally_for_real_product_cards(tmp_path,monkeypatch):
    t,p,s=transport(tmp_path,monkeypatch,[SETTLED])
    try:
        r=t.fetch_page(URL)
        assert 'data-deal-id=' in r['text'] and p.waits==1
        assert r['render_settle']['new_network_requests']==0
        assert Path(r['render_settle']['initial_dom_reference']['path']).read_text(encoding='utf8')==INITIAL
        assert r['http_document_html']==INITIAL and r['rendered_dom_html']==r['text']
    finally:s.close()

@pytest.mark.parametrize('code',[403,429])
def test_restricted_initial_response_never_waits_or_interacts(tmp_path,monkeypatch,code):
    t,p,s=transport(tmp_path,monkeypatch,[SETTLED],code)
    try:assert t.fetch_page(URL)['status_code']==code and p.waits==0
    finally:s.close()

def test_render_challenge_stops_wait_without_hiding_it(tmp_path,monkeypatch):
    t,p,s=transport(tmp_path,monkeypatch,['<h1>Robot Check</h1>',SETTLED])
    try:
        r=t.fetch_page(URL)
        assert r['access_state']=='CHALLENGE' and p.waits==1
    finally:s.close()

def test_never_ready_render_stays_unverified_and_bounded(tmp_path,monkeypatch):
    t,p,s=transport(tmp_path,monkeypatch,[INITIAL])
    try:
        r=t.fetch_page(URL)
        assert r['render_settle']['outcome']=='UNVERIFIED_TIMEOUT' and p.waits==12
        assert r['render_settle']['new_network_requests']==0
        assert 'data-deal-id=' not in r['text']
    finally:s.close()
