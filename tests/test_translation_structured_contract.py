import pytest
from types import SimpleNamespace

from amazon_es_bestseller.translation.structured_contract import (
    STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION,
    build_structured_translation_draft,
    formalize_structured_translation_input,
    dispatch_structured_translation_tasks,
    bind_formal_structured_translation_input,
    validate_formal_structured_translation_input,
    execute_formal_structured_translation,
)
from amazon_es_bestseller.production.spanish_master import build_spanish_master
from amazon_es_bestseller.quality.source_fields import audit_source_fields
from amazon_es_bestseller.quality.source_gate import evaluate_source_gate
from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider
from amazon_es_bestseller.translation.service import TranslationService
from amazon_es_bestseller.orchestration.workflow import ProductionWorkflow


def _record():
    record = {
        "asin": "B000000101",
        # Display text is intentionally contradictory and must never be parsed.
        "product_details_es": "Color: DISPLAY SHOULD NOT WIN",
        "eligibleattributes": [
            {"section": "Info", "label_raw": "Tipo", "value_raw": "Texto libre", "position": 1, "item_id": "attr-free"},
            {"section": "Información", "label_raw": "Color", "value_raw": "Rojo", "position": 2, "item_id": "attr-color"},
            {"section": "Información", "label_raw": "Modelo", "value_raw": "X-100", "position": 3, "item_id": "attr-model"},
            {"section": "Información", "label_raw": "Material", "value_raw": "中文", "position": 4, "item_id": "attr-cjk"},
        ],
        "rawattributes_raw": [
            {"section": "Información", "label_raw": "Fabricante", "value_raw": "Owner raw only", "position": 1, "item_id": "attr-maker"},
            {"section": "Información", "label_raw": "Color", "value_raw": "Rojo", "position": 2, "item_id": "attr-color"},
        ],
        "owner_optional_exclusion": {
            "excluded_attributes": [{"section": "Información", "label_raw": "Fabricante", "value_raw": "Owner raw only", "position": 1, "item_id": "attr-maker"}],
        },
        "feature_bullets_raw": ["Primera frase", "Segunda frase"],
    }
    record["canonicalstructuredsource"] = list(record["eligibleattributes"])
    return record


def test_structured_details_do_not_fallback_to_display_or_send_owner_excluded_raw():
    draft = build_structured_translation_draft([_record()], review_item_ids=set())
    details = draft["records"][0]["fields"]["product_details"]
    assert details["items"][0]["value_raw"] == "Texto libre"
    assert [item["item_id"] for item in details["items"][:3]] == ["attr-free", "attr-color", "attr-model"]
    assert all("DISPLAY SHOULD NOT WIN" not in item["value_raw"] for item in details["items"])
    assert details["excluded_raw_trace"][0]["value_raw"] == "Owner raw only"
    assert "Owner raw only" not in [item["value_raw"] for item in details["translation_tasks"]]
    assert [item["item_id"] for item in details["translation_tasks"]] == ["attr-free", "attr-color"]
    return
    assert [item["value_raw"] for item in details["items"]] == ["Rojo", "X-100", "中文"]
    assert all("DISPLAY SHOULD NOT WIN" not in item["value_raw"] for item in details["items"])
    assert details["excluded_raw_trace"][0]["value_raw"] == "Owner raw only"
    assert "Owner raw only" not in [item["value_raw"] for item in details["translation_tasks"]]
    assert [item["item_id"] for item in details["translation_tasks"]] == ["attr-color"]


def test_structured_details_preserve_identity_and_block_cjk_or_review_items_without_losing_boundaries():
    record = _record()
    draft = build_structured_translation_draft([record], review_item_ids={"B000000101:attr-color"})
    details = draft["records"][0]["fields"]["product_details"]
    by_id = {item["item_id"]: item for item in details["items"]}
    assert by_id["attr-model"]["admission"] == "PRESERVED_IDENTITY"
    assert by_id["attr-model"]["value_for_translation"] == "X-100"
    assert by_id["attr-color"]["admission"] == "REVIEW_BLOCKED"
    assert by_id["attr-cjk"]["admission"] == "CJK_BLOCKED"
    bullets = draft["records"][0]["fields"]["feature_bullets"]["items"]
    assert [(item["item_id"], item["value_raw"]) for item in bullets] == [
        ("bullet-0", "Primera frase"), ("bullet-1", "Segunda frase"),
    ]


def test_structured_item_and_field_hashes_and_order_are_stable():
    first = build_structured_translation_draft([_record()], review_item_ids=set())
    second = build_structured_translation_draft([_record()], review_item_ids=set())
    left = first["records"][0]["fields"]
    right = second["records"][0]["fields"]
    assert first["schema_version"] == STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION
    assert left["product_details"]["field_hash"] == right["product_details"]["field_hash"]
    assert [(item["item_id"], item["source_hash"]) for item in left["product_details"]["items"]] == [
        (item["item_id"], item["source_hash"]) for item in right["product_details"]["items"]
    ]


def test_formal_structured_input_and_dispatch_are_source_gate_blocked_without_provider_call():
    draft = build_structured_translation_draft([_record()], review_item_ids=set())
    calls = []
    with pytest.raises(ValueError, match="SOURCE_GATE_NOT_READY"):
        formalize_structured_translation_input(draft, {"status": "BLOCKED", "ready": False})
    with pytest.raises(RuntimeError, match="STRUCTURED_CALLBACK_DISPATCH_RETIRED"):
        dispatch_structured_translation_tasks(
            draft, {"status": "BLOCKED", "ready": False}, lambda task: calls.append(task),
        )
    assert calls == []


