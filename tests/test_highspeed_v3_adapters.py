from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed.adapters import parse_page

def parse(html,kind="BEST_SELLERS",v3=True,**kw):
    paths={"BEST_SELLERS":"bestsellers","NEW_RELEASES":"new-releases","MOVERS_SHAKERS":"movers-and-shakers","MOST_WISHED_FOR":"most-wished-for","MOST_GIFTED":"most-gifted"}
    url="https://www.amazon.es/gp/"+paths[kind]
    source={"source_id":"s","source_type":kind,"actual_source_url":url}
    if v3:source["parser_profile"]="V3"
    return parse_page(html,source=source,run_id="r",page_url=url,page_number=1,observed_at="2026-10-14T00:00:00Z",evidence_path="saved.html",**kw)

def card(title,alt="",other=""):
    return '<h1>Los más vendidos</h1><div id="zg"><div id="gridItemRoot"><span class="zg-bdg-text">#1</span><a href="/product/one"><div data-product-title>'+title+'</div><img alt="'+alt+'"></a>'+other+'</div></div>'

def test_v3_marks_truncated_title_without_inventing_completion():
    r=parse(card("Producto..."))["observations"][0]
    assert r["product_title_status"]=="TRUNCATED"
    assert r["product_title_raw"]=="Producto..."

def test_v3_does_not_take_title_from_another_product_in_same_container():
    html=card("",other='<a href="/product/two"><div data-product-title>Wrong product</div></a>')
    r=parse(html)["observations"][0]
    assert r["raw_product_url"]=="/product/one"
    assert r["product_title_raw"] is None
    assert r["product_title_status"]=="MISSING"

def test_v3_numeric_semantic_title_is_valid():
    r=parse(card("1984"))["observations"][0]
    assert r["product_title_status"]=="SOURCE_VISIBLE"

def test_v3_advertisement_is_not_ranked_card():
    h=card("one").replace('<div id="gridItemRoot">','<div id="gridItemRoot" data-ad-marker="sponsored">')
    p=parse(h)
    assert p["observations"]==[]
    assert p["page"]["page_complete"] is False

def test_v2_shape_and_titles_unchanged():
    r=parse(card("Producto..."),v3=False)["observations"][0]
    assert "product_title_status" not in r
    assert r["parser_version"]=="link_first_adapters_v2.2.title_priority"

@pytest.mark.parametrize("fixture,kind",[("continue30000_wished_dvd_explicit_empty.html","MOST_WISHED_FOR"),("continue30000_gifted_devices_empty.html","MOST_GIFTED"),("continue30000_best_luggage_empty.html","BEST_SELLERS")])
def test_actual_official_empty_evidence_is_not_success(fixture,kind):
    html=(Path(__file__).parent/"fixtures"/"highspeed"/fixture).read_text(encoding="utf-8")
    p=parse(html,kind)
    assert p["page"]["page_content_state"]=="SOURCE_EMPTY"
    assert not p["page"]["page_complete"]
    assert p["observations"]==[]

def test_zero_card_parser_failure_is_not_official_empty():
    p=parse('<h1>Los más vendidos</h1><div id="zg-right-col"><div class="unknown-product">A product</div></div>')
    assert p["page"]["page_content_state"]=="PARSER_EMPTY_UNVERIFIED"
    assert p["page"]["status"]=="FAILED"

@pytest.mark.parametrize("kind,count",[("NEW_RELEASES",36),("MOVERS_SHAKERS",0),("MOST_WISHED_FOR",0),("MOST_GIFTED",0)])
def test_five_list_historical_real_roots_do_not_fabricate_cards(kind,count):
    fixture=Path(__file__).parent/"fixtures"/"highspeed"/("real_v3_"+kind.lower()+"_root.html")
    p=parse(fixture.read_text(encoding="utf-8"),kind)
    assert p["page"]["source_type_verified"]
    assert len(p["observations"])==count
    assert not p["page"]["page_complete"]
    if not count:assert p["page"]["page_content_state"]==("PARSER_EMPTY_UNVERIFIED" if kind=="MOVERS_SHAKERS" else "NAVIGATION_ONLY")

def test_actual_best_carousel_metadata_keeps_contexts_separate():
    fixture=Path(__file__).parent/"fixtures"/"highspeed"/"p0_native_carousels.html"
    p=parse(fixture.read_text(encoding="utf-8"))
    groups=p["page"]["carousel_groups"]
    assert len(groups)==2
    assert len({g["ranking_context_url"] for g in groups})==2
    assert all(g["expected_count"]>g["actual_count"] for g in groups)
    assert p["acp_pending"]
    assert not p["page"]["page_complete"]
    assert all(q["url"] is None for q in p["acp_pending"])

def test_nav_tile_is_not_product_card():
    h='<h1>Los más vendidos</h1><div id="zg"><div data-card-metrics-id="p13n-zg-list-carousel-nav"><a href="/gp/bestsellers/books">Ver más</a></div></div>'
    assert not parse(h)["observations"]

