from amazon_es_bestseller.quality.chinese import audit_field
from amazon_es_bestseller.quality.chinese_gate import evaluate_chinese_gate
from amazon_es_bestseller.translation.repair_queue import apply_repair, build_repair_queue
from amazon_es_bestseller.translation.service import source_hash


def test_numeric_units_negation_and_golden_product_type_fail_closed():
    cases = (
        ("Capacidad 9 L", "\u4ea7\u54c1 25.4 L"),
        ("30 L", "20 L"),
        ("10 x 15 cm", "10 x 10 mm"),
        ("Bolsa t\u00e9rmica", "\u996d\u76d2"),
        ("Recipiente reutilizable", "\u4e00\u6b21\u6027\u5bb9\u5668"),
        ("Pastillas de limpieza", "\u5496\u5561\u624b\u67c4"),
        ("Mini motosierra", "\u94fe\u6761\u6cb9"),
        ("Hilo de corte", "\u4fee\u526a\u673a"),
        ("Portafilter", "\u538b\u7c89\u5668"),
    )
    for source, target in cases:
        row = audit_field(asin="B1", field="title", source_es=source,
                          translated_zh=target, source_hash=source_hash(source))
        assert row["status"] == "REPAIR"
    assert audit_field(
        asin="B1", field="product_details", source_es="Sin alcohol: No",
        translated_zh="\u4e0d\u542b\u9152\u7cbe\uff1a\u5426", source_hash=source_hash("Sin alcohol: No"),
    )["status"] == "PASS"


def test_field_repair_is_hash_bound_and_good_fields_unchanged():
    good = audit_field(asin="B1", field="title", source_es="Producto 9 L",
                       translated_zh="\u4ea7\u54c1 9 L", source_hash=source_hash("Producto 9 L"))
    bad = audit_field(asin="B1", field="specification", source_es="30 L",
                      translated_zh="20 L", source_hash=source_hash("30 L"))
    queue = build_repair_queue([good, bad])
    assert len(queue) == 1 and queue[0]["field"] == "specification"
    assert queue[0]["old_translation"] == "20 L"
    assert apply_repair(queue[0], source_hash="changed", candidate="30 L",
                        qa_result={"status": "PASS"})["status"] == "BLOCKED"
    qa = audit_field(asin="B1", field="specification", source_es="30 L",
                     translated_zh="30 L", source_hash=source_hash("30 L"))
    repaired = apply_repair(queue[0], source_hash=source_hash("30 L"), candidate="30 L", qa_result=qa)
    assert repaired["status"] == "PASS"
    assert evaluate_chinese_gate([good, repaired])["status"] == "SKU_ZH_READY"


def test_empty_source_hash_and_source_change_block_release():
    missing_hash = audit_field(asin="B1", field="description", source_es="Producto",
                               translated_zh="\u4ea7\u54c1", source_hash="")
    assert missing_hash["status"] == "REPAIR"
    assert evaluate_chinese_gate([missing_hash])["status"] == "REVIEW_REQUIRED"


def test_current_source_hash_and_exact_repair_tuple_are_required():
    row = audit_field(asin="B1", field="title", source_es="Bolsa térmica",
                      translated_zh="防水保温袋", source_hash=source_hash("Bolsa térmica"))
    assert row["status"] == "MANUAL_REVIEW"
    queue = build_repair_queue([row])
    assert apply_repair(queue[0], source_hash=source_hash("Bolsa térmica"), candidate="保温袋", qa_result={"status": "PASS"})["status"] == "MANUAL_REVIEW"
