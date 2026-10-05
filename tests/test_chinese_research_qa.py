from amazon_es_bestseller.quality.chinese import audit_field
from amazon_es_bestseller.quality.chinese_gate import evaluate_chinese_gate
from amazon_es_bestseller.translation.repair_queue import apply_repair, build_repair_queue


def test_numeric_units_negation_and_golden_product_type_fail_closed():
    cases = (
        ("Capacidad 9 L", "\u4ea7\u54c1 25.4 L"),
        ("30 L", "20 L"),
        ("10 x 15 cm", "10 x 10 mm"),
        ("Bolsa t\u00e9rmica", "\u996d\u76d2"),
        ("Pastillas de limpieza", "\u5496\u5561\u624b\u67c4"),
    )
    for source, target in cases:
        row = audit_field(asin="B1", field="title", source_es=source,
                          translated_zh=target, source_hash="h")
        assert row["status"] == "REPAIR"
    assert audit_field(
        asin="B1", field="product_details", source_es="Sin alcohol: No",
        translated_zh="\u4e0d\u542b\u9152\u7cbe\uff1a\u5426", source_hash="h",
    )["status"] == "PASS"


def test_field_repair_is_hash_bound_and_good_fields_unchanged():
    good = audit_field(asin="B1", field="title", source_es="Producto 9 L",
                       translated_zh="\u4ea7\u54c1 9 L", source_hash="h")
    bad = audit_field(asin="B1", field="specification", source_es="30 L",
                      translated_zh="20 L", source_hash="h2")
    queue = build_repair_queue([good, bad])
    assert len(queue) == 1 and queue[0]["field"] == "specification"
    assert queue[0]["old_translation"] == "20 L"
    assert apply_repair(queue[0], source_hash="changed", candidate="30 L",
                        qa_result={"status": "PASS"})["status"] == "BLOCKED"
    repaired = apply_repair(queue[0], source_hash="h2", candidate="30 L",
                            qa_result={"status": "PASS"})
    assert repaired["status"] == "PASS"
    assert evaluate_chinese_gate([good, repaired])["status"] == "SKU_ZH_READY"


def test_empty_source_hash_and_source_change_block_release():
    missing_hash = audit_field(asin="B1", field="description", source_es="Producto",
                               translated_zh="\u4ea7\u54c1", source_hash="")
    assert missing_hash["status"] == "REPAIR"
    assert evaluate_chinese_gate([missing_hash])["status"] == "REVIEW_REQUIRED"
