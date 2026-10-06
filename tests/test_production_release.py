from copy import deepcopy

import pytest

from amazon_es_bestseller.production.release import (
    BLOCKED, DRAFT, READY, _candidate_hash, _hash, build_stage_evidence,
    evaluate_release_gate, export_ready, seal_artifact,
)
from amazon_es_bestseller.production.spanish_master import build_spanish_master
from amazon_es_bestseller.quality.chinese_gate import evaluate_chinese_gate
from amazon_es_bestseller.quality.source_fields import audit_source_fields
from amazon_es_bestseller.quality.source_gate import evaluate_source_gate
from amazon_es_bestseller.translation.production_contract import canonical_record, source_text
from amazon_es_bestseller.translation.service import source_hash

ASINS = ("B012345678", "B012345679")


def _source(asin, note):
    return {"asin": asin, "requested_asin": asin, "ranking_asin": asin, "detail_asin": asin, "final_asin": asin,
            "product_url": "https://www.amazon.es/dp/%s" % asin, "image_url": "https://images.amazon.es/%s.jpg" % asin,
            "current_price": "12,00", "rating": "4,5", "title_es_raw": "Botella de acero 500 ml", "notes": note,
            "detail_schema_version": "detail-schema-v1", "ranking_schema_version": "ranking-schema-v1", "parser_version": "detail-parser-v1",
            "ranking_contexts": [{"ranking_source_url": "https://www.amazon.es/Best-Sellers/zgbs/123", "ranking_page_number": 1,
                                  "bestseller_rank": 1 if asin == ASINS[0] else 2, "leaf_category": "Cocina", "browse_node_id": "123"}]}


def _report(master, check):
    evidence = build_stage_evidence(master, check)
    return {"check": check, "status": "PASS", "produced_stage": check, "report_schema_version": "production-stage-evidence-v1",
            "evidence_ref": evidence, "summary": {"records_checked": evidence["record_count"]}, "issues": [],
            "records": [{"asin": asin, "record_hash": value, "status": "PASS"} for asin, value in evidence["record_hashes"].items()]}


def valid_artifacts():
    source = [_source(ASINS[0], "human A"), _source(ASINS[1], "human B")]
    audit = audit_source_fields(source)
    master = build_spanish_master(source, audit, evaluate_source_gate(audit), run_id="r1")
    rows, chinese = deepcopy(master["records"]), deepcopy(master["records"])
    for row in chinese:
        row["title_zh"] = "Chinese bottle"
    translation_records, candidate_records, execution = [], [], []
    for row in rows:
        title_source = source_text(canonical_record(row)["title_es_raw"])
        field = {"asin": row["asin"], "field": "title_es_raw", "target_field": "title_zh",
                 "field_type": "title_zh", "promotion_status": "PROMOTED", "source_text": title_source,
                 "source_hash": source_hash(title_source), "target_value": "Chinese bottle",
                 "context": {"field": "title_es_raw", "target_field": "title_zh"},
                 "dictionary_version": "3", "translation_schema_version": "translation-v2.25"}
        field["candidate_hash"] = _candidate_hash(field)
        # Legacy state fields are execution provenance only.  Keep their
        # values distinct from the formal candidate surface to prove release
        # never uses them as a fallback.
        translation_records.append({"asin": row["asin"], "release_status": "READY", "fields": [dict(field)]})
        candidate_records.append({"asin": row["asin"], "release_status": "READY",
                                  "field_candidates": [dict(field)]})
        execution.append({"asin": row["asin"], "fields": {
            "title_zh": {"dictionary_version": "2", "source_hash": field["source_hash"]}}})
    translation = {"state": {"input_manifest": {"dataset_hash": "input-hash", "record_count": 2},
                               "release_candidate": {"release_status": "READY", "records": candidate_records},
                               "records": translation_records}, "execution": {"records": execution},
                   "provider_provenance": {"provider": "qwen-mt", "model": "qwen-mt-flash", "request_count": 2, "verified": True}}
    dictionary = {"completed": True, "manifest": {"dictionary_version": 3, "dictionary_hash": "dict-hash", "translation_schema_version": "translation-v2.25", "promoted_dictionary": {}},
                  "rerender": {"dictionary_version": "3", "ready": True, "selective_repair": []}}
    qa = {"fields": [dict(field, status="PASS") for record in candidate_records for field in record["field_candidates"]]}
    return {"spanish_master": master,
            "ranking_authority": seal_artifact("ranking_authority", _report(master, "ranking_authority")),
            "source_audit": seal_artifact("source_audit", audit), "source_gate": seal_artifact("source_gate", evaluate_source_gate(audit)),
            "detail_identity": seal_artifact("detail_identity", _report(master, "detail_identity")),
            "offline_replay": seal_artifact("offline_replay", _report(master, "offline_replay")),
            "field_closure": seal_artifact("field_closure", {"check": "field_closure", "status": "PASS", "summary": {"total_skus": 2}, "records": []}),
            "translation": seal_artifact("translation", translation), "dictionary_sync": seal_artifact("dictionary_sync", dictionary),
            "chinese_qa": seal_artifact("chinese_qa", qa),
            "chinese_gate": seal_artifact("chinese_gate", evaluate_chinese_gate(qa["fields"])),
            "spanish_output": seal_artifact("spanish_output", {"records": rows}), "chinese_output": seal_artifact("chinese_output", {"records": chinese})}


