from copy import deepcopy
from types import SimpleNamespace

from amazon_es_bestseller.orchestration.workflow import ProductionWorkflow
from amazon_es_bestseller.translation.dictionary_sync import sync_evidence
from amazon_es_bestseller.translation.service import source_hash
from amazon_es_bestseller.translation.structured_contract import (
    apply_structured_dictionary_rerender,
    build_structured_dictionary_evidence,
)


def _item(asin, record_hash, item_hash):
    return {
        "asin": asin, "field": "product_details", "item_id": "attr-color", "position": 0,
        "label_raw": "Color", "label_zh": "颜色", "label_status": "PASS", "value_raw": "Rojo",
        "source_hash": item_hash, "admission": "TRANSLATION_TASK", "promotion_status": "PROMOTED",
        "translated_text": "红色", "qa": {"status": "PASS"},
        "label_qa": {"status": "PASS"}, "translation_schema_version": "translation-structured-input-v2",
        "dictionary_version": "1", "dictionary_hash": "old-dict",
    }


def _record(asin, record_hash, item_hash):
    item = _item(asin, record_hash, item_hash)
    return {
        "asin": asin, "source_record_hash": record_hash,
        "human_note": "preserve-note-" + asin,
        "fields": [{
            "asin": asin, "field": "product_details", "target_field": "product_details_zh",
            "field_hash": "field-" + asin, "source_text": "structured:" + asin,
            "source_hash": source_hash("structured:" + asin), "structured_source_hash": "field-" + asin,
            "translation_schema_version": "translation-structured-input-v2",
            "dictionary_version": "1", "dictionary_hash": "old-dict",
            "promotion_status": "PROMOTED", "final_zh": "颜色：红色", "translated_text": "颜色：红色",
            "items": [item],
        }],
    }


def _state():
    return {
        "status": "NONFORMAL_STRUCTURED_OVERLAY", "formal_release": False,
        "records": [
            _record("B000000001", "record-1", "item-1"),
            _record("B000000002", "record-2", "item-2"),
        ],
        "chinese_master_overlay": [
            {"asin": "B000000001", "product_details_zh": "颜色：红色"},
            {"asin": "B000000002", "product_details_zh": "颜色：红色"},
        ],
    }


def _changed_manifest(*, evidence, target="红色"):
    return {
        "dictionary_version": 2, "dictionary_hash": "new-dict",
        "translation_schema_version": "translation-structured-input-v2",
        "change_log": [{"key": "attribute|color|rojo", "old": None, "new": target}],
        "promotions": [{"status": "PROMOTED", "field_type": "color", "source_normalized": "rojo",
                        "target": target, "context_key": "attribute_label=color",
                        "evidence": evidence}],
    }


def test_structured_dictionary_noop_preserves_state_without_provider_or_reqa():
    state = _state()
    result = apply_structured_dictionary_rerender(state, {
        "dictionary_version": 1, "dictionary_hash": "old-dict", "change_log": [], "promotions": [],
    })
    assert result["status"] == "NO_CHANGE"
    assert result["ready"] is True
    assert result["updates"] == []
    assert result["state"] == state
    assert result["state"] is not state


def test_structured_dictionary_rerenders_one_bound_item_and_leaves_unaffected_item_unchanged():
    state = _state()
    before = deepcopy(state)
    evidence = [{"asin": "B000000001", "source_record_hash": "record-1", "item_id": "attr-color",
                 "affected_field": "product_details", "target_field": "product_details_zh",
                 "item_source_hash": "item-1", "source_hash": source_hash("Rojo")}]
    result = apply_structured_dictionary_rerender(state, _changed_manifest(evidence=evidence))
    first, second = result["state"]["records"]
    assert result["status"] == "READY"
    assert [row["asin"] for row in result["updates"]] == ["B000000001"]
    assert first["fields"][0]["items"][0]["effective_render"]["translated_text"] == "红色"
    assert first["fields"][0]["items"][0]["effective_render"]["dictionary_hash"] == "new-dict"
    assert first["human_note"] == before["records"][0]["human_note"]
    assert second == before["records"][1]
    assert state == before


def test_structured_dictionary_rejects_tampered_asin_record_item_source_or_target_binding_and_does_not_pollute_overlay():
    for tampered in ("asin", "record", "item", "source", "target"):
        state = _state()
        evidence = {"asin": "B000000001", "source_record_hash": "record-1", "item_id": "attr-color",
                    "affected_field": "product_details", "target_field": "product_details_zh",
                    "item_source_hash": "item-1", "source_hash": source_hash("Rojo")}
        if tampered == "asin":
            evidence["asin"] = "B000000999"
        elif tampered == "record":
            evidence["source_record_hash"] = "forged"
        elif tampered == "item":
            evidence["item_source_hash"] = "forged"
        elif tampered == "source":
            evidence["source_hash"] = "forged"
        else:
            evidence["target_field"] = "feature_bullets_zh"
        result = apply_structured_dictionary_rerender(state, _changed_manifest(evidence=[evidence]))
        assert result["status"] == "REPAIR_REQUIRED"
        assert result["updates"] == []
        assert result["state"]["chinese_master_overlay"] == state["chinese_master_overlay"]


