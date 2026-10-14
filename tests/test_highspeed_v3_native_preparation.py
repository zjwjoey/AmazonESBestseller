import pytest
from pathlib import Path
from amazon_es_bestseller.highspeed.runner import _next_acp_pending

def test_v3_next_pending_visits_all_independent_carousels_after_one_miss():
    groups=[{"metadata":{"carousel":True,"ranking_context_url":"one"}}, {"metadata":{"carousel":True,"ranking_context_url":"two"}}]
    assert _next_acp_pending(groups,{"one"},v3=True)==groups[1]
    assert _next_acp_pending(groups,{"one","two"},v3=True) is None
    assert _next_acp_pending(groups,{"one"},v3=False)==groups[0]

from types import SimpleNamespace
from amazon_es_bestseller.transport.playwright import PlaywrightTransport
from amazon_es_bestseller.highspeed.adapters import parse_page

def saved_descriptor():
    path=Path(__file__).parent/"fixtures"/"highspeed"/"real_v3_new_releases_root.html"
    url="https://www.amazon.es/gp/new-releases"
    parsed=parse_page(path.read_text(encoding="utf-8"),source={"source_id":"new","source_type":"NEW_RELEASES","actual_source_url":url,"parser_profile":"V3"},run_id="r",page_url=url,page_number=1,observed_at="2026-10-14T00:00:00Z",evidence_path=str(path))
    return dict(parsed["acp_pending"][0],source_id="new"),url

def test_real_saved_carousel_owner_is_exact_parent_evidence_not_live_ready():
    descriptor,url=saved_descriptor()
    owner=PlaywrightTransport._saved_carousel_owner(descriptor,url)
    assert owner["owner_id"]
    assert owner["owner_path"].startswith("/acp/")
    assert owner["owner_params"]
    assert owner["ranking_context_binding"]["page_evidence_sha256"]==descriptor["metadata"]["evidence_sha256"]
    assert owner["ranking_context_binding"]["ranking_context_url"]==descriptor["metadata"]["ranking_context_url"]

@pytest.mark.parametrize("field,value",[("evidence_sha256","0"*64),("ranking_context_url","https://www.amazon.es/gp/new-releases/other"),("carousel_options_raw","[]")])
def test_saved_carousel_wrong_parent_or_group_never_prepares(field,value):
    descriptor,url=saved_descriptor();descriptor["metadata"]=dict(descriptor["metadata"],**{field:value})
    with pytest.raises(ValueError,match="OWNER_EVIDENCE_UNVERIFIED"):
        PlaywrightTransport._saved_carousel_owner(descriptor,url)

def test_real_saved_owner_without_native_post_is_not_contract():
    descriptor,url=saved_descriptor()
    page=SimpleNamespace(url=url,evaluate=lambda *args:False,route=lambda *args:None)
    transport=PlaywrightTransport(SimpleNamespace(page=page));transport.install_bounded_acp_capture()
    with pytest.raises(ValueError,match="CONTRACT_NOT_OBSERVED"):
        transport.prepare_ajax_descriptor(descriptor,referer=url)
    assert not transport._native_acp_requests
    assert transport._active_carousel_owner is None

def test_native_carousel_actor_only_uses_aborted_actual_request_byte_exact(tmp_path):
    # Mock route protocol test; this is not an observed LIVE endpoint/response.
    from amazon_es_bestseller.highspeed.cli import save_native_contract
    descriptor,url=saved_descriptor();owner=PlaywrightTransport._saved_carousel_owner(descriptor,url)
    class Page:
        def __init__(self):self.url=url;self.aborted=0;self.continued=0
        def route(self,pattern,callback):self.capture=callback
        def evaluate(self,script,argument):
            assert argument["owner_id"]==owner["owner_id"]
            request=SimpleNamespace(url="https://www.amazon.es"+owner["owner_path"].rstrip("/")+"/mock-observed-only",method="POST",post_data='{"opaque":"site-byte-exact"}',resource_type="fetch",headers={"content-type":"application/json","x-amz-acp-params":owner["owner_params"]})
            self.capture(SimpleNamespace(request=request,abort=lambda:setattr(self,"aborted",self.aborted+1),continue_=lambda **kw:setattr(self,"continued",self.continued+1)))
            return True
        def wait_for_timeout(self,ms):pass
    page=Page();transport=PlaywrightTransport(SimpleNamespace(page=page))
    transport.install_bounded_acp_capture(on_capture=lambda value:save_native_contract(SimpleNamespace(root=tmp_path),{"run_id":"r"},value))
    prepared=transport.prepare_ajax_descriptor(descriptor,referer=url)
    assert page.aborted==1 and page.continued==0
    assert prepared["payload"]=='{"opaque":"site-byte-exact"}'
    assert prepared["request_contract_source"]=="NATIVE_BROWSER_REQUEST_ABORTED"
    assert prepared["ranking_context_binding"]==owner["ranking_context_binding"]
    assert prepared["native_contract_evidence_path"]
    assert transport._active_carousel_owner is None

