from amazon_es_bestseller.translation.dictionary_sync import dictionary_manifest, sync_evidence
from amazon_es_bestseller.translation.dictionary_service import DictionaryService


def _rows(target="过滤器", context="coffee", field_type="attribute"):
    return [{"evidence_id": "e1", "source": "filtro", "target": target, "context_key": context,
             "field_type": field_type, "affected_field": "product_details"},
            {"evidence_id": "e2", "source": "Filtro", "target": target, "context_key": context,
             "field_type": field_type, "affected_field": "product_details"}]


def test_sync_promotes_only_independent_explicit_qa_pass_evidence():
    result = sync_evidence(_rows(), qa_results={"e1": {"qa_status": "PASS"}, "e2": {"qa_status": "PASS"}},
                           source_run_id="run-1", translation_schema_version="v1")
    assert result["counts"] == {"promoted": 1, "candidates": 0, "conflicts": 0}
    assert result["impacted_fields"] == ["product_details"]
    assert result["selective_repair_required"] is True


def test_sync_keeps_ambiguous_context_and_missing_qa_as_candidates():
    rows = _rows() + _rows(target="滤芯", context="water")
    result = sync_evidence(rows, qa_results={"e1": {"qa_status": "PASS"}, "e2": {"qa_status": "PASS"}},
                           source_run_id="run-1", translation_schema_version="v1")
    assert result["counts"]["promoted"] == 2
    # Same normalized word remains scoped by context, never a global overwrite.
    assert len(result["promoted_map"]) == 2
    missing = sync_evidence(_rows(), qa_results={}, source_run_id="run-1", translation_schema_version="v1")
    assert missing["candidates"][0]["reason"] == "CHINESE_QA_NOT_PASS"


def test_sync_conflicts_and_versions_are_stable_without_change():
    conflict = _rows() + [{"evidence_id": "e3", "source": "filtro", "target": "滤芯", "context_key": "coffee",
                            "field_type": "attribute"}]
    result = sync_evidence(conflict, qa_results={key: {"qa_status": "PASS"} for key in ("e1", "e2", "e3")},
                           source_run_id="run-1", translation_schema_version="v1")
    assert result["counts"]["conflicts"] == 1
    first = sync_evidence(_rows(), qa_results={"e1": {"qa_status": "PASS"}, "e2": {"qa_status": "PASS"}},
                          source_run_id="run-1", translation_schema_version="v1")
    second = sync_evidence(_rows(), qa_results={"e1": {"qa_status": "PASS"}, "e2": {"qa_status": "PASS"}},
                           source_run_id="run-1", translation_schema_version="v1", previous=first)
    assert second["dictionary_version"] == first["dictionary_version"]
    assert second["changelog"] == []
    manifest = dictionary_manifest(DictionaryService(), source_run_id="run-1", translation_schema_version="v1")
    assert manifest["dictionary_hash"] == dictionary_manifest(DictionaryService(), source_run_id="run-1", translation_schema_version="v1")["dictionary_hash"]
