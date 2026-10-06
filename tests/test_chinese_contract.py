from amazon_es_bestseller.quality.chinese import audit_field, qa_payload_hash
from amazon_es_bestseller.quality.chinese_gate import evaluate_chinese_gate
from amazon_es_bestseller.translation.production import build_release_translation_candidate
from amazon_es_bestseller.translation.rerender import rerender_affected_fields
from amazon_es_bestseller.translation.service import source_hash


def _qa_row():
    source = "Botella de acero 500 ml"
    return audit_field(
        asin="B000000001", field="title_es_raw", target_field="title_zh", field_type="title_zh",
        source_es=source, translated_zh="不锈钢水瓶 500 ml", source_hash=source_hash(source),
        dictionary_version="7", translation_schema_version="translation-v2.25",
        context={"field": "title_es_raw", "target_field": "title_zh"},
    )


def test_chinese_qa_contract_and_gate_recompute_readiness():
    row = _qa_row()
    assert row["status"] == "PASS"
    assert {"asin", "field", "target_field", "field_type", "source_text", "source_hash",
            "translated_text", "target_value", "candidate_hash", "context", "dictionary_version",
            "translation_schema_version", "status", "issues"} <= set(row)
    gate = evaluate_chinese_gate([{**row, "ready": False}])
    assert gate["ready"] and gate["status"] == "SKU_ZH_READY"
    assert gate["qa_payload_hash"] == qa_payload_hash([row])
    forged = evaluate_chinese_gate([{**row, "status": "REPAIR", "ready": True}])
    assert not forged["ready"] and forged["status"] == "BLOCKED"


def test_release_candidate_uses_one_target_value_and_field_binding():
    source = "Botella de acero 500 ml"
    state = {"input_manifest": {"translation_schema_version": "translation-v2.25"}, "records": [{
        "asin": "B000000001", "source_record_hash": "record-hash", "fields": [{
            "field": "title_es_raw", "target_field": "title_zh", "source_text": source,
            "source_hash": source_hash(source), "final_zh": "不锈钢水瓶 500 ml",
            "promotion_status": "PROMOTED", "dictionary_version": "7",
            "translation_schema_version": "translation-v2.25",
            "context": {"field": "title_es_raw", "target_field": "title_zh"},
        }],
    }]}
    result = build_release_translation_candidate(state)
    field = result["records"][0]["field_candidates"][0]
    assert result["release_status"] == "READY"
    assert field["field"] == "title_es_raw" and field["target_field"] == "title_zh"
    assert field["field_type"] == "title_zh" and field["target_value"] == "不锈钢水瓶 500 ml"
    assert field["candidate_hash"]


def test_dictionary_noop_is_ready_without_rerendering_unaffected_fields():
    result = rerender_affected_fields([], {}, {"dictionary_version": 7, "dictionary_hash": "same",
                                                "translation_schema_version": "translation-v2.25",
                                                "promotions": []})
    assert result["ready"] and result["status"] == "NO_CHANGE"
    assert result["updates"] == [] and result["selective_repair"] == []