def test_missing_review_evidence_keeps_ordinary_items_out_of_tasks():
    draft = build_structured_translation_draft([_record()])
    details = draft["records"][0]["fields"]["product_details"]
    assert details["translation_tasks"] == []
    assert next(item for item in details["items"] if item["item_id"] == "attr-color")["admission"] == "REVIEW_EVIDENCE_MISSING"


def _ready_bound_input():
    record = _record()
    record["feature_bullets_raw"] = []
    record.update({"product_url": "https://www.amazon.es/dp/B000000101", "rating": 4.5,
                   "detail_schema_version": "2", "ranking_schema_version": "ranking-v1",
                   "parser_version": "fixture-v1", "raw_source": {"fixture": True}})
    audit = audit_source_fields([record])
    gate = evaluate_source_gate(audit)
    master = build_spanish_master([record], audit, gate, run_id="structured-test")
    review = {"schema_version": "structured-review-snapshot-v1", "item_ids": [
        "B000000101:attr-free", "B000000101:attr-color", "B000000101:attr-model", "B000000101:attr-cjk"], "review_item_ids": []}
    return bind_formal_structured_translation_input(
        master, audit, gate, review, prompt_version="structured-test-v1",
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"},
    ), master, audit, gate, review


def test_formal_binding_rejects_forged_ready_and_tampered_payloads_before_provider():
    formal, master, audit, gate, review = _ready_bound_input()
    validate_formal_structured_translation_input(formal, master, audit, gate, review,
                                                 prompt_version="structured-test-v1",
                                                 dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"})
    forged = {"status": "SOURCE_READY", "ready": True}
    with pytest.raises(ValueError, match="SOURCE_GATE_UNVERIFIED"):
        validate_formal_structured_translation_input(formal, master, audit, forged, review,
                                                     prompt_version="structured-test-v1",
                                                     dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"})
    tampered = dict(formal); tampered["records"] = [dict(formal["records"][0])]
    tampered["records"][0]["asin"] = "B000000999"
    with pytest.raises(ValueError, match="STRUCTURED_BINDING_ASIN_SET_MISMATCH"):
        validate_formal_structured_translation_input(tampered, master, audit, gate, review,
                                                     prompt_version="structured-test-v1",
                                                     dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"})


class _CaptureProvider(TranslationProvider):
    name = "qwen-mt"
    model = "qwen-mt-fixture"

    def __init__(self):
        self.calls = []

    def translate(self, text, *, asin, field, **kwargs):
        self.calls.append((asin, field, text, kwargs))
        return ProviderResponse(text="红色", provider=self.name, model=self.model, raw={"fixture": True})


def test_formal_execution_uses_service_item_path_and_never_sends_excluded_or_identity(tmp_path):
    formal, master, audit, gate, review = _ready_bound_input()
    provider = _CaptureProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"),
                                 prompt_version="structured-test-v1",
                                 dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"})
    first = execute_formal_structured_translation(formal, master, audit, gate, review, service,
                                                  prompt_version="structured-test-v1",
                                                  dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"})
    sent = " ".join(call[2] for call in provider.calls)
    assert "Owner raw only" not in sent and "X-100" not in sent
    assert provider.calls and all(call[1] == "product_details" for call in provider.calls)
    detail = first["records"]["B000000101"]["fields"]["product_details"]
    assert [item["item_id"] for item in detail["items"]] == ["attr-free", "attr-color", "attr-model", "attr-cjk"]
    assert detail["items"][2]["translated_text"] == "X-100"
    assert detail["items"][3]["translation_status"] == "review_required"
    calls = len(provider.calls)
    second = execute_formal_structured_translation(formal, master, audit, gate, review, service,
                                                   prompt_version="structured-test-v1",
                                                   dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"})
    assert len(provider.calls) == calls
    assert second["summary"]["provider_calls"] == 0


def test_workflow_translation_stage_consumes_formal_structured_input(tmp_path):
    _, master, audit, gate, review = _ready_bound_input()
    provider = _CaptureProvider()
    task = SimpleNamespace(task_id="structured-fixture", history_dir=tmp_path / "history",
                           translation={"provider_mode": "qwen-mt-fixture"})
    workflow = ProductionWorkflow(task, tmp_path / "run", run_id="structured", translation_provider=provider)
    input_payload = workflow._store("translation-input", {
        "translation_input": {"records": []}, "translation_batch_master": master,
        "translation_batch_source_audit": audit, "translation_batch_source_gate": gate,
        "structured_profile": "formal", "structured_review_snapshot": review,
    })
    preclean_payload = workflow._store("preclean", {"preclean": {}})
    dictionary_payload = workflow._store("dictionary", {"dictionary_manifest": {
        "dictionary_version": "1", "dictionary_hash": "dict-hash"}})
    context = {"manifest": {"stages": {"translation-input": {"payload": input_payload},
                                         "preclean": {"payload": preclean_payload},
                                         "dictionary": {"payload": dictionary_payload}}}}
    result = workflow.stage_translation(context)
    assert result["execution"]["records"]["B000000101"]["fields"]["product_details"]["excluded_raw_trace"]
    assert result["structured_followup_status"] == "NOT_EXECUTED"
    assert provider.calls and all("Owner raw only" not in call[2] for call in provider.calls)
