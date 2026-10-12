from amazon_es_bestseller.translation.production_contract import canonical_record
from amazon_es_bestseller.translation.production import build_production_input


def test_raw_bullet_array_takes_priority_over_flattened_display_alias():
    row = {"asin": "B000000001", "feature_bullets_es": "Display summary",
           "feature_bullets_raw": ["Primer punto\ncontinuación", "Segundo punto"]}
    assert canonical_record(row)["feature_bullets"] == row["feature_bullets_raw"]
    item = build_production_input([row])["records"][0]
    assert item["fields"]["feature_bullets"]["raw_value"] == row["feature_bullets_raw"]


def test_empty_raw_bullet_array_does_not_inherit_old_display_content():
    assert canonical_record({"asin": "B000000001", "feature_bullets_raw": [],
                             "feature_bullets_es": "Old display"})["feature_bullets"] == []