def codes(result):
    return {item["code"] for item in result["findings"]}


def test_complete_bound_evidence_is_ready_and_export_rechecks_gate(tmp_path):
    artifacts = valid_artifacts()
    assert evaluate_release_gate(artifacts)["status"] == READY
    output = export_ready(artifacts, None, str(tmp_path / "out.xlsx"))
    assert output["decision"]["status"] == READY and (tmp_path / "out.xlsx").exists()


def test_self_sealed_empty_or_asin_only_pass_reports_do_not_become_authority():
    artifacts = valid_artifacts()
    artifacts["ranking_authority"] = seal_artifact("ranking_authority", {"check": "ranking_authority", "status": "PASS", "summary": {"records_checked": 2}, "issues": []})
    assert "REPORT_PROVENANCE_MISSING" in codes(evaluate_release_gate(artifacts))
    artifacts = valid_artifacts(); forged = _report(artifacts["spanish_master"], "ranking_authority")
    forged["records"] = [{"asin": asin, "record_hash": "fake", "status": "PASS"} for asin in ASINS]
    artifacts["ranking_authority"] = seal_artifact("ranking_authority", forged)
    assert "REPORT_RECORD_BINDING_MISMATCH" in codes(evaluate_release_gate(artifacts))


def test_master_raw_source_audit_and_stage_scope_are_replayed():
    artifacts = valid_artifacts(); artifacts["spanish_master"]["records"][0]["raw_source"]["title_es_raw"] = "replaced"
    assert "MASTER_ARTIFACT_HASH_INVALID" in codes(evaluate_release_gate(artifacts))
    artifacts = valid_artifacts(); master = artifacts["spanish_master"]
    master["records"][0]["raw_source"]["title_es_raw"] = "replaced"
    master["artifact_hash"] = _hash({key: master.get(key) for key in ("master_schema_version", "source_audit_hash",
        "source_record_bindings_hash", "source_gate_status", "run_id", "records")})
    assert "SOURCE_GATE_NOT_BOUND_READY" in codes(evaluate_release_gate(artifacts))
    artifacts = valid_artifacts(); report = artifacts["offline_replay"]["payload"]; report["summary"]["records_checked"] = 1
    artifacts["offline_replay"] = seal_artifact("offline_replay", report)
    assert "EVIDENCE_SCOPE_MISMATCH" in codes(evaluate_release_gate(artifacts))
    artifacts = valid_artifacts(); report = artifacts["detail_identity"]["payload"]
    report["evidence_ref"]["schema_versions"][ASINS[0]]["parser_version"] = "other"
    artifacts["detail_identity"] = seal_artifact("detail_identity", report)
    assert "REPORT_EVIDENCE_REF_MISMATCH" in codes(evaluate_release_gate(artifacts))


def test_chinese_qa_requires_complete_bound_candidate_source_target_and_field():
    artifacts = valid_artifacts(); qa = artifacts["chinese_qa"]["payload"]; qa["fields"] = qa["fields"][:1]
    artifacts["chinese_qa"] = seal_artifact("chinese_qa", qa)
    assert "CHINESE_QA_FIELD_COVERAGE_INCOMPLETE" in codes(evaluate_release_gate(artifacts))
    artifacts = valid_artifacts(); qa = artifacts["chinese_qa"]["payload"]; qa["fields"][0]["target_value"] = "forged"
    artifacts["chinese_qa"] = seal_artifact("chinese_qa", qa)
    assert "CHINESE_QA_FIELD_BINDING_INVALID" in codes(evaluate_release_gate(artifacts))
    artifacts = valid_artifacts(); tr = artifacts["translation"]["payload"]
    tr["state"]["records"][0]["fields"][0]["source_hash"] = "replaced"
    artifacts["translation"] = seal_artifact("translation", tr)
    assert evaluate_release_gate(artifacts)["status"] == READY
    artifacts = valid_artifacts(); field = artifacts["translation"]["payload"]["state"]["release_candidate"]["records"][0]["field_candidates"][0]
    field["target_value"] = "forged candidate"; field["candidate_hash"] = _candidate_hash(field)
    artifacts["translation"] = seal_artifact("translation", artifacts["translation"]["payload"])
    assert "TRANSLATION_CANDIDATE_BINDING_INVALID" in codes(evaluate_release_gate(artifacts))


