from amazon_es_bestseller.categories.provenance import category_evidence_from_detail
from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2
from amazon_es_bestseller.identity import IDENTITY_MATCH, IDENTITY_MISMATCH, resolve_identity
import pytest


ASIN = "B000000001"


def test_desktop_breadcrumb_is_scoped_fallback_and_keeps_full_spanish_path():
    html = f'''<html><body>
      <input id="ASIN" value="{ASIN}">
      <div id="desktop-breadcrumbs_feature_div">
        <a href="/Hogar-y-cocina/b?node=599391031">Hogar y cocina</a>
        <a href="/Costura/b?node=4358016031">Costura y manualidades</a>
        <a href="/Esgrafiado/b?node=4358420031">Esgrafiado</a>
      </div>
      <aside id="recommendations"><a href="/recommendation">Productos similares</a>
        <span data-asin="B000000099">Similar item</span></aside>
      <h1 id="productTitle">Producto</h1>
    </body></html>'''
    result = parse_detail_page(html, ASIN)
    assert result["detail_category_trail"] == [
        "Hogar y cocina", "Costura y manualidades", "Esgrafiado"]
    assert result["detail_category_evidence"][0]["source"] == "desktop-breadcrumbs"
    assert result["detail_category_evidence"][0]["links"][0]["href_raw"].endswith("599391031")
    assert "B000000099" not in result["detail_category_trail"]


def test_bsr_category_is_separate_and_never_fills_missing_product_breadcrumb():
    html = f'''<html><body><input id="ASIN" value="{ASIN}">
      <div id="SalesRank"><span>n.º 123 en Hogar y cocina
        (<a href="/gp/bestsellers/kitchen/599391031">Ver el Top 100 en Hogar y cocina</a>)</span></div>
      <h1 id="productTitle">Producto</h1></body></html>'''
    result = parse_detail_evidence_v2(
        html, ASIN, requested_url=f"https://www.amazon.es/dp/{ASIN}",
        final_url=f"https://www.amazon.es/dp/{ASIN}")
    category = result["category_evidence"]
    assert result["detail_category_trail"] == []
    assert result["bsr_category_path"] == ["Hogar y cocina"]
    assert result["bsr_category_id"] == "599391031"
    assert category["product_breadcrumb_category_path"] == []
    assert category["bsr_category_path"] == ["Hogar y cocina"]
    assert category["bsr_category_evidence"][0]["browse_node_id"] == "599391031"


def test_page_wide_recommendation_asins_are_weak_not_conflicts():
    result = resolve_identity(
        ranking_asin=ASIN, requested_asin=ASIN,
        final_url_asin=f"https://www.amazon.es/dp/{ASIN}",
        canonical_asin=ASIN, parsed_detail_asin=ASIN,
        weak_embedded_asins=["B000000099", "B000000098"])
    assert result["identity_status"] == IDENTITY_MATCH
    assert result["weak_embedded_asins"] == ["B000000098", "B000000099"]
    assert {row["source"] for row in result["identity_evidence"] if row["asin"].startswith("B00000009")} == {
        "weak_embedded_asin"}


def test_v2_saved_page_recommendation_asins_do_not_override_matching_owned_identity():
    html = f'''<html><head><link rel="canonical" href="https://www.amazon.es/dp/{ASIN}"></head>
      <body><input id="ASIN" value="{ASIN}"><div id="dp-container" data-asin="{ASIN}">
      <h1 id="productTitle">Producto</h1><div id="recommendations_feature_div">
      <span data-asin="B000000099">Otro producto</span><a href="/dp/B000000098">Otro enlace</a>
      </div></div></body></html>'''
    result = parse_detail_evidence_v2(
        html, ASIN, requested_url=f"https://www.amazon.es/dp/{ASIN}",
        final_url=f"https://www.amazon.es/dp/{ASIN}")
    assert result["identity_status"] == IDENTITY_MATCH
    weak = {row["asin"] for row in result["identity_evidence"]
            if row["source"] == "weak_embedded_asin"}
    assert {"B000000099", "B000000098"} <= weak


def test_conflicting_owned_and_canonical_asins_remain_blocked():
    result = resolve_identity(
        ranking_asin=ASIN, requested_asin=ASIN,
        canonical_asin="B000000002", parsed_detail_asin="B000000002")
    assert result["identity_status"] == IDENTITY_MISMATCH


def test_v2_reuses_one_dom_and_keeps_v1_base_fields_identical(monkeypatch):
    from amazon_es_bestseller.collection import detail_v2

    html = f'''<html><head><link rel="canonical" href="https://www.amazon.es/dp/{ASIN}"></head>
      <body><input id="ASIN" value="{ASIN}"><h1 id="productTitle">Producto</h1>
      <div id="productOverview_feature_div"><table><tr><td>Material</td><td>Acero</td></tr></table></div>
      <div id="feature-bullets"><ul><li>Bullet</li></ul></div></body></html>'''
    expected_base = parse_detail_page(html, ASIN)
    real_soup = detail_v2.BeautifulSoup
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return real_soup(*args, **kwargs)
    monkeypatch.setattr(detail_v2, "BeautifulSoup", counted)
    result = detail_v2.parse_detail_evidence_v2(
        html, ASIN, requested_url=f"https://www.amazon.es/dp/{ASIN}",
        final_url=f"https://www.amazon.es/dp/{ASIN}")
    assert len(calls) == 1
    version_fields = {"detail_parser_version", "detail_parser_contract_version"}
    assert {key: result[key] for key in expected_base if key not in version_fields} == {
        key: value for key, value in expected_base.items() if key not in version_fields}


