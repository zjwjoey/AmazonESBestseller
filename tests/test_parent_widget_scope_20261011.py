from copy import deepcopy

from amazon_es_bestseller.quality.parent_scope import review_parent_scope


CHILD = "B000000001"
HTML = '''<div id="productOverview_feature_div" data-csa-c-asin="B000000001">
<table><tr><td>Color</td><td>Rojo</td></tr></table></div>
<div id="prodDetails"><table><tr><th>Material</th><td>Metal</td></tr></table>
<table><tr><th>ASIN</th><td>B000000001</td></tr></table></div>
<div id="featurebullets_feature_div" data-csa-c-asin="B000000001">
<div id="feature-bullets"><ul><li>Resistente</li></ul></div></div>
<div id="detailBulletsWrapper_feature_div"><ul>
<li><b>ASIN:</b> B000000001</li><li><b>Color:</b> Rojo</li></ul></div>'''


def row():
    return {"asin": CHILD, "attributes": [
        {"section": "product_overview", "label_raw": "Color", "value_raw": "Rojo"},
        {"section": "technical_details", "label_raw": "Material", "value_raw": "Metal"},
        {"section": "technical_details", "label_raw": "ASIN", "value_raw": CHILD}],
        "feature_bullets_raw": ["Resistente"], "detail_bullets_raw": ["ASIN: B000000001", "Color: Rojo"]}


def test_explicit_child_widgets_and_local_detail_identity_bind_exact_occurrences():
    source = row()
    before = deepcopy(source)
    assert review_parent_scope(HTML, source)["all_child_scope_confirmed"]
    assert source == before


def test_directional_mark_next_to_asin_does_not_destroy_exact_child_anchor():
    source = row()
    source["detail_bullets_raw"][0] = "ASIN ‏ : ‎ B000000001"
    marked = HTML.replace('<b>ASIN:</b> B000000001', '<b>ASIN ‏ :</b> ‎ B000000001')
    assert review_parent_scope(marked, source)["all_child_scope_confirmed"]


def test_parent_widget_does_not_confirm_child():
    assert not review_parent_scope(HTML.replace('data-csa-c-asin="B000000001"',
                                              'data-csa-c-asin="B000000002"'), row())["all_child_scope_confirmed"]


def test_wrong_bullet_value_or_order_cannot_be_bound():
    source = row()
    source["feature_bullets_raw"] = ["Otro"]
    assert not review_parent_scope(HTML, source)["all_child_scope_confirmed"]
    source = row()
    source["detail_bullets_raw"].reverse()
    assert not review_parent_scope(HTML, source)["all_child_scope_confirmed"]


def test_unscoped_description_is_not_promoted_by_page_identity():
    source = row()
    source["product_description_raw"] = "Texto genérico"
    assert not review_parent_scope(HTML, source)["all_child_scope_confirmed"]


def test_foreign_asin_in_local_table_group_remains_unconfirmed():
    bad = HTML.replace('<th>Material</th><td>Metal</td>', '<th>ASIN</th><td>B000000002</td>')
    assert not review_parent_scope(bad, row())["all_child_scope_confirmed"]
