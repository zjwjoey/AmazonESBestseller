"""Offline route-contract tests: these do not assert Amazon endpoint success."""
from types import SimpleNamespace
from pathlib import Path
import json
import html
import pytest
from amazon_es_bestseller.transport.playwright import PlaywrightTransport

PAGE = "https://www.amazon.es/gp/bestsellers/kitchen/2822691031"
ENDPOINT = "https://www.amazon.es/acp/eu-south-2/widget/nextPage"

def test_actual_p0_grid_preserves_distinct_offsets_and_raw_rec_metadata():
    from test_multisource_sources import parse
    fixture=json.loads((Path(__file__).parent/"fixtures/multisource/p0_furniture_acp_metadata.json").read_text(encoding="utf-8"))
    attrs=" ".join(f'{k}="{html.escape(v,quote=True)}"' for k,v in fixture["grid_attributes"].items())
    body=("<h1>Los más vendidos</h1><div data-acp-path='/acp/widget/' data-acp-params='fixture-only'>"
        f"<div {attrs}></div></div><div id='gridItemRoot' data-asin='B078C6QR1C'>"
        "<span class='zg-bdg-text'>#1</span><a href='/dp/B078C6QR1C'>Saved card excerpt</a></div>")
    d=parse(html=body)["page"]["acp_pending"][0]
    assert d["metadata"]["index_offset_raw"] == "30"
    assert d["metadata"]["offset_raw"] == "50"
    assert json.loads(d["metadata"]["client_recs_raw"])[0]["metadataMap"]["render.zg.rank"] == "1"
    assert len(d["metadata"]["entries"]) == 50

class Route:
    def __init__(self, url=ENDPOINT, body='{"native":"contract"}', kind="fetch"):
        self.request = SimpleNamespace(url=url, method="POST", post_data=body,
            resource_type=kind, headers={"content-type":"application/json",
                "x-amz-acp-params":"fixture-token", "cookie":"private", "user-agent":"private"})
        self.aborted = self.continued = False
    def abort(self): self.aborted = True
    def continue_(self): self.continued = True

class Page:
    url = PAGE
    def __init__(self): self.callback=None; self.routes=[]; self.scrolls=0; self.inject=True; self.fail=False
    def route(self, pattern, callback): self.callback=callback
    def wait_for_timeout(self, value): assert value == 500
    def evaluate(self, code, data=None):
        if data is None:
            self.scrolls += 1
            if self.inject:
                route=Route(); self.callback(route); self.routes.append(route)
            return
        # An unrelated native request may never consume the manual allowance.
        wrong=Route(body='wrong'); self.callback(wrong); self.routes.append(wrong)
        exact=Route(data["url"], data["body"]); self.callback(exact); self.routes.append(exact)
        if self.fail: raise RuntimeError("offline failure")
        return {"status":200,"url":data["url"],"text":"<div>Saved cards</div>"}

def transport():
    page=Page(); t=PlaywrightTransport(SimpleNamespace(page=page))
    t.install_bounded_acp_capture()
    return t,page

def test_native_capture_aborts_unmetered_request_and_uses_exact_body_without_cookie():
    t,p=transport()
    d=t.prepare_ajax_descriptor({"url":ENDPOINT,"payload":"guessed"},referer=PAGE)
    assert d["payload"] == '{"native":"contract"}'
    assert d["request_contract_source"] == "NATIVE_BROWSER_REQUEST_ABORTED"
    assert "cookie" not in d["headers"] and "user-agent" not in d["headers"]
    assert p.routes[0].aborted and not p.routes[0].continued
    t.fetch_ajax(d["url"],method=d["method"],payload=d["payload"],headers=d["headers"],referer=PAGE)
    assert p.routes[-2].aborted and p.routes[-1].continued
    after=Route(); p.callback(after); assert after.aborted
    assert t._manual_acp_allowance is None


def test_aborted_native_contract_is_saved_even_without_matching_endpoint():
    page = Page()
    saved = []
    t = PlaywrightTransport(SimpleNamespace(page=page))
    t.install_bounded_acp_capture(on_capture=saved.append)
    different = Route(url="https://www.amazon.es/acp/another-widget/nextPage")
    page.callback(different)
    assert different.aborted and not different.continued
    assert saved[0]["url"] == different.request.url
    assert saved[0]["parent_page_url"] == PAGE
    assert "cookie" not in saved[0]["headers"]


def test_saved_native_request_preserves_dynamic_query_json_and_rank_metadata():
    from urllib.parse import urlsplit, urlunsplit
    fixture = json.loads((Path(__file__).parent / "fixtures/multisource/p0_furniture_acp_native_request.json").read_text(encoding="utf-8"))
    page = Page()
    page.url = fixture["source_page_url"]
    t = PlaywrightTransport(SimpleNamespace(page=page))
    t.install_bounded_acp_capture()
    request = Route(fixture["url"], fixture["payload"])
    request.request.headers = fixture["headers"]
    page.callback(request)
    parts = urlsplit(fixture["url"])
    endpoint = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    descriptor = t.prepare_ajax_descriptor({"url": endpoint,
        "headers": {"x-amz-acp-params": "fixture-only-token"}}, referer=page.url)
    assert request.aborted
    assert descriptor["url"] == fixture["url"]
    assert descriptor["payload"] == fixture["payload"]
    assert descriptor["headers"]["content-type"] == fixture["headers"]["content-type"]
    body = json.loads(descriptor["payload"])
    first = json.loads(body["ids"][0])
    assert first["metadataMap"]["render.zg.rank"] == "31"
    assert body["indexes"][0] == 30


