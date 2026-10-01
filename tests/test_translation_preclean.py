import json

from amazon_es_bestseller.translation.preclean import audit_records, clean_text, write_reports


def test_preclean_preserves_raw_and_removes_only_deterministic_noise(tmp_path):
    raw = "\u200fModelo: X1\nModelo: X1\nPeso: 4,25 kg\nVer más"
    result = audit_records([{
        "asin": "B00000001",
        "title_es_raw": "Producto 500 ml",
        "product_details_es": raw,
        "feature_bullets_es": ["USB-C 5W", "USB-C 5W", ""],
    }])
    row = result["translation_input_records"][0]
    assert row["raw_fields"]["product_details"]["source_hash"]
    assert row["fields"]["product_details"]["clean_text"] != raw
    assert "DUPLICATE_LABEL" in result["summary"]["issue_counts"]
    assert result["summary"]["identity_count"] == 2
    assert all(record.get("product_details_es") != row["fields"]["product_details"]["clean_text"]
               for record in [{"product_details_es": raw}])


def test_preclean_statuses_and_reports_are_reproducible(tmp_path):
    records = [{"asin": "B00000002", "title_es_raw": "   ", "category_l1": "Coche y moto"}]
    first = audit_records(records)
    second = audit_records(records)
    assert json.dumps(first["translation_input_records"], ensure_ascii=False, sort_keys=True) == \
           json.dumps(second["translation_input_records"], ensure_ascii=False, sort_keys=True)
    assert first["translation_input_records"][0]["fields"]["title_es_raw"]["clean_status"] == "SOURCE_MISSING"
    paths = write_reports(first, tmp_path)
    for name in ("preclean_audit.json", "preclean_audit.md", "field_quality.csv",
                 "sku_quality.csv", "cross_field_issues.csv", "structure_issues.csv",
                 "language_profile.csv", "identity_summary.csv", "unit_numeric_issues.csv",
                 "review_queue.csv", "translation_input_records.json"):
        assert name in paths
        assert (tmp_path / name).exists()


def test_clean_text_does_not_change_normal_source():
    result = clean_text("Producto para coche", field="title_es_raw")
    assert result["clean_status"] == "CLEAN"
    assert result["clean_text"] == result["source_text"]


def test_description_metadata_is_flagged_not_repaired():
    result = audit_records([{
        "asin": "B00000003",
        "product_description_raw": "ASIN B00000001 valoración 4,5 reviews",
    }])
    field = result["translation_input_records"][0]["fields"]["product_description"]
    assert field["clean_status"] == "NEEDS_REVIEW"
    assert "POSSIBLE_METADATA_IN_DESCRIPTION" in field["issues"]
    assert field["translate_allowed"] is False


def test_cross_field_review_blocks_both_fields_and_enters_queue():
    result = audit_records([{
        "asin": "B00000004",
        "title_es_raw": "Taladro profesional 18V",
        "specification_es": "Taladro profesional 18V",
    }])
    row = result["translation_input_records"][0]
    assert row["record_status"] == "NEEDS_REVIEW"
    assert row["fields"]["title_es_raw"]["translate_allowed"] is False
    assert row["fields"]["specification_es"]["translate_allowed"] is False
    assert result["review_queue"]


def test_exact_detail_duplicates_are_removed_only_from_derived_rows():
    result = audit_records([{
        "asin": "B00000005",
        "product_details_es": "Modelo: X1\nModelo: X1",
    }])
    row = result["translation_input_records"][0]
    assert len(row["details_structured"]) == 1
    assert result["summary"]["identity_count"] == 2
    assert row["raw_fields"]["product_details"]["source_present"] is True
    assert row["fields"]["product_details"]["protected_tokens"] == []
