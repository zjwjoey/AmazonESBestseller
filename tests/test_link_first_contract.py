from pathlib import Path

from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.collection.ranking_links import parse_ranking_links
from amazon_es_bestseller.quality.staged_fields import audit_detail_fields, resolve_selected_identity


HTML = Path(__file__).with_name("fixtures").joinpath("link_first_detail.html").read_text(encoding="utf-8")


def test_snapshot_never_reads_card_or_href_asin():
    html = '<div id="gridItemRoot" data-asin="B099999999"><span class="zg-bdg-text">#51</span><a href="/dp/B000CRBEJ2?th=1"><img alt="Producto"></a></div>'
    rows = parse_ranking_links(html, "https://www.amazon.es/gp/bestsellers/tools/123", 2, "now", "group")
    assert len(rows) == 1
    assert rows[0]["bestseller_rank"] == 51
    assert rows[0]["ranking_product_url_raw"] == "/dp/B000CRBEJ2?th=1"
    assert not any("asin" in key for key in rows[0])
    assert rows == parse_ranking_links(html, rows[0]["ranking_source_url"], 2, "now", "group")


def test_child_identity_wins_but_visible_parent_is_reviewed():
    result = resolve_selected_identity(HTML, "https://www.amazon.es/dp/B000CRBEJ2", "https://www.amazon.es/dp/B000CRBEJ2")
    assert result["asin"] == "B000CRBEJ2"
    assert result["status"] == "CONFIRMED"
    row = parse_detail_page(HTML, result["asin"])
    audit = audit_detail_fields(HTML, row, result)
    assert audit["status"] == "WARN"
    assert any(i["code"] == "PARENT_DETAIL_SCOPE_REVIEW" for i in audit["issues"])
    assert row["attributes"][2]["value_raw"] == "B0D7MQ7BFB"


def test_conflicting_selected_signals_never_count_as_success():
    html = HTML.replace('"currentAsin":"B000CRBEJ2"', '"currentAsin":"B012345678"')
    assert resolve_selected_identity(html, "", "")["status"] == "BLOCK"


def test_unrelated_redirect_cannot_inherit_rank():
    assert resolve_selected_identity(HTML, "https://www.amazon.es/dp/B012345678", "https://www.amazon.es/dp/B000CRBEJ2")["status"] == "BLOCK"


def test_numeric_book_asin_and_bidi():
    html = '<input id="ASIN" value="\u200e1234567890"><span id="productTitle">Libro</span>'
    result = resolve_selected_identity(html, "https://www.amazon.es/dp/1234567890", "https://www.amazon.es/dp/1234567890")
    assert result["asin"] == "1234567890" and result["status"] == "CONFIRMED"


def test_missing_source_category_is_warning_not_filled():
    identity = resolve_selected_identity(HTML, "", "https://www.amazon.es/dp/B000CRBEJ2")
    row = parse_detail_page(HTML, identity["asin"])
    audit = audit_detail_fields(HTML, row, identity)
    assert any(i["field"] == "category_l3" and i["code"] == "SOURCE_MISSING" for i in audit["issues"])
    assert "category_l3" not in row


def test_unknown_attributes_and_duplicate_label_binding_preserved():
    identity = resolve_selected_identity(HTML, "", "https://www.amazon.es/dp/B000CRBEJ2")
    row = parse_detail_page(HTML, identity["asin"])
    assert audit_detail_fields(HTML, row, identity)["status"] != "BLOCK"
    row["attributes"][3]["value_raw"], row["attributes"][4]["value_raw"] = row["attributes"][4]["value_raw"], row["attributes"][3]["value_raw"]
    result = audit_detail_fields(HTML, row, identity)
    assert any(i["code"] == "FIELD_BINDING_ERROR" for i in result["issues"])


def test_bullets_omission_and_attribute_omission_are_hard_errors():
    identity = resolve_selected_identity(HTML, "", "https://www.amazon.es/dp/B000CRBEJ2")
    row = parse_detail_page(HTML, identity["asin"])
    row["attributes"].pop(1)
    row["feature_bullets_raw"].pop()
    audit = audit_detail_fields(HTML, row, identity)
    assert audit["status"] == "BLOCK"
    assert {"PARSER_MISSED", "BULLETS_BINDING_ERROR"} <= {i["code"] for i in audit["issues"]}


def test_unsafe_link_is_quarantined_without_guessing_asin():
    html = '<div id="gridItemRoot" data-asin="B000CRBEJ2"><span class="zg-bdg-text">#1</span><a href="https://www.amazon.es.evil.test/dp/B000CRBEJ2">Product</a></div>'
    row = parse_ranking_links(html, "https://www.amazon.es/gp/bestsellers/tools/123", 1, "now", "a")[0]
    assert not row["product_link"] and row["snapshot_audit"] == "BLOCK"


def test_visible_rating_omission_is_parser_missed():
    html = HTML.replace('</body>', '<div id="acrPopover"><span class="a-icon-alt">4,5 de 5 estrellas</span></div></body>')
    identity = resolve_selected_identity(html, "", "https://www.amazon.es/dp/B000CRBEJ2")
    row = parse_detail_page(html, identity["asin"])
    row["rating_raw"] = ""
    result = audit_detail_fields(html, row, identity)
    assert any(i["code"] == "PARSER_MISSED" and i["field"] == "rating_raw" for i in result["issues"])