def test_unrelated_metadata_does_not_override_main_grid():
    h=card("one").replace('<div id="zg">','<div data-client-recs-list="[1,2,3,4,5]"></div><div id="zg-right-col"><div class="p13n-desktop-grid" data-client-recs-list="[1]">')+'</div>'
    p=parse(h)
    assert p["page"]["expected_count"]==1

def test_carousel_fragment_without_original_context_is_not_bound_to_root():
    fixture=Path(__file__).parent/"fixtures"/"highspeed"/"p0_native_carousels.html"
    html=fixture.read_text(encoding="utf-8")
    base=parse(html)
    fragment='<div class="ranking-card"><span class="zg-bdg-text">#4</span><a href="/product/four"><span data-product-title>four</span></a></div>'
    p=parse(html,acp_responses=[{"body":fragment,"evidence_path":"acp.html"}])
    assert len(p["observations"])==len(base["observations"])
    assert "ACP_CAROUSEL_CONTEXT_UNVERIFIED" in p["page"]["flags"]

def test_carousel_fragment_exact_original_context_binding_is_required():
    fixture=Path(__file__).parent/"fixtures"/"highspeed"/"p0_native_carousels.html"
    html=fixture.read_text(encoding="utf-8");base=parse(html)
    context=base["observations"][0]["rank_context"]
    fragment='<div class="ranking-card"><span class="zg-bdg-text">#4</span><a href="/product/four"><span data-product-title>four</span></a></div>'
    response={"body":fragment,"evidence_path":"acp.html","ranking_context_binding":{"source_id":"s","page_evidence_sha256":base["page"]["evidence_sha256"],"ranking_context_url":context}}
    p=parse(html,acp_responses=[response])
    assert p["observations"][-1]["rank_context"]==context
    assert p["observations"][-1]["raw_product_url"]=="/product/four"
    assert not p["page"]["page_complete"]

def test_v3_numeric_title_projection_keeps_source_numeric_book_name(tmp_path):
    from amazon_es_bestseller.highspeed.title_review import project_title
    html=card("1984");path=tmp_path/"saved.html";path.write_text(html,encoding="utf-8")
    row=parse(html)["observations"][0];row["evidence_path"]=str(path);row["product_title_evidence"]["evidence_path"]=str(path)
    source={"source_id":"s","source_type":"BEST_SELLERS","actual_source_url":row["page_url"],"parser_profile":"V3"}
    reviewed=project_title(row,source)
    assert reviewed["review_status"]=="UNCHANGED"
    assert reviewed["corrected_source_title"]=="1984"
    assert reviewed["title_parser_version"]=="link_first_adapters_v3.card_bound"

def test_v3_carousel_projection_validates_both_parent_page_and_acp(tmp_path):
    from amazon_es_bestseller.highspeed.title_review import project_title
    fixture=Path(__file__).parent/"fixtures"/"highspeed"/"p0_native_carousels.html"
    html=fixture.read_text(encoding="utf-8");base_path=tmp_path/"base.html";base_path.write_text(html,encoding="utf-8")
    base=parse(html);context=base["observations"][0]["rank_context"]
    body='<div class="ranking-card"><span class="zg-bdg-text">#4</span><a href="/product/four"><span data-product-title>four</span></a></div>'
    acp_path=tmp_path/"acp.html";acp_path.write_text(body,encoding="utf-8")
    response={"body":body,"evidence_path":str(acp_path),"ranking_context_binding":{"source_id":"s","page_evidence_sha256":base["page"]["evidence_sha256"],"ranking_context_url":context}}
    row=parse(html,acp_responses=[response])["observations"][-1]
    row["category_scope_evidence"]["page_evidence_path"]=str(base_path)
    source={"source_id":"s","source_type":"BEST_SELLERS","actual_source_url":row["page_url"],"parser_profile":"V3"}
    assert project_title(row,source)["review_status"]=="UNCHANGED"
    base_path.write_text("changed original page",encoding="utf-8")
    assert project_title(row,source)["review_status"]=="UNVERIFIED"

def test_v3_acp_without_saved_body_evidence_is_not_candidate():
    fragment='<div class="ranking-card"><span class="zg-bdg-text">#2</span><a href="/product/two"><span data-product-title>two</span></a></div>'
    p=parse(card("one"),acp_responses=[{"body":fragment}])
    assert len(p["observations"])==1
    assert "ACP_EVIDENCE_PATH_MISSING" in p["page"]["flags"]
    assert not p["page"]["page_complete"]

def test_v3_acp_uses_actual_capture_time_v2_keeps_legacy_page_time():
    fragment='<div class="ranking-card"><span class="zg-bdg-text">#2</span><a href="/product/two"><span data-product-title>two</span></a></div>'
    response={"body":fragment,"evidence_path":"acp.html","observed_at":"2026-10-14T00:10:00Z"}
    v3=parse(card("one"),acp_responses=[response])["observations"][-1]
    legacy=parse(card("one"),v3=False,acp_responses=[response])["observations"][-1]
    assert v3["observed_at"]==v3["rating_observed_at"]=="2026-10-14T00:10:00Z"
    assert legacy["observed_at"]=="2026-10-14T00:00:00Z"
