from amazon_es_bestseller.translation.dictionary_service import DictionaryService
from amazon_es_bestseller.translation.dictionary_sync import dictionary_manifest, normalize_context, sync_evidence


def _rows(target="滤芯", category="cafetera"):
    context = {"category": category, "attribute_label": "filtro"}
    rows = []
    for index, asin in enumerate(("B000000001", "B000000002"), 1):
        rows.append({"evidence_id": "%s-e%s" % (category, index), "asin": asin,
                     "source_record_hash": "record-%s" % index, "source_hash": "source-filtro",
                     "source": "filtro", "target": target, "context": context,
                     "field_type": "attribute", "affected_field": "product_details",
                     "target_field": "product_details_zh"})
    return rows


def _qa(rows, version=0, schema="v1"):
    context_key, _ = normalize_context(rows[0]["context"])
    return {row["evidence_id"]: {"qa_status": "PASS", "source_hash": row["source_hash"],
                                  "target": row["target"], "field_type": row["field_type"],
                                  "context_key": context_key, "dictionary_version": str(version),
                                  "schema_version": schema} for row in rows}


def test_sync_requires_two_independent_asin_and_record_hashes():
    rows = _rows()
    rows[1].update(asin=rows[0]["asin"], source_record_hash=rows[0]["source_record_hash"])
    result = sync_evidence(rows, qa_results=_qa(rows), source_run_id="run-1", translation_schema_version="v1")
    assert result["counts"]["promoted"] == 0
    assert result["candidates"][0]["reason"] == "INSUFFICIENT_INDEPENDENT_EVIDENCE"


def test_sync_rejects_old_or_unbound_pass_and_unknown_context():
    rows = _rows()
    old = _qa(rows); old["cafetera-e2"]["schema_version"] = "old"
    result = sync_evidence(rows, qa_results=old, source_run_id="run-1", translation_schema_version="v1")
    assert result["candidates"][0]["reason"] == "CHINESE_QA_NOT_EXACT_BOUND_PASS"
    rows[0]["context"] = "coffee"
    unknown = sync_evidence(rows, qa_results=_qa(_rows()), source_run_id="run-1", translation_schema_version="v1")
    assert unknown["candidates"][0]["reason"] == "MISSING_OR_UNKNOWN_CONTEXT"


def test_contextual_promotions_remain_separate_and_report_ambiguity():
    coffee = _rows(target="滤芯", category="cafetera")
    pool = _rows(target="过滤器", category="piscina")
    rows = coffee + pool
    result = sync_evidence(rows, qa_results=_qa(coffee) | _qa(pool), source_run_id="run-1", translation_schema_version="v1")
    assert result["counts"]["promoted"] == 2
    assert len(result["cross_context_ambiguities"]) == 1
    service = DictionaryService(manifest=result["manifest"])
    context, _ = normalize_context(coffee[0]["context"])
    assert service.lookup_promoted("filtro", field_type="attribute", context_key=context) == "滤芯"
    assert service.lookup_promoted("filtro", field_type="attribute", context_key="") is None


def test_manifest_hash_version_and_change_log_are_stable_without_change():
    rows = _rows()
    first = sync_evidence(rows, qa_results=_qa(rows), source_run_id="run-1", translation_schema_version="v1")
    second = sync_evidence(rows, qa_results=_qa(rows, version=first["dictionary_version"]),
                           source_run_id="run-1", translation_schema_version="v1", previous=first)
    assert first["dictionary_hash"] == first["manifest"]["dictionary_hash"]
    assert dictionary_manifest(sync_result=first, source_run_id="run-1", translation_schema_version="v1") == first["manifest"]
    assert second["dictionary_version"] == first["dictionary_version"]
    assert second["change_log"] == []
    assert second["dictionary_hash"] == first["dictionary_hash"]
