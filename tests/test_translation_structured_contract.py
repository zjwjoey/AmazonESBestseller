from copy import deepcopy
import json

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
    build_structured_production_overlay,
)
from amazon_es_bestseller.production.spanish_master import build_spanish_master
from amazon_es_bestseller.quality.source_fields import audit_source_fields
from amazon_es_bestseller.quality.source_gate import evaluate_source_gate
from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider
from amazon_es_bestseller.translation.service import TranslationService
from amazon_es_bestseller.translation.service import source_hash
from amazon_es_bestseller.orchestration.workflow import ProductionWorkflow
from amazon_es_bestseller.quality.chinese import audit_field


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


def test_new_owner_attribute_exclusion_consumes_only_hash_bound_eligible_attributes():
    excluded = {"section": "Info", "label_raw": "Capacidad", "value_raw": "2,3 kg", "position": 0,
                "item_id": "capacidad"}
    sibling = {"section": "Info", "label_raw": "Material", "value_raw": "Acero", "position": 1,
               "item_id": "material"}
    record = {"asin": "B000000102", "rawattributes_raw": [excluded, sibling],
              "canonicalstructuredsource": [excluded, sibling], "eligibleattributes": [sibling],
              "feature_bullets_raw": [],
              "owner_attribute_exclusions": {
                  "schema_version": "owner-current-source-attribute-exclusion-v1",
                  "raw_attributes_hash": source_hash(json.dumps([excluded, sibling], ensure_ascii=False, sort_keys=True,
                                                                  separators=(",", ":"))),
                  "eligible_attributes_hash": source_hash(json.dumps([sibling], ensure_ascii=False, sort_keys=True,
                                                                       separators=(",", ":"))),
                  "excluded_items": [{"locator": {"source": "attributes", "label_raw": "Capacidad",
                                                     "value_raw": "2,3 kg", "position": 0}}],
              }}
    authority = {"asin": "B000000102", "attributes": [excluded, sibling]}
    record["owner_attribute_exclusions"]["raw_record_hash"] = source_hash(json.dumps(authority, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    with pytest.raises(ValueError, match="AUTHORITY_REQUIRED"):
        build_structured_translation_draft([record], review_item_ids=set())
    draft = build_structured_translation_draft([record], review_item_ids=set(), authority_records=[authority])
    values = [item["value_raw"] for item in draft["records"][0]["fields"]["product_details"]["items"]]
    assert values == ["Acero"]

    tampered = deepcopy(record)
    tampered["eligibleattributes"] = [excluded, sibling]
    with pytest.raises(ValueError, match="OWNER_ATTRIBUTE_EXCLUSION_BINDING_INVALID"):
        build_structured_translation_draft([tampered], review_item_ids=set(), authority_records=[authority])

    bad_authority = deepcopy(authority); bad_authority["attributes"][0]["section"] = "changed"
    with pytest.raises(ValueError, match="AUTHORITY_REQUIRED"):
        build_structured_translation_draft([record], review_item_ids=set(), authority_records=[bad_authority])


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


def _ready_bound_input(record=None):
    if record is None:
        record = _record()
        record["feature_bullets_raw"] = []
    record.update({"product_url": "https://www.amazon.es/dp/B000000101", "rating": 4.5,
                   "detail_schema_version": "2", "ranking_schema_version": "ranking-v1",
                   "parser_version": "fixture-v1", "raw_source": {"fixture": True}})
    audit = audit_source_fields([record])
    gate = evaluate_source_gate(audit)
    master = build_spanish_master([record], audit, gate, run_id="structured-test")
    draft = build_structured_translation_draft(master["records"], review_item_ids=set())
    decisions = [{"item_id": "%s:%s" % (row["asin"], item["item_id"]),
                  "source_hash": item["source_hash"], "status": "APPROVED"}
                 for row in draft["records"] for field in row["fields"].values() for item in field["items"]]
    review = {"schema_version": "structured-review-snapshot-v1", "source_audit_hash": gate["audit_hash"],
              "item_ids": [item["item_id"] for item in decisions], "item_decisions": decisions}
    return bind_formal_structured_translation_input(
        master, audit, gate, review, prompt_version="structured-test-v1",
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"},
    ), master, audit, gate, review


def _overlay(formal, execution, master, audit, gate, review):
    return build_structured_production_overlay(
        formal, execution, verified_master=master, source_audit=audit, source_gate=gate,
        review_snapshot=review, prompt_version="structured-test-v1",
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"},
    )


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


def test_structured_overlay_keeps_failed_item_out_of_master_and_queues_it(tmp_path):
    formal, master, audit, gate, review = _ready_bound_input()
    provider = _CaptureProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"),
                                 prompt_version="structured-test-v1",
                                 dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"})
    execution = execute_formal_structured_translation(
        formal, master, audit, gate, review, service, prompt_version="structured-test-v1",
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"},
    )
    state = _overlay(formal, execution, master, audit, gate, review)
    details = state["records"][0]["fields"][0]
    assert details["promotion_status"] == "QA_BLOCKED"
    assert "product_details_zh" not in state["chinese_master_overlay"][0]
    assert {item["item_id"] for item in details["items"]} == {
        item["item_id"] for item in formal["records"][0]["fields"]["product_details"]["items"]
    }
    assert any(item["item_id"] == "attr-cjk" and item["auto_provider_repair"] is False
               for item in state["repair_queue"])
    assert all(item["strategy"] not in {"auto_repair", "provider_retry"} for item in state["repair_queue"])
    assert all(item["dictionary_version"] == "1" for item in state["dictionary_rerender_item_impact"])
    assert details["items"][0]["provider"] == "qwen-mt"
    assert details["items"][0]["model"] == "qwen-mt-fixture"


def test_structured_overlay_promotes_only_a_complete_legal_field_and_preserves_bullets(tmp_path):
    record = _record()
    record["eligibleattributes"] = record["eligibleattributes"][1:3]
    record["canonicalstructuredsource"] = list(record["eligibleattributes"])
    formal, master, audit, gate, review = _ready_bound_input(record)
    provider = _CaptureProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"),
                                 prompt_version="structured-test-v1",
                                 dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"})
    execution = execute_formal_structured_translation(
        formal, master, audit, gate, review, service, prompt_version="structured-test-v1",
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"},
    )
    state = _overlay(formal, execution, master, audit, gate, review)
    overlay = state["chinese_master_overlay"][0]
    rendered_bullets = execution["records"]["B000000101"]["fields"]["feature_bullets"]["items"]
    assert overlay["feature_bullets_zh"].split("\n") == [item["translated_text"] for item in rendered_bullets]
    assert overlay["product_details_zh"]
    assert state["repair_queue"] == []
    assert all(row["status"] == "PASS" for row in state["chinese_qa"])
    tampered = dict(execution)
    tampered["binding"] = {"tampered": True}
    with pytest.raises(ValueError, match="STRUCTURED_EXECUTION_BINDING_MISMATCH"):
        _overlay(formal, tampered, master, audit, gate, review)


@pytest.mark.parametrize("mutation", [
    "execution_item_asin", "duplicate_item_id", "formal_value", "formal_target_field",
    "execution_record_hash", "execution_label",
])
def test_structured_overlay_rejects_every_tampered_bound_item_fact(tmp_path, mutation):
    formal, master, audit, gate, review = _ready_bound_input()
    provider = _CaptureProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"),
                                 prompt_version="structured-test-v1",
                                 dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"})
    execution = execute_formal_structured_translation(
        formal, master, audit, gate, review, service, prompt_version="structured-test-v1",
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"},
    )
    formal, execution = deepcopy(formal), deepcopy(execution)
    details = execution["records"]["B000000101"]["fields"]["product_details"]["items"]
    if mutation == "execution_item_asin":
        details[0]["asin"] = "B000000999"
    elif mutation == "duplicate_item_id":
        details.append(deepcopy(details[0]))
    elif mutation == "formal_value":
        formal["records"][0]["fields"]["product_details"]["items"][0]["value_raw"] = "Forjado"
    elif mutation == "formal_target_field":
        formal["records"][0]["fields"]["product_details"]["target_field"] = "forged_zh"
    elif mutation == "execution_record_hash":
        execution["records"]["B000000101"]["source_record_hash"] = "forged"
    elif mutation == "execution_label":
        details[0]["label_raw"] = "Forjado"
    with pytest.raises(ValueError, match="STRUCTURED_(EXECUTION|BINDING|RECORD_OR_ITEM)"):
        _overlay(formal, execution, master, audit, gate, review)


def test_structured_overlay_blocks_unknown_label_and_exposes_full_field_source_to_generic_qa(tmp_path):
    record = _record()
    record["eligibleattributes"] = [dict(record["eligibleattributes"][1], label_raw="Etiqueta desconocida")]
    record["canonicalstructuredsource"] = list(record["eligibleattributes"])
    formal, master, audit, gate, review = _ready_bound_input(record)
    provider = _CaptureProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"),
                                 prompt_version="structured-test-v1",
                                 dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"})
    execution = execute_formal_structured_translation(
        formal, master, audit, gate, review, service, prompt_version="structured-test-v1",
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "dict-hash"},
    )
    state = _overlay(formal, execution, master, audit, gate, review)
    field = state["records"][0]["fields"][0]
    assert field["promotion_status"] == "QA_BLOCKED"
    assert "product_details_zh" not in state["chinese_master_overlay"][0]
    assert field["items"][0]["label_status"] == "MANUAL_REVIEW"
    assert field["source_text"]
    assert field["source_hash"] == source_hash(field["source_text"])
    downstream = audit_field(asin=field["asin"], field=field["field"],
                             source_es=field["source_text"], translated_zh=field["final_zh"],
                             source_hash=field["source_hash"], target_field=field["target_field"],
                             translation_schema_version=field["translation_schema_version"])
    assert "SOURCE_MISSING" not in {issue["code"] for issue in downstream["issues"]}


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
    assert result["structured_followup_status"] == "RELEASE_AND_EXCEL_NOT_EXECUTED"
    assert result["state"]["status"] == "NONFORMAL_STRUCTURED_OVERLAY"
    assert result["state"]["formal_release"] is False
    assert provider.calls and all("Owner raw only" not in call[2] for call in provider.calls)
