from copy import deepcopy

import pytest

from amazon_es_bestseller.production.release import (
    BLOCKED, DRAFT, READY, evaluate_release_gate, export_ready, seal_artifact,
)
from amazon_es_bestseller.production.spanish_master import build_spanish_master
from amazon_es_bestseller.quality.source_fields import audit_source_fields
from amazon_es_bestseller.quality.source_gate import evaluate_source_gate


ASINS = ("B012345678", "B012345679")


def _source(asin, note):
    rank = 1 if asin == ASINS[0] else 2
    return {
        "asin": asin, "requested_asin": asin, "ranking_asin": asin, "detail_asin": asin,
        "final_asin": asin, "product_url": "https://www.amazon.es/dp/%s" % asin,
        "image_url": "https://images.amazon.es/%s.jpg" % asin, "current_price": "12,00",
        "rating": "4,5", "title_es_raw": "Botella de acero 500 ml", "notes": note,
        "ranking_contexts": [{"ranking_source_url": "https://www.amazon.es/Best-Sellers/zgbs/123",
                              "ranking_page_number": 1, "bestseller_rank": rank,
                              "leaf_category": "Cocina", "browse_node_id": "123"}],
    }


def _report(check, count=2):
    return {"check": check, "status": "PASS", "summary": {"records_checked": count}, "issues": []}


def valid_artifacts():
    source = [_source(ASINS[0], "human A"), _source(ASINS[1], "human B")]
    audit = audit_source_fields(source)
    master = build_spanish_master(source, audit, evaluate_source_gate(audit), run_id="r1")
    rows = deepcopy(master["records"])
    chinese = deepcopy(rows)
    for row in chinese:
        row["title_zh"] = "保温水瓶"
    translation_records = []
    execution = []
    for row in rows:
        translation_records.append({"asin": row["asin"], "release_status": "READY",
                                    "fields": [{"promotion_status": "PROMOTED", "source_hash": "h-" + row["asin"]}]})
        execution.append({"asin": row["asin"], "fields": {"title_zh": {"dictionary_version": "3"}}})
    translation = {"state": {"input_manifest": {"dataset_hash": "input-hash", "record_count": 2},
                               "release_candidate": {"release_status": "READY"}, "records": translation_records},
                   "execution": {"records": execution},
                   "provider_provenance": {"provider": "qwen-mt", "model": "qwen-mt-flash",
                                           "request_count": 2, "verified": True}}
    dictionary = {"completed": True, "manifest": {"dictionary_version": 3, "dictionary_hash": "dict-hash",
                 "translation_schema_version": "translation-v2.25", "promoted_dictionary": {}},
                 "rerender": {"dictionary_version": "3", "ready": True, "selective_repair": []}}
    return {
        "spanish_master": master,
        "ranking_authority": seal_artifact("ranking_authority", _report("ranking_authority")),
        "source_audit": seal_artifact("source_audit", audit),
        "source_gate": seal_artifact("source_gate", evaluate_source_gate(audit)),
        "detail_identity": seal_artifact("detail_identity", _report("detail_identity")),
        "offline_replay": seal_artifact("offline_replay", _report("offline_replay")),
        "field_closure": seal_artifact("field_closure", {"check": "field_closure", "status": "PASS",
                                                            "summary": {"total_skus": 2}, "records": []}),
        "translation": seal_artifact("translation", translation),
        "dictionary_sync": seal_artifact("dictionary_sync", dictionary),
        "chinese_qa": seal_artifact("chinese_qa", {"fields": [{"asin": asin, "status": "PASS"} for asin in ASINS]}),
        "chinese_gate": seal_artifact("chinese_gate", {"status": "SKU_ZH_READY"}),
        "spanish_output": seal_artifact("spanish_output", {"records": rows}),
        "chinese_output": seal_artifact("chinese_output", {"records": chinese}),
    }


def codes(result):
    return {item["code"] for item in result["findings"]}


def test_complete_synthetic_evidence_is_ready_and_export_rechecks_gate():
    artifacts = valid_artifacts()
    decision = evaluate_release_gate(artifacts)
    assert decision["status"] == READY
    received = {}
    def exporter(records, *, output_path):
        received["records"] = records; received["path"] = output_path
        return "written"
    output = export_ready(artifacts, exporter, "out.xlsx")
    assert output["export_result"] == "written"
    assert received["records"][0]["notes"] == "human A"
    assert received["records"][0]["title_zh"] == "保温水瓶"


