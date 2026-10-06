from copy import deepcopy

from amazon_es_bestseller.translation.rerender import rerender_affected_fields


def _manifest():
    evidence = {"asin": "B000000001", "source_record_hash": "record-hash",
                "source_hash": "source-hash", "affected_field": "product_details",
                "target_field": "product_details_zh"}
    return {"dictionary_version": 3, "dictionary_hash": "dict-hash",
            "translation_schema_version": "translation-v2.25",
            "promotions": [{"status": "PROMOTED", "source_normalized": "filtro",
                            "target": "滤芯", "context_key": "category=cafetera",
                            "evidence": [evidence]}]}


def _translations():
    return {"B000000001": {"asin": "B000000001", "source_record_hash": "record-hash",
                             "human_note": "do not touch", "fields": {"product_details_zh": {
                                 "source_text": "Filtro", "source_hash": "source-hash",
                                 "translated_text": "旧译文", "translation_status": "success"},
                                 "title_zh": {"source_text": "Titulo", "source_hash": "title-hash",
                                              "translated_text": "好字段", "translation_status": "success"}}}}


def _pass(**kwargs):
    return {"status": "PASS", "asin": kwargs["asin"], "field": kwargs["field"],
            "target_field": kwargs["target_field"], "source_hash": kwargs["source_hash"],
            "target": kwargs["translated_text"], "dictionary_version": kwargs["dictionary_version"],
            "schema_version": kwargs["schema_version"], "context_key": kwargs["context_key"]}


def test_rerender_updates_only_exact_affected_field_and_preserves_master_data():
    translations = _translations()
    calls = []
    def audited_pass(**kwargs):
        calls.append((kwargs["asin"], kwargs["target_field"]))
        return _pass(**kwargs)
    result = rerender_affected_fields([{"asin": "B000000001", "notes": "human"}], translations,
                                      _manifest(), qa_callback=audited_pass)
    field = result["records"]["B000000001"]["fields"]["product_details_zh"]
    assert result["ready"]
    assert field["translated_text"] == "滤芯"
    assert field["rerender_status"] == "READY"
    assert field["dictionary_version"] == "3"
    assert result["records"]["B000000001"]["fields"]["title_zh"]["translated_text"] == "好字段"
    assert result["records"]["B000000001"]["human_note"] == "do not touch"
    assert calls == [("B000000001", "product_details_zh")]
    assert translations == _translations()


def test_rerender_requires_fresh_exact_qa_and_queues_only_affected_field():
    def bare_pass(**kwargs):
        return {"status": "PASS"}
    result = rerender_affected_fields([{"asin": "B000000001"}], _translations(), _manifest(), qa_callback=bare_pass)
    assert not result["ready"]
    assert result["updates"] == []
    assert result["selective_repair"] == [{"asin": "B000000001", "field": "product_details",
                                             "target_field": "product_details_zh", "dictionary_version": "3",
                                             "context_key": "category=cafetera", "reason": "RERENDER_QA_NOT_PASS",
                                             "qa": {"status": "PASS"}}]


def test_rerender_source_record_mismatch_never_mutates_translation():
    broken = _translations(); broken["B000000001"]["source_record_hash"] = "changed"
    before = deepcopy(broken)
    result = rerender_affected_fields([{"asin": "B000000001"}], broken, _manifest(), qa_callback=_pass)
    assert result["selective_repair"][0]["reason"] == "SOURCE_RECORD_CHANGED"
    assert result["records"]["B000000001"]["fields"]["product_details_zh"]["translated_text"] == before["B000000001"]["fields"]["product_details_zh"]["translated_text"]
