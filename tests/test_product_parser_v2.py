from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2


def test_detail_v2_preserves_duplicate_labels_and_variation_evidence():
    html = """
    <html><body>
      <input id="ASIN" value="B000000001">
      <link rel="canonical" href="https://www.amazon.es/dp/B000000001">
      <span id="productTitle">Producto de prueba</span>
      <div id="wayfinding-breadcrumbs_feature_div"><a>Hogar</a><a>Cocina</a></div>
      <div id="productOverview_feature_div"><table>
        <tr><td class="a-span3">Color</td><td class="a-span9">Rojo</td></tr>
        <tr><td class="a-span3">Color</td><td class="a-span9">Azul</td></tr>
      </table></div>
      <script>
      {"currentAsin":"B000000001","parentAsin":"B000000009",
       "dimensions":["Color"],"variationDisplayLabels":{"Color":"Color"},
       "variationValues":{"Color":["Rojo","Azul"]},
       "dimensionValuesDisplayData":{"B000000001":["Rojo"],"B000000002":["Azul"]}}
      </script>
    </body></html>
    """
    result = parse_detail_evidence_v2(
        html, "B000000001", requested_url="https://www.amazon.es/dp/B000000001",
        ranking_context={"ranking_category_path": ["Hogar", "Cocina"],
                         "ranking_category_placement_id": "pl_test"})
    attrs = result["ordered_detail_evidence"]
    assert [row["label_raw"] for row in attrs] == ["Color", "Color"]
    assert [row["position"] for row in attrs] == [0, 1]
    assert result["identity_status"] == "IDENTITY_MATCH"
    assert result["variation_evidence"]["parent_asin"] == "B000000009"
    assert set(result["variation_family_asins"]) == {"B000000001", "B000000002", "B000000009"}
    assert result["category_evidence"]["category_evidence_source"] == "PRODUCT_BREADCRUMB"