def test_category_evidence_keeps_breadcrumb_and_ranking_sources_separate():
    evidence = category_evidence_from_detail(
        {"detail_category_trail": ["Hogar y cocina", "Costura"],
         "detail_category_evidence": [{"source": "desktop-breadcrumbs",
                                        "path": ["Hogar y cocina", "Costura"], "links": []}],
         "bsr_category_path": ["Hogar y cocina"],
         "bsr_category_evidence": [{"label_raw": "Esgrafiado", "browse_node_id": "12345"}]},
        {"ranking_category_path": ["Bricolaje y herramientas", "Organizadores"]})
    output = evidence.to_dict()
    assert output["product_breadcrumb_category_path"] == ["Hogar y cocina", "Costura"]
    assert output["ranking_category_path"] == ["Bricolaje y herramientas", "Organizadores"]
    assert output["bsr_category_path"] == ["Hogar y cocina"]
    assert output["bsr_category_evidence"][0]["browse_node_id"] == "12345"


def test_one_item_parse_identity_and_network_errors_do_not_lose_later_products(tmp_path, monkeypatch):
    import json
    from amazon_es_bestseller.collection import detail as detail_module
    from amazon_es_bestseller.collection.detail import collect_details

    asins = [f"B00000000{i}" for i in range(1, 7)]
    pages = {
        asins[0]: f'<input id="ASIN" value="{asins[0]}"><h1 id="productTitle">OK one</h1>',
        asins[1]: f'<input id="ASIN" value="{asins[1]}"><h1 id="productTitle">Parse error</h1>',
        # The requested page contains a different page-owned ASIN.
        asins[2]: f'<input id="ASIN" value="B000000099"><h1 id="productTitle">Wrong item</h1>',
        asins[3]: f'<input id="ASIN" value="{asins[3]}"><h1 id="productTitle">OK four</h1>',
        asins[5]: f'<input id="ASIN" value="{asins[5]}"><h1 id="productTitle">OK six</h1>',
    }

    class Page:
        url = ""
        def content(self):
            return "<html><body>" + pages[self.url.rsplit("/", 1)[-1]] + "</body></html>"

    class Session:
        def __init__(self):
            self.page = Page()
            self.calls = []
            self.waits = 0
        def goto(self, url):
            asin = url.rsplit("/", 1)[-1]
            self.calls.append(asin)
            self.page.url = url
            if asin == asins[4]:
                raise TimeoutError("navigation timeout")
            return 200
        def wait_for_product_page(self): pass
        def wait_for_price_text(self): pass
        def wait_between_requests(self): self.waits += 1

    original = detail_module._parse_detail_for_version
    def parse(html, asin, **kwargs):
        if asin == asins[1]:
            raise ValueError("fixture parser failure")
        return original(html, asin, **kwargs)
    monkeypatch.setattr(detail_module, "_parse_detail_for_version", parse)
    monkeypatch.setattr(detail_module.time, "sleep", lambda _: None)
    session = Session()
    results = collect_details(asins, session, str(tmp_path))

    assert [row["asin"] for row in results] == [asins[0], asins[3], asins[5]]
    assert session.calls == asins
    checkpoints = {asin: json.loads((tmp_path / "checkpoints" / f"{asin}.json").read_text(encoding="utf-8"))
                   for asin in asins}
    assert checkpoints[asins[1]]["detail_status"] == "PARSER_ERROR"
    assert checkpoints[asins[2]]["status"] == "asin_mismatch"
    assert checkpoints[asins[4]]["detail_status"] == "TIMEOUT"
    assert checkpoints[asins[0]]["status"] == checkpoints[asins[3]]["status"] == checkpoints[asins[5]]["status"] == "success"


def test_access_restriction_and_checkpoint_storage_failure_stop_following_requests(tmp_path, monkeypatch):
    import json
    from amazon_es_bestseller.access.detector import AccessStopError
    from amazon_es_bestseller.collection import detail as detail_module
    from amazon_es_bestseller.collection.detail import collect_details

    class Page:
        url = ""
        def content(self): return "<html><body>Robot Check</body></html>"
    class RestrictedSession:
        def __init__(self):
            self.page = Page()
            self.calls = []
        def goto(self, url): self.calls.append(url); self.page.url = url; return 403
        def wait_for_product_page(self): pass
        def wait_for_price_text(self): pass
        def wait_between_requests(self): pass

    session = RestrictedSession()
    with pytest.raises(AccessStopError):
        collect_details([ASIN, "B000000002"], session, str(tmp_path / "restricted"))
    assert len(session.calls) == 1
    cp = json.loads((tmp_path / "restricted" / "checkpoints" / f"{ASIN}.json").read_text(encoding="utf-8"))
    assert cp["status"] == "access_stop"

    class GoodSession(RestrictedSession):
        def goto(self, url): self.calls.append(url); self.page.url = url; return 200
        def page_content(self): return f'<html><body><input id="ASIN" value="{ASIN}"><h1 id="productTitle">OK</h1></body></html>'
    good = GoodSession()
    good.page.content = good.page_content
    def fail_checkpoint(*args, **kwargs): raise OSError("disk full")
    monkeypatch.setattr(detail_module, "write_checkpoint", fail_checkpoint)
    monkeypatch.setattr(detail_module.time, "sleep", lambda _: None)
    with pytest.raises(OSError, match="disk full"):
        collect_details([ASIN, "B000000002"], good, str(tmp_path / "storage"))
    assert len(good.calls) == 1

