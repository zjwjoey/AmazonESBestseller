from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2


def test_parent_child_variation_is_related_not_mismatched():
    html = '''<html><body>
      <input id="ASIN" value="B000000002">
      <span id="productTitle">Variant</span>
      <script>{"currentAsin":"B000000002","parentAsin":"B000000009",
      "dimensionValuesDisplayData":{"B000000001":["Rojo"],"B000000002":["Azul"]}}</script>
    </body></html>'''
    result = parse_detail_evidence_v2(html, "B000000001")
    assert result["identity_status"] == "VARIATION_RELATED"
    assert result["identity_status_code"] == "VARIATION_RELATED"
    assert result["resolved_asin"] == "B000000002"