def test_hash_alignment_provider_and_nonformal_controls_block(tmp_path):
    artifacts = valid_artifacts(); artifacts["ranking_authority"]["payload"]["status"] = "FORGED"
    assert "ARTIFACT_HASH_INVALID" in codes(evaluate_release_gate(artifacts))
    artifacts = valid_artifacts(); output = artifacts["chinese_output"]["payload"]; output["records"][0]["notes"] = "changed"
    artifacts["chinese_output"] = seal_artifact("chinese_output", output)
    assert "HUMAN_NOTES_CHANGED" in codes(evaluate_release_gate(artifacts))
    artifacts = valid_artifacts(); translation = artifacts["translation"]["payload"]; translation["provider_provenance"]["provider"] = "fake"
    artifacts["translation"] = seal_artifact("translation", translation)
    assert "REAL_PROVIDER_PROVENANCE_MISSING" in codes(evaluate_release_gate(artifacts))
    assert evaluate_release_gate({}, formal=False)["status"] == DRAFT
    with pytest.raises(ValueError, match="PRODUCTION_RELEASE_NOT_READY"):
        export_ready(valid_artifacts(), None, str(tmp_path / "out.xlsx"), debug=True)


def test_field_closure_p1_blocks_even_when_all_other_bound_stages_pass():
    artifacts = valid_artifacts()
    artifacts["field_closure"] = seal_artifact("field_closure", {"check": "field_closure", "status": "PASS",
        "summary": {"total_skus": 2}, "records": [{"asin": ASINS[0], "severity": "P1"}]})
    assert "FIELD_CLOSURE_BLOCKED" in codes(evaluate_release_gate(artifacts))


def test_bilingual_missing_extra_asin_and_url_image_mutations_block():
    artifacts = valid_artifacts()
    chinese = artifacts["chinese_output"]["payload"]
    chinese["records"] = chinese["records"][:1]
    artifacts["chinese_output"] = seal_artifact("chinese_output", chinese)
    assert "ASIN_SET_OR_ORDER_MISMATCH" in codes(evaluate_release_gate(artifacts))

    artifacts = valid_artifacts()
    chinese = artifacts["chinese_output"]["payload"]
    chinese["records"].append({"asin": "B099999999", "product_url": "https://www.amazon.es/dp/B099999999",
                               "image_url": "https://images.amazon.es/extra.jpg"})
    artifacts["chinese_output"] = seal_artifact("chinese_output", chinese)
    assert "ASIN_SET_OR_ORDER_MISMATCH" in codes(evaluate_release_gate(artifacts))

    artifacts = valid_artifacts()
    spanish = artifacts["spanish_output"]["payload"]
    spanish["records"][0]["product_url"] = "https://www.amazon.es/dp/B099999999"
    spanish["records"][0]["image_url"] = "https://images.amazon.es/wrong.jpg"
    artifacts["spanish_output"] = seal_artifact("spanish_output", spanish)
    assert "URL_OR_IMAGE_MISMATCH" in codes(evaluate_release_gate(artifacts))


def test_dictionary_rerender_version_drift_and_pending_repair_block():
    artifacts = valid_artifacts()
    dictionary = artifacts["dictionary_sync"]["payload"]
    dictionary["rerender"] = {"dictionary_version": "2", "ready": False,
                              "selective_repair": [{"asin": ASINS[0], "field_type": "title_zh"}]}
    artifacts["dictionary_sync"] = seal_artifact("dictionary_sync", dictionary)
    assert {"RERENDER_VERSION_DRIFT", "RERENDER_QA_INCOMPLETE"} <= codes(evaluate_release_gate(artifacts))


def test_final_dictionary_version_does_not_rewrite_historical_execution_provenance():
    artifacts = valid_artifacts()
    translation = artifacts["translation"]["payload"]
    raw_versions = {field["dictionary_version"] for record in translation["execution"]["records"]
                    for field in record["fields"].values()}
    candidate_versions = {field["dictionary_version"]
                          for record in translation["state"]["release_candidate"]["records"]
                          for field in record["field_candidates"]}
    assert raw_versions == {"2"}
    assert candidate_versions == {"3"}
    assert evaluate_release_gate(artifacts)["status"] == READY


def test_force_true_is_never_a_formal_release_bypass(tmp_path):
    result = evaluate_release_gate(valid_artifacts(), force=True)
    assert result["status"] == BLOCKED and "FORCE_BYPASS_FORBIDDEN" in codes(result)
    with pytest.raises(ValueError, match="PRODUCTION_RELEASE_NOT_READY"):
        export_ready(valid_artifacts(), None, str(tmp_path / "out.xlsx"), force=True)


@pytest.mark.parametrize("stage", ["detail_identity", "offline_replay"])
def test_detail_identity_and_replay_each_reject_a_sealed_nonpass_report(stage):
    artifacts = valid_artifacts()
    report = _report(artifacts["spanish_master"], stage)
    report["status"] = "BLOCK"
    artifacts[stage] = seal_artifact(stage, report)
    assert "REPORT_NOT_PASS" in codes(evaluate_release_gate(artifacts))