def test_runner_v3_scheduler_is_opt_in_and_does_not_change_frozen_source_records(tmp_path):
    from test_highspeed_runner import plan,Store,make_runner,NOW,parser
    p=plan(tmp_path);p["source_order_policy"]="FIVE_TYPE_ROUND_ROBIN_V3"
    first=p["sources"][0]
    fixture=next(iter(first["offline_pages"].values()))
    best_url="https://www.amazon.es/gp/bestsellers/books"
    new_url="https://www.amazon.es/gp/new-releases/books"
    p["sources"]=[first,dict(first,source_id="best2",page_urls=[best_url],offline_pages={best_url:fixture}),
        dict(first,source_id="new",source_type="NEW_RELEASES",page_urls=[new_url],offline_pages={new_url:fixture})]
    original=[s["source_id"] for s in p["sources"]];seen=[]
    def parse_observed(html,**kwargs):seen.append(kwargs["source"]["source_id"]);return parser(html,**kwargs)
    make_runner(Store(tmp_path/"store"),clock=lambda:NOW,parser=parse_observed).run(p)
    assert seen==["home","new","best2"]
    assert [s["source_id"] for s in p["sources"]]==original

@pytest.mark.parametrize("failure",["ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED","ACP_CAROUSEL_OWNER_EVIDENCE_UNVERIFIED","ACP_CAROUSEL_NATIVE_CONTRACT_AMBIGUOUS"])
def test_runner_v3_attempts_each_independent_native_group_without_network_probes(tmp_path,failure):
    # Scheduler protocol mock, not LIVE acceptance or source completeness proof.
    from test_highspeed_runner import plan,approval,Store,make_runner,NOW,URL
    p=plan(tmp_path,"T1");p["snapshot_contract_version"]=3
    from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION
    p["normalizer_version"]=NORMALIZER_VERSION
    p["sources"][0]["parser_profile"]="V3"
    a=approval(p,tmp_path);a["sources"]["home"]["allow_native_acp"]=True
    a["sources"]["home"]["parser_profile"]="V3"
    store=Store(tmp_path/"store");seen=[]
    class Transport:
        def fetch_page(self,url):return {"status_code":200,"url":url,"text":"normal saved mock representation"}
        def prepare_ajax_descriptor(self,descriptor,**kwargs):
            seen.append(descriptor["metadata"]["ranking_context_url"])
            assert descriptor["source_id"]=="home"
            raise ValueError(failure)
        def fetch_ajax(self,*args,**kwargs):raise AssertionError("unknown contract must never send request")
    def parse_pending(html,**kwargs):
        pending=[{"url":None,"method":"POST","metadata":{"carousel":True,"ranking_context_url":c},"offset":3,"count":47} for c in ["one","two"]]
        return {"page":{"actual_count":2,"raw_card_count":2,"status":"PARTIAL","access_status":"NORMAL","source_type_verified":True,"source_context_valid":True,"flags":["ACP_INCOMPLETE"],"acp_pending":pending,"page_complete":False},"observations":[{"raw_product_url":"/product/one"},{"raw_product_url":"/product/two"}],"acp_pending":pending}
    make_runner(store,clock=lambda:NOW,parser=parse_pending,gate_reader=lambda:{"state":"NORMAL"}).run(p,authorization=a,transport=Transport())
    assert seen==["one","two"]
    assert len(store.requests)==1
    assert store.saved_pages[-1]["carousel_contract_failures"]=={"one":failure,"two":failure}
    assert not store.saved_pages[-1]["page_complete"]
    assert store.saved_pages[-1]["terminal_for_snapshot"] is True

@pytest.mark.parametrize("change",[{"parent_page_url":"https://www.amazon.es/gp/bestsellers"},{"url":"https://example.com/acp/other/load"},{"method":"GET"},{"headers":{"x-amz-acp-params":"wrong-component"}}])
def test_other_widget_or_source_request_never_gets_carousel_binding(change):
    descriptor,url=saved_descriptor();owner=PlaywrightTransport._saved_carousel_owner(descriptor,url)
    captured={"url":"https://www.amazon.es"+owner["owner_path"].rstrip("/")+"/mock-only","method":"POST","payload":"opaque","parent_page_url":url,"headers":{"x-amz-acp-params":owner["owner_params"]}}
    captured.update(change)
    assert not PlaywrightTransport._carousel_request_matches(captured,owner)

def test_actual_v3_movers_empty_unverified_root_is_not_navigation_shortcut():
    from amazon_es_bestseller.highspeed.runner import source_navigation_only
    path=Path(__file__).parent/"fixtures"/"highspeed"/"real_v3_movers_shakers_root.html"
    url="https://www.amazon.es/gp/movers-and-shakers"
    source={"source_id":"m","source_type":"MOVERS_SHAKERS","actual_source_url":url,"parser_profile":"V3"}
    p=parse_page(path.read_text(encoding="utf-8"),source=source,run_id="r",page_url=url,page_number=1,observed_at="2026-10-14T00:00:00Z",evidence_path=str(path))
    assert p["page"]["page_content_state"]=="PARSER_EMPTY_UNVERIFIED"
    assert not source_navigation_only(source,url,p)