def test_tampered_or_missing_artifacts_and_p1_closure_block_formal_release():
    artifacts = valid_artifacts()
    artifacts["ranking_authority"]["payload"]["status"] = "FORGED"  # hash no longer matches
    decision = evaluate_release_gate(artifacts)
    assert decision["status"] == BLOCKED
    assert "ARTIFACT_HASH_INVALID" in codes(decision)
    artifacts = valid_artifacts()
    artifacts.pop("offline_replay")
    artifacts["field_closure"] = seal_artifact("field_closure", {"check": "field_closure", "status": "PASS",
        "summary": {"total_skus": 2}, "records": [{"severity": "P1"}]})
    decision = evaluate_release_gate(artifacts)
    assert {"MISSING_ARTIFACT", "FIELD_CLOSURE_BLOCKED"} <= codes(decision)


def test_alignment_rejects_missing_extra_asin_url_image_and_human_note_changes():
    artifacts = valid_artifacts()
    payload = artifacts["chinese_output"]["payload"]
    payload["records"] = payload["records"][:1]
    artifacts["chinese_output"] = seal_artifact("chinese_output", payload)
    decision = evaluate_release_gate(artifacts)
    assert "ASIN_SET_OR_ORDER_MISMATCH" in codes(decision)
    artifacts = valid_artifacts()
    payload = artifacts["chinese_output"]["payload"]
    payload["records"].append({"asin": "B099999999", "product_url": "https://www.amazon.es/dp/B099999999",
                               "image_url": "https://images.amazon.es/extra.jpg"})
    artifacts["chinese_output"] = seal_artifact("chinese_output", payload)
    assert "ASIN_SET_OR_ORDER_MISMATCH" in codes(evaluate_release_gate(artifacts))
    artifacts = valid_artifacts()
    payload = artifacts["spanish_output"]["payload"]
    payload["records"][0]["image_url"] = "https://images.amazon.es/wrong.jpg"
    payload["records"][0]["notes"] = "changed"
    artifacts["spanish_output"] = seal_artifact("spanish_output", payload)
    decision = evaluate_release_gate(artifacts)
    assert {"URL_OR_IMAGE_MISMATCH", "HUMAN_NOTES_CHANGED"} <= codes(decision)


def test_translation_requires_real_provenance_and_dictionary_rerender_version_qa():
    artifacts = valid_artifacts()
    payload = artifacts["translation"]["payload"]
    payload["provider_provenance"]["provider"] = "fake"
    artifacts["translation"] = seal_artifact("translation", payload)
    payload = artifacts["dictionary_sync"]["payload"]
    payload["rerender"] = {"dictionary_version": "2", "ready": False, "selective_repair": [{"asin": ASINS[0]}]}
    artifacts["dictionary_sync"] = seal_artifact("dictionary_sync", payload)
    decision = evaluate_release_gate(artifacts)
    assert {"REAL_PROVIDER_PROVENANCE_MISSING", "RERENDER_VERSION_DRIFT", "RERENDER_QA_INCOMPLETE"} <= codes(decision)


def test_nonformal_is_draft_and_debug_force_cannot_be_formal_bypass():
    assert evaluate_release_gate({}, formal=False)["status"] == DRAFT
    result = evaluate_release_gate(valid_artifacts(), debug=True, force=True)
    assert {"DEBUG_FORMAL_FORBIDDEN", "FORCE_BYPASS_FORBIDDEN"} <= codes(result)
    with pytest.raises(ValueError, match="PRODUCTION_RELEASE_NOT_READY"):
        export_ready(valid_artifacts(), lambda *a, **k: None, "out.xlsx", debug=True)


@pytest.mark.parametrize("stage,check", [
    ("ranking_authority", "ranking_authority"), ("detail_identity", "detail_identity"),
    ("offline_replay", "offline_replay"),
])
def test_each_authority_report_must_be_bound_pass(stage, check):
    artifacts = valid_artifacts()
    artifacts[stage] = seal_artifact(stage, {"check": check, "status": "BLOCK", "summary": {"records_checked": 2}, "issues": []})
    assert "REPORT_NOT_PASS" in codes(evaluate_release_gate(artifacts))


def test_source_gate_and_empty_output_cannot_be_forged_ready():
    artifacts = valid_artifacts()
    artifacts["source_gate"] = seal_artifact("source_gate", {"status": "SOURCE_READY", "ready": True, "audit_hash": "forged"})
    artifacts["spanish_output"] = seal_artifact("spanish_output", {"records": []})
    found = codes(evaluate_release_gate(artifacts))
    assert {"SOURCE_GATE_NOT_BOUND_READY", "EMPTY_ASIN_SET", "ASIN_SET_OR_ORDER_MISMATCH"} <= found
