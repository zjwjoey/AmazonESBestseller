import json
from pathlib import Path

from amazon_es_bestseller.quality.chinese import audit_field
from amazon_es_bestseller.translation.repair_queue import apply_repair, build_repair_queue
from amazon_es_bestseller.translation.service import source_hash


def test_real_corpus_sin_coleccion_wu_xilie_is_not_a_negation_mismatch():
    fixture = Path(__file__).parent / "fixtures" / "local_translation_benchmark" / "negation_regressions.json"
    case = json.loads(fixture.read_text(encoding="utf-8"))["cases"][0]
    result = audit_field(asin=case["asin"], field=case["field"], source_es=case["source_text"],
                         translated_zh=case["translated_text"], source_hash=source_hash(case["source_text"]))
    assert "NEGATION_MISMATCH" not in {issue["code"] for issue in result["issues"]}


def test_numeric_units_and_historical_product_type_regressions_fail_closed():
    cases = (("Capacidad 9 L", "x 25.4 L"), ("30 L", "20 L"), ("10 x 15 cm", "10 x 10 mm"),
             ("Bolsa t\u00e9rmica", "\u996d\u76d2"), ("Recipiente reutilizable", "\u4e00\u6b21\u6027"),
             ("Pastillas de limpieza", "\u5496\u5561\u624b\u67c4"), ("Mini motosierra", "\u94fe\u6761\u6cb9"),
             ("Hilo de corte", "\u4fee\u526a\u673a"), ("Portafilter", "\u538b\u7c89\u5668"))
    for source, target in cases:
        assert audit_field(asin="B1", field="title", source_es=source, translated_zh=target,
                           source_hash=source_hash(source))["status"] == "REPAIR"


def test_claim_is_reviewed_and_repair_is_reaudited_and_attempt_limited():
    source = "Bolsa t\u00e9rmica"
    blocked = audit_field(asin="B1", field="title", source_es=source, translated_zh="\u9632\u6c34\u4fdd\u6e29\u888b",
                          source_hash=source_hash(source))
    assert blocked["status"] == "MANUAL_REVIEW"
    item = build_repair_queue([blocked], max_attempts=1)[0]
    # A caller-created PASS does not decide the outcome: fresh audit does.
    promoted = apply_repair(item, source_hash=source_hash(source), candidate="\u4fdd\u6e29\u888b", qa_result={"status": "PASS"})
    assert promoted["status"] == "PASS"
    neg = audit_field(asin="B2", field="description", source_es="Sin alcohol", translated_zh="\u542b\u9152\u7cbe",
                      source_hash=source_hash("Sin alcohol"))
    retry = build_repair_queue([neg], max_attempts=1)[0]
    assert apply_repair(retry, source_hash=source_hash("Sin alcohol"), candidate="\u542b\u9152\u7cbe", qa_result={})["status"] == "MANUAL_REVIEW"
    assert apply_repair({**retry, "attempt": 1}, source_hash=source_hash("Sin alcohol"), candidate="\u4e0d\u542b\u9152\u7cbe", qa_result={})["code"] == "MAX_ATTEMPTS_REACHED"
