from amazon_es_bestseller.collection.field_completeness import (
    EXTRACTED, LOW_QUALITY, PARSER_MISSED, SOURCE_ABSENT,
    FieldCompletenessMonitor, check_detail_fields,
)
from amazon_es_bestseller.task_workspace import DEFAULT_THRESHOLDS

ASIN = "B000000001"
URL = "https://www.amazon.es/dp/" + ASIN
PRODUCT = '<input id="ASIN" value="' + ASIN + '"><span id="productTitle">Bolso azul</span>'
META = {"status_code": 200, "access_state": "NORMAL", "final_url": URL}
RECORD = dict(asin=ASIN, requested_asin=ASIN, resolved_asin=ASIN,
              identity_status="MATCH", title_es_raw="Bolso azul")


def test_known_source_miss_absence_and_low_quality_are_distinct():
    valid, fields, anomalies = check_detail_fields(
        ASIN, {**RECORD, "product_description_raw": "1"}, {"status": "success"},
        PRODUCT + '<div id="feature-bullets"><li>Dos bolsillos</li></div>'
        '<div id="aplus"><p>Vídeo y detalles adicionales</p></div>', META)
    assert valid
    assert fields["asin"]["state"] == EXTRACTED
    assert fields["feature_bullets_raw"]["state"] == PARSER_MISSED
    assert fields["attributes"]["state"] == SOURCE_ABSENT
    assert fields["product_description_raw"]["state"] == LOW_QUALITY
    assert fields["date_first_available_raw"]["state"] == SOURCE_ABSENT
    monitor = FieldCompletenessMonitor(DEFAULT_THRESHOLDS)
    summary = monitor.summarize([{"fields": fields}])
    assert summary["feature_bullets_raw"]["parser_missed_rate"] == 1
    assert summary["attributes"]["parser_missed_count"] == 0
    assert summary["attributes"]["parser_missed_rate"] is None
    assert monitor.signals(summary, summary) == []


def test_success_tag_cannot_override_wrong_identity_or_blank_title():
    valid, fields, anomalies = check_detail_fields(ASIN, RECORD, {"status": "success"},
        PRODUCT.replace(ASIN, "B000000002"), META)
    assert not valid
    assert {"field": "asin", "code": "DETAIL_IDENTITY_UNCONFIRMED"} in anomalies
    assert not fields["title_es_raw"]["eligible"]
    assert not fields["title_es_raw"]["source_present"]
    valid, fields, _ = check_detail_fields(ASIN, {}, {"status": "invalid"},
        PRODUCT.replace("Bolso azul", ""), META)
    assert not valid and fields["title_es_raw"]["eligible"]
    monitor = FieldCompletenessMonitor(DEFAULT_THRESHOLDS)
    report = monitor.summarize([{"fields": fields}] * 50)
    assert monitor.signals(report, report) == [
        {"level": "PAUSE", "scope": "batch", "field": "title_es_raw", "rate": 1.0},
        {"level": "PAUSE", "scope": "task", "field": "title_es_raw", "rate": 1.0},
    ]


def test_saved_source_binds_details_and_quarantines_mixed_bullets():
    html = (PRODUCT + '<div id="feature-bullets"><li>Dos bolsillos</li></div>'
            '<div id="productOverview_feature_div"><table><tr><td>Color</td><td>Azul</td></tr></table></div>')
    valid, fields, anomalies = check_detail_fields(
        ASIN, {**RECORD, "feature_bullets_raw": ["Motor de 2400 W"]}, {"status": "success"}, html, META)
    assert not valid
    assert fields["attributes"]["state"] == PARSER_MISSED
    assert fields["feature_bullets_raw"]["state"] == LOW_QUALITY
    assert {"field": "feature_bullets_raw", "code": "DETAIL_FIELD_NOT_BOUND_TO_SOURCE"} in anomalies


def test_informational_content_is_not_a_missing_product_description():
    html = (PRODUCT + '<div id="productDescription"><h2>Descripción del producto</h2></div>'
            '<div id="aplus"><p>Contenido ampliado</p></div>')
    valid, fields, _ = check_detail_fields(ASIN, RECORD, {"status": "success"}, html, META)
    assert valid
    for name in ("feature_bullets_raw", "product_description_raw", "selected_variation_raw", "seller_raw"):
        assert fields[name]["state"] == SOURCE_ABSENT
        assert not fields[name]["source_present"]