def test_native_query_cannot_match_another_widget_path():
    t, page = transport()
    page.inject = False
    route = Route(url="https://www.amazon.es/acp/different-widget/nextPage?page-type=zeitgeist&stamp=1")
    page.callback(route)
    with pytest.raises(ValueError, match="CONTRACT_NOT_OBSERVED"):
        t.prepare_ajax_descriptor({"url": ENDPOINT}, referer=PAGE)

def test_capture_unavailable_never_falls_back_to_guessed_descriptor():
    t,p=transport(); p.inject=False
    with pytest.raises(ValueError,match="CONTRACT_NOT_OBSERVED"):
        t.prepare_ajax_descriptor({"url":ENDPOINT,"payload":"guess"},referer=PAGE)
    assert p.scrolls==6

@pytest.mark.parametrize("url",["https://evil.example/acp/widget/nextPage", "https://www.amazon.es/dp/B000000001"])
def test_capture_rejects_non_source_endpoint_without_scroll(url):
    t,p=transport()
    with pytest.raises(ValueError,match="ENDPOINT_INVALID"):
        t.prepare_ajax_descriptor({"url":url},referer=PAGE)
    assert p.scrolls==0

def test_capture_requires_fresh_source_page():
    t,p=transport(); p.url="about:blank"
    with pytest.raises(ValueError,match="CURRENT_SOURCE_PAGE"):
        t.prepare_ajax_descriptor({"url":ENDPOINT},referer=PAGE)
    assert p.scrolls==0

def test_stale_widget_params_cannot_supply_contract_for_new_page():
    t,p=transport()
    t.prepare_ajax_descriptor({"url":ENDPOINT},referer=PAGE)
    p.inject=False
    with pytest.raises(ValueError,match="CONTRACT_NOT_OBSERVED"):
        t.prepare_ajax_descriptor({"url":ENDPOINT,
            "headers":{"x-amz-acp-params":"new-widget-token"}},referer=PAGE)

def test_manual_allowance_revoked_on_evaluate_failure_and_document_cannot_pass():
    t,p=transport(); p.fail=True
    with pytest.raises(RuntimeError,match="offline failure"):
        t.fetch_ajax(ENDPOINT,method="POST",payload="native")
    assert t._manual_acp_allowance is None
    t._manual_acp_allowance=(ENDPOINT,"POST","native")
    doc=Route(body="native",kind="document"); p.callback(doc)
    assert doc.aborted and not doc.continued

def native_furniture_setup():
    fixture=json.loads((Path(__file__).parent/"fixtures/multisource/p0_furniture_acp_native_request.json").read_text(encoding="utf-8"))
    raw=json.loads((Path(__file__).parent/"fixtures/multisource/p0_furniture_acp_metadata.json").read_text(encoding="utf-8"))
    t,p=transport(); p.url=fixture["source_page_url"]; p.inject=False
    route=Route(fixture["url"],fixture["payload"]); route.request.headers=fixture["headers"]; p.callback(route)
    metadata={"entries":json.loads(raw["grid_attributes"]["data-client-recs-list"]),
        "index_offset_raw":"30","offset_raw":"50","faceout":"GeneralFaceout","reftag":"zg_bs_g_2822691031"}
    return t,p,fixture,metadata

def test_native_proven_batches_advance_without_replaying_initial_eight():
    t,p,f,m=native_furniture_setup()
    first=t.prepare_ajax_descriptor({"url":f["url"],"metadata":m,"offset":30,"count":20},referer=p.url)
    assert first["payload"]==f["payload"]
    for offset,count,expected in [(38,12,list(range(38,46))),(46,4,list(range(46,50)))]:
        d=t.prepare_ajax_descriptor({"url":f["url"],"metadata":m,"offset":offset,"count":count},referer=p.url)
        body=json.loads(d["payload"])
        assert body["indexes"]==expected and body["offset"]==str(offset)
        assert [json.loads(value) for value in body["ids"]]==m["entries"][offset:offset+len(expected)]
        assert d["url"]==f["url"] and d["headers"]==f["headers"]
        assert d["request_contract_source"]=="NATIVE_BROWSER_CONTRACT_DERIVED_BATCH"
        assert len(d["native_payload_sha256"])==64

@pytest.mark.parametrize("invalid",["missing", "recs", "offset", "count", "unaligned"])
def test_native_batch_derivation_requires_exact_saved_metadata_proof(invalid):
    t,p,f,m=native_furniture_setup()
    d={"url":f["url"],"metadata":m,"offset":38,"count":12}
    if invalid=="missing": d.pop("metadata")
    if invalid=="recs": m["entries"][30]["id"]="B000000000"
    if invalid=="offset": m["offset_raw"]="51"
    if invalid=="count": d["count"]=13
    if invalid=="unaligned": d["offset"]=39; d["count"]=11
    with pytest.raises(ValueError,match="ACP_NATIVE_BATCH_PROOF_INVALID"):
        t.prepare_ajax_descriptor(d,referer=p.url)
