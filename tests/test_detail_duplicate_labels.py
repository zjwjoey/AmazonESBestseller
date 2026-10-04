from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2


def test_duplicate_labels_keep_section_position_and_source():
    html = '''<html><body><span id="productTitle">x</span>
      <div id="prodDetails"><table><tr><th>Modelo</th><td>A</td></tr>
      <tr><th>Modelo</th><td>B</td></tr></table></div></body></html>'''
    rows = parse_detail_evidence_v2(html, "B000000001")["ordered_detail_evidence"]
    model_rows = [row for row in rows if row["label_raw"] == "Modelo"]
    assert len(model_rows) == 2
    assert [row["position"] for row in model_rows] == [0, 1]
    assert all(row["section"] and row["source"] for row in model_rows)
