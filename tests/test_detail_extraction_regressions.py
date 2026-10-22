"""Offline extraction regressions: missing evidence stays missing."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amazon_es_bestseller.collection import detail as module
from amazon_es_bestseller.collection.detail import collect_details, parse_detail_page
from amazon_es_bestseller.collection.detail_executor import execute_detail_plan
from amazon_es_bestseller.collection.checkpoints import write_checkpoint
from amazon_es_bestseller.pipeline import normalize_product

ASIN = "B000000001"


def page(asin=ASIN, title="Saved product", body=""):
    return (f'<html><body><input id="ASIN" value="{asin}">'
            f'<h1 id="productTitle">{title}</h1>{body}</body></html>')


class Session:
    def __init__(self, html=None):
        self.html = html
        self.calls = []
        self.delays = 0
        self.page = SimpleNamespace(url="", content=lambda: self.html or page(
            self.page.url.rsplit("/", 1)[-1], "Fresh product"))

    def goto(self, url):
        self.calls.append(url)
        self.page.url = url
        return 200

    def wait_for_product_page(self):
        pass

    def wait_for_price_text(self):
        pass

    def wait_between_requests(self):
        self.delays += 1


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(module, "time", SimpleNamespace(sleep=lambda _: None))


def plan(action):
    return {"snapshot_id": "regression", "records": [{"snapshot_id": "regression",
            "ranking_asin": ASIN, "detail_action": action}]}


def save_html(root, body, name=ASIN):
    (root / "html").mkdir(parents=True, exist_ok=True)
    target = root / "html" / (name + ".html")
    target.write_text(body, encoding="utf-8")
    return target


def test_success_flag_without_record_cannot_skip_extraction(tmp_path):
    write_checkpoint(tmp_path / "checkpoints", ASIN, {"status": "success",
        "snapshot_id": "regression", "action": "FETCH_NEW"})
    session = Session()
    result = execute_detail_plan(plan("FETCH_NEW"), session, str(tmp_path))
    assert len(session.calls) == 1
    assert result["state_record_count"] == 1


def test_reuse_in_new_output_replays_available_html_without_network(tmp_path):
    evidence = tmp_path / "evidence"
    save_html(evidence, page())
    result = execute_detail_plan(plan("REUSE_VALID_CACHE"), None, str(tmp_path / "new"),
                                 saved_html=evidence / "html", offline=True)
    assert result["records"][0]["status"] == "REUSED"
    assert result["state_record_count"] == 1
    assert result["execution_status"] == "COMPLETE"


def test_reuse_without_data_is_pending_not_reused(tmp_path):
    result = execute_detail_plan(plan("REUSE_VALID_CACHE"), None, str(tmp_path), offline=True)
    assert result["records"][0]["status"] == "PENDING"
    assert result["execution_status"] == "INCOMPLETE"


def test_explicit_refetch_preserves_old_html_and_requests_fresh_content(tmp_path):
    original = save_html(tmp_path, page(title="Old product")).read_bytes()
    session = Session()
    result = execute_detail_plan(plan("REFETCH_INVALID_CACHE"), session, str(tmp_path))
    assert len(session.calls) == 1
    assert result["details"][0]["title_es_raw"] == "Fresh product"
    assert any(p.read_bytes() == original for p in (tmp_path / "cache_history").rglob("*.html"))


def test_refetch_does_not_change_historical_challenge_rule(tmp_path):
    from amazon_es_bestseller.access.detector import AccessStopError
    original = save_html(tmp_path, "<html><body>Robot Check</body></html>").read_bytes()
    session = Session()
    with pytest.raises(AccessStopError):
        execute_detail_plan(plan("REFETCH_INVALID_CACHE"), session, str(tmp_path))
    assert session.calls == []
    assert (tmp_path / "html" / (ASIN + ".html")).read_bytes() == original


def test_corrupt_html_isolated_next_saved_product_still_replayed(tmp_path):
    target = save_html(tmp_path, "bad")
    target.write_bytes(b"\xff\xfe\x00broken")
    save_html(tmp_path, page("B000000002"), "B000000002")
    session = Session()
    result = collect_details([ASIN, "B000000002"], session, str(tmp_path))
    assert [r["asin"] for r in result] == ["B000000002"]
    assert session.calls == []
    assert target.read_bytes() == b"\xff\xfe\x00broken"
    checkpoint = json.loads((tmp_path / "checkpoints" / (ASIN + ".json")).read_text(encoding="utf-8"))
    assert checkpoint["detail_status"] == "CACHE_READ_ERROR"


def test_invalid_and_identity_conflict_pages_count_progress_and_keep_pacing(tmp_path):
    events = []
    for i, html in enumerate(["<html><body>Unavailable</body></html>", page("B000000002")]):
        session = Session(html)
        collect_details([ASIN], session, str(tmp_path / str(i)), on_progress=events.append)
        assert events[-1]["completed"] == events[-1]["total"] == 1
        assert session.delays == 1


def test_custom_checkpoint_directory_is_read_and_written_consistently(tmp_path):
    session = Session()
    checkpoints = tmp_path / "external_checkpoints"
    for _ in range(2):
        execute_detail_plan(plan("FETCH_NEW"), session, str(tmp_path / "run"),
                            checkpoint_root=checkpoints)
    assert (checkpoints / (ASIN + ".json")).is_file()
    assert len(session.calls) == 1


def test_legacy_collector_stop_preserves_successful_records_and_manifest(tmp_path):
    from amazon_es_bestseller.access.detector import AccessStopError

    def legacy(asins, session, out_dir, *, request_urls, execution_context):
        write_checkpoint(Path(out_dir) / "checkpoints", ASIN, {"status": "success",
            "snapshot_id": "regression", "action": "FETCH_NEW",
            "record": {"asin": ASIN, "title_es_raw": "Saved product"}})
        raise AccessStopError("HTTP 403")

    with pytest.raises(AccessStopError):
        execute_detail_plan(plan("FETCH_NEW"), None, str(tmp_path), collector=legacy)
    manifest = json.loads((tmp_path / "detail_execution_manifest.json").read_text(encoding="utf-8"))
    assert manifest["access_stop"] == "HTTP 403"
    assert json.loads((tmp_path / "details.json").read_text(encoding="utf-8"))[0]["asin"] == ASIN


def test_monthly_bought_never_borrows_recommended_or_hidden_products():
    html = page(body='''<div id="desktop-dp-sims"><div class="social-proofing-faceout">
      900+ comprados el mes pasado</div></div>
      <div hidden class="social-proofing-faceout">800+ comprados el mes pasado</div>''')
    assert parse_detail_page(html, ASIN)["monthly_bought_raw"] == ""


def test_current_price_visible_markup_without_offscreen_is_not_lost():
    html = page(body='''<div id="apex_desktop"><span class="a-price priceToPay apex-pricetopay-value">
        <span aria-hidden="true">8 , 55 €</span></span></div>''')
    assert normalize_product(parse_detail_page(html, ASIN))["current_price"] == 8.55


def test_struck_or_unit_price_cannot_become_current_price():
    html = page(body='''<div id="corePrice_feature_div">
      <span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">19,99 €</span></span>
      <span class="a-price pricePerUnit"><span class="a-offscreen">4,99 €/kg</span></span></div>''')
    assert parse_detail_page(html, ASIN)["current_price_raw"] == ""


def test_all_additional_information_sections_and_unknown_attributes_survive():
    html = page(body='''<div id="productOverview_feature_div"><table><tr><th>Tipo de cierre</th>
      <td>Magnético</td></tr></table></div>
      <div id="detailBullets_feature_div"><dl><dt>Etiqueta nueva</dt><dd>Valor único</dd></dl></div>
      <table id="productDetails_detailBullets_sections1"><tr><th>Material especial</th>
      <td>Acero</td></tr></table>
      <div id="detailBulletsWrapper_feature_div"><ul><li>Capacidad: 500 mililitros</li></ul></div>''')
    raw = parse_detail_page(html, ASIN)
    pairs = {(r["label_raw"], r["value_raw"]) for r in raw["attributes"]}
    assert {("Tipo de cierre", "Magnético"), ("Etiqueta nueva", "Valor único"),
            ("Material especial", "Acero"), ("Capacidad", "500 mililitros")} <= pairs
    normalized = normalize_product(raw)
    assert "Valor único" in normalized["product_details_es"]
    assert "500" in normalized["specification_es"]


def test_dedicated_description_fallback_excludes_a_plus_and_hidden_text():
    html = page(body='''<div id="productDescription_feature_div"></div>
      <div id="productDescription">Descripción guardada<span hidden>ruido</span></div>
      <div id="aplus">Contenido A+ separado</div>''')
    assert parse_detail_page(html, ASIN)["product_description_raw"] == "Descripción guardada"
    assert parse_detail_page(page(body='<div id="aplus">Solo A+</div>'), ASIN)["product_description_raw"] == ""


def test_absent_fields_stay_empty_and_unknown_attributes_are_not_invented():
    raw = parse_detail_page(page(), ASIN)
    product = normalize_product(raw)
    assert raw["attributes"] == []
    assert raw["product_description_raw"] == ""
    assert product["monthly_bought_min"] is None
    assert product["current_price"] is None
    assert product["specification_es"] == ""


def test_additional_sections_two_and_standalone_technical_table_are_preserved():
    raw = parse_detail_page(page(body='''<table id="productDetails_techSpec_sections2">
       <tr><th>Certificación nueva</th><td>EN 123</td></tr></table>
       <table id="productDetails_detailBullets_sections2"><tr><th>Modelo</th><td>M2</td></tr></table>'''), ASIN)
    assert {(a["label_raw"], a["value_raw"]) for a in raw["attributes"]} == {
        ("Certificación nueva", "EN 123"), ("Modelo", "M2")}


def test_distinct_values_with_same_label_survive_full_detail_display():
    from amazon_es_bestseller.translation.full_detail import render_details_es
    attrs = [{"label_raw": "Material", "value_raw": value} for value in ["Acero", "Aluminio"]]
    result = render_details_es(attrs)
    assert "Material: Acero" in result
    assert "Material: Aluminio" in result


def test_legacy_saved_bullet_facts_reach_core_spec_without_mutating_input():
    source = {"asin": ASIN, "title_es_raw": "Producto", "detail_bullets_raw": ["Capacidad: 500 mililitros"]}
    before = json.dumps(source)
    product = normalize_product(source)
    assert "500" in product["specification_es"]
    assert "500" in product["product_details_es"]
    assert json.dumps(source) == before


def test_checkout_launcher_does_not_use_another_installed_checkout():
    import os
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts" / "amazon_es.py"), "--runtime-info"],
                            cwd=root, capture_output=True, text=True, check=True,
                            env={**os.environ, "PYTHONPATH": str(root / ".tmp" / "unrelated")})
    info = json.loads(result.stdout)
    assert Path(info["module_path"]).is_relative_to(root / "src")


def test_saved_multivalue_pack_count_cannot_crash_or_be_guessed():
    from amazon_es_bestseller.normalization.specification import package_count
    source = {"asin": "B094K1DN4D", "title_es_raw": "Espejo Personalizado De Mano Doble Cara",
              "attributes": [{"label_raw": "Número de piezas", "value_raw": "1, 24, 48"}]}
    before = json.dumps(source)
    product = normalize_product(source)
    assert package_count({"numero_de_piezas": "1, 24, 48"}) is None
    assert product["attributes"] == source["attributes"]
    assert json.dumps(source) == before


def test_current_invalid_evidence_cannot_be_hidden_by_old_success(tmp_path):
    (tmp_path / "details.json").write_text(json.dumps([
        {"asin": ASIN, "title_es_raw": "Old product"}]), encoding="utf-8")
    target = save_html(tmp_path, "broken")
    target.write_bytes(b"\xff\xfeinvalid")
    result = execute_detail_plan(plan("REUSE_VALID_CACHE"), None, str(tmp_path), offline=True)
    assert result["records"][0]["status"] == "PENDING"
    assert result["execution_status"] == "INCOMPLETE"


def test_internal_collector_type_error_is_never_retried(tmp_path):
    calls = []
    def broken(*args, **kwargs):
        calls.append(True)
        raise TypeError("internal parser got an unexpected keyword argument")
    with pytest.raises(TypeError):
        execute_detail_plan(plan("FETCH_NEW"), None, str(tmp_path), collector=broken)
    assert calls == [True]


_HISTORICAL = json.loads((Path(__file__).parent / "fixtures" /
    "historical_detail_field_excerpts.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _HISTORICAL["cases"], ids=lambda row: row["asin"])
def test_saved_historical_audit_excerpt_is_extracted_without_imputation(case):
    from amazon_es_bestseller.normalization.monthly_bought import parse_monthly_bought
    from amazon_es_bestseller.normalization.price import parse_price
    assert case["original_full_html"] is False
    raw = parse_detail_page(case["reduced_regression_html"], case["asin"])
    if case["field"] == "月购买量":
        assert parse_monthly_bought(raw["monthly_bought_raw"]) == parse_monthly_bought(case["archived_text"])
    elif case["field"] == "当前售价":
        assert parse_price(raw["current_price_raw"]) == parse_price(case["archived_text"])
    else:
        assert raw["product_description_raw"] == case["archived_text"].strip()


def test_real_saved_amazon_page_keeps_collapsed_technical_facts():
    import re
    from bs4 import BeautifulSoup
    evidence = Path(__file__).parent / "fixtures/consolidation_v2/B013ERLQDK.html"
    html = evidence.read_text(encoding="utf-8")
    raw = parse_detail_page(html, "B013ERLQDK")
    actual = {(a["label_raw"], a["value_raw"]) for a in raw["attributes"]}
    source = BeautifulSoup(html, "lxml")
    for tr in source.select("#prodDetails table tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) == 2:
            pair = tuple(re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip() for cell in cells)
            if all(pair):
                assert pair in actual
    assert ("Número Modelo", "STST1-75518") in actual
    assert raw["current_price_raw"] == "8,99€"
    assert raw["monthly_bought_raw"] == "500+"


def test_known_expandable_attribute_full_text_survives_without_preview_duplicate():
    html = page(body='''<div id="prodDetails"><div class="a-expander-content" style="display:none">
       <table><tr><th>Material</th><td>Acero</td></tr></table></div></div>
       <div id="productOverview_feature_div"><table><tr><td>Detalle</td><td>
       <span class="a-truncate"><span class="a-truncate-full a-offscreen">Texto completo largo</span>
       <span class="a-truncate-cut">Texto...</span></span></td></tr></table></div>''')
    attrs = parse_detail_page(html, ASIN)["attributes"]
    assert ("Material", "Acero") in {(a["label_raw"], a["value_raw"]) for a in attrs}
    assert next(a["value_raw"] for a in attrs if a["label_raw"] == "Detalle") == "Texto completo largo"
