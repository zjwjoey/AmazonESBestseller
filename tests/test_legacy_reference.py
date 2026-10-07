from amazon_es_bestseller.translation.legacy_reference import (
    LEGACY_REFERENCE_CANDIDATES_SCHEMA_VERSION,
    build_legacy_reference_candidates,
    evaluate_legacy_reviewed_formal_gate,
)


def _record(**overrides):
    record = {
        "asin": "B000000101",
        "title_es_raw": "Bolsa roja",
        "brand": "Marca sin traducir",
        "category_l1": "Hogar",
        "selected_variation_raw": "Color: Rojo",
        "specification_es": "500 ml",
        "product_description": "Bolsa reutilizable para casa",
        "product_details": "Color: Rojo",
        "feature_bullets": ["Ligera"],
    }
    record.update(overrides)
    return record


def _legacy_reference(**overrides):
    reference = {
        "B000000101": {
            "spanish": {
                "title_es_raw": "Bolsa roja",
                "category_l1": "Hogar",
                "selected_variation_raw": "Color: Rojo",
                "specification_es": "500 ml",
                "product_description": "Bolsa reutilizable para casa",
                "product_details": "Color: Rojo",
                "feature_bullets": "Ligera",
            },
            "chinese": {
                "title_es_raw": "红色袋子",
                "category_l1": "家居",
                "selected_variation_raw": "颜色：红色",
                "specification_es": "500毫升",
                "product_description": "家用可重复使用袋",
                "product_details": "颜色：红色",
                "feature_bullets": "轻便",
            },
            "provenance": {
                "source_kind": "legacy_excel_reference",
                "provider": "unknown",
                "spanish": {"file_hash": "es-file", "sheet": "ES", "row": 8},
                "chinese": {"file_hash": "zh-file", "sheet": "ZH", "row": 9},
            },
        }
    }
    reference["B000000101"].update(overrides)
    return reference


def test_matching_scalar_reference_is_diagnostic_only_and_retains_qa():
    draft = build_legacy_reference_candidates(
        [_record()], _legacy_reference(), {"status": "BLOCKED"},
    )
    assert draft["schema_version"] == LEGACY_REFERENCE_CANDIDATES_SCHEMA_VERSION
    title = next(item for item in draft["candidates"] if item["field"] == "title_es_raw")
    assert title["legacy_es"] == "Bolsa roja"
    assert title["legacy_zh"] == "红色袋子"
    assert title["source_kind"] == "legacy_excel_reference"
    assert title["provider"] == "unknown"
    assert title["qa"]["qa_status"] in {"pass", "qa_failed"}
    assert draft["qa_report"]["counts"]["candidates"] == len(draft["candidates"])
    assert draft["qa_report"]["diagnostic_only"] is True
    assert title["release_admission"] == "DIAGNOSTIC_ONLY_SOURCE_GATE_BLOCKED"
    assert title["candidate_status"] in {"PASS", "QA_FAILED"}


def test_source_hash_mismatch_does_not_reuse_same_asin_legacy_text():
    draft = build_legacy_reference_candidates(
        [_record(title_es_raw="Bolsa azul")], _legacy_reference(), {"status": "BLOCKED"},
    )
    assert all(item["field"] != "title_es_raw" for item in draft["candidates"])
    assert draft["summary"]["source_hash_mismatch"] == 1


def test_details_bullets_and_brand_are_not_legacy_reuse_candidates():
    draft = build_legacy_reference_candidates(
        [_record()], _legacy_reference(), {"status": "BLOCKED"},
    )
    fields = {item["field"] for item in draft["candidates"]}
    assert "product_details" not in fields
    assert "feature_bullets" not in fields
    assert "brand" not in fields
    assert draft["preserved_source_fields"] == [{
        "asin": "B000000101", "field": "brand", "value": "Marca sin traducir",
        "reason": "BRAND_PRESERVED_SOURCE_NO_LEGACY_REPLACEMENT",
    }]


def test_formal_gate_requires_final_source_ready_reverification_and_explicit_review():
    draft = build_legacy_reference_candidates(
        [_record()], _legacy_reference(), {"status": "BLOCKED"},
    )
    blocked = evaluate_legacy_reviewed_formal_gate(
        draft, {"status": "BLOCKED", "canonical_hash": draft["source_binding"]["current_canonical_hash"]},
        {"policy_version": "legacy-reviewed-reference-v1", "reviewed_candidate_hashes": []},
    )
    assert blocked["status"] == "SOURCE_GATE_NOT_READY"
    ready_but_changed = evaluate_legacy_reviewed_formal_gate(
        draft, {"status": "SOURCE_READY", "canonical_hash": "different"},
        {"policy_version": "legacy-reviewed-reference-v1", "reviewed_candidate_hashes": []},
    )
    assert ready_but_changed["status"] == "SOURCE_BINDING_REVERIFY_REQUIRED"