def test_structured_dictionary_qa_failure_blocks_entire_field_and_never_requests_provider_retry():
    state = _state()
    evidence = [{"asin": "B000000001", "source_record_hash": "record-1", "item_id": "attr-color",
                 "affected_field": "product_details", "target_field": "product_details_zh",
                 "item_source_hash": "item-1", "source_hash": source_hash("Rojo")}]
    result = apply_structured_dictionary_rerender(state, _changed_manifest(evidence=evidence, target="Rojo"))
    field = result["state"]["records"][0]["fields"][0]
    assert result["status"] == "REPAIR_REQUIRED"
    assert field["promotion_status"] == "QA_BLOCKED"
    assert field["final_zh"] == ""
    assert "product_details_zh" not in result["state"]["chinese_master_overlay"][0]
    assert result["repair_queue"]
    assert all(row["auto_provider_repair"] is False and row["strategy"] == "manual_review"
               for row in result["repair_queue"])


def test_structured_dictionary_evidence_sync_conflict_never_promotes_or_mutates_state():
    state = _state()
    evidence, qa = build_structured_dictionary_evidence(state)
    conflicting = deepcopy(evidence[1])
    conflicting["target"] = "蓝色"
    conflicting["evidence_id"] = "B000000002:product_details:attr-color:conflict"
    qa[conflicting["evidence_id"]] = {**qa[evidence[1]["evidence_id"]], "target": "蓝色"}
    sync = sync_evidence([evidence[0], conflicting], qa_results=qa, source_run_id="fixture",
                         translation_schema_version="translation-structured-input-v2",
                         previous={"dictionary_version": 1, "promoted_map": {}})
    assert sync["promotions"] == []
    assert sync["conflicts"][0]["reason"] == "CONTEXT_CONFLICT"
    result = apply_structured_dictionary_rerender(state, sync["manifest"])
    assert result["status"] == "NO_CHANGE"
    assert result["state"] == state


def test_workflow_routes_structured_state_through_dictionary_sync_and_rerender_without_provider(tmp_path):
    class NoProvider:
        def translate(self, *_args, **_kwargs):
            raise AssertionError("structured dictionary rerender must not call a provider")

    task = SimpleNamespace(task_id="structured-rerender", history_dir=tmp_path / "history", translation={})
    workflow = ProductionWorkflow(task, tmp_path / "run", run_id="structured-rerender", translation_provider=NoProvider())
    input_payload = workflow._store("translation-input", {"translation_batch_master": {"records": []}})
    translation_payload = workflow._store("translation", {"state": _state()})
    dictionary_payload = workflow._store("dictionary", {"dictionary_manifest": {
        "dictionary_version": 1, "dictionary_hash": "old-dict", "promoted_map": {},
    }})
    context = {"manifest": {"stages": {
        "translation-input": {"payload": input_payload},
        "translation": {"payload": translation_payload},
        "dictionary": {"payload": dictionary_payload},
    }}}
    result = workflow.stage_dictionary_rerender(context)
    assert result["structured_dictionary_rerender"] is True
    assert result["rerender"]["status"] == "READY"
    assert result["translation_state"]["formal_release"] is False


def test_workflow_structured_dictionary_noop_is_ready_and_never_calls_provider(tmp_path):
    class NoProvider:
        def translate(self, *_args, **_kwargs):
            raise AssertionError("no-op dictionary rerender must not call a provider")

    task = SimpleNamespace(task_id="structured-rerender-noop", history_dir=tmp_path / "history", translation={})
    workflow = ProductionWorkflow(task, tmp_path / "run", run_id="structured-rerender-noop",
                                  translation_provider=NoProvider())
    input_payload = workflow._store("translation-input", {"translation_batch_master": {"records": []}})
    translation_payload = workflow._store("translation", {"state": _state()})
    dictionary_payload = workflow._store("dictionary", {"dictionary_manifest": {
        "dictionary_version": 1, "dictionary_hash": "old-dict",
        "promoted_map": {"color|attribute_label=color;field=product_details|rojo": "红色"},
    }})
    context = {"manifest": {"stages": {
        "translation-input": {"payload": input_payload},
        "translation": {"payload": translation_payload},
        "dictionary": {"payload": dictionary_payload},
    }}}
    result = workflow.stage_dictionary_rerender(context)
    assert result["structured_dictionary_rerender"] is True
    assert result["rerender"]["status"] == "NO_CHANGE"
    assert result["rerender"]["updates"] == []
    assert result["translation_state"] == _state()
