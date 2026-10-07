import pytest

from amazon_es_bestseller.translation.structured_contract import (
    STRUCTURED_TRANSLATION_INPUT_SCHEMA_VERSION,
    build_structured_translation_draft,
    formalize_structured_translation_input,
    dispatch_structured_translation_tasks,
)


def _record():
    return {
        "asin": "B000000101",
        # Display text is intentionally contradictory and must never be parsed.
        "product_details_es": "Color: DISPLAY SHOULD NOT WIN",
        "eligibleattributes": [
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


def test_structured_details_do_not_fallback_to_display_or_send_owner_excluded_raw():
    draft = build_structured_translation_draft([_record()], review_item_ids=set())
    details = draft["records"][0]["fields"]["product_details"]
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
    with pytest.raises(ValueError, match="SOURCE_GATE_NOT_READY"):
        dispatch_structured_translation_tasks(
            draft, {"status": "BLOCKED", "ready": False}, lambda task: calls.append(task),
        )
    assert calls == []


def test_missing_review_evidence_keeps_ordinary_items_out_of_tasks():
    draft = build_structured_translation_draft([_record()])
    details = draft["records"][0]["fields"]["product_details"]
    assert details["translation_tasks"] == []
    assert next(item for item in details["items"] if item["item_id"] == "attr-color")["admission"] == "REVIEW_EVIDENCE_MISSING"
