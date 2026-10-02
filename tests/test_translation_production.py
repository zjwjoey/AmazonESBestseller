import time
import json

import pytest

from amazon_es_bestseller.translation.preclean import audit_records
from amazon_es_bestseller.translation.production import (
    build_production_input, build_production_state, records_for_preclean,
)
from amazon_es_bestseller.cli import main


def _records(count=5):
    return [
        {"asin": f"B{index:09d}", "title_es_raw": "Bolsa térmica 500 ml" if index == 0 else ("" if index == 1 else f"Producto {index}"),
         "brand": "Marca", "category_l1": "Hogar y cocina",
         "specification_es": "Capacidad: 500 ml" if index == 0 else "",
         "product_details": "Color: Rojo\nVoltaje: 9 V" if index == 2 else "",
         "feature_bullets": ["Sin alcohol: No"] if index == 3 else []}
        for index in range(count)
    ]


def test_production_input_is_hash_bound_and_duplicate_safe():
    result = build_production_input(_records(5), source_run_id="collect-1", run_id="run-1")
    assert result["manifest"]["record_count"] == 5
    assert result["manifest"]["unique_asin_count"] == 5
    assert all(row["source_record_hash"] for row in result["records"])
    assert all(row["fields"]["title_es_raw"]["source_hash"] for row in result["records"])
    with pytest.raises(ValueError, match="DUPLICATE_ASIN"):
        build_production_input(_records(1) + _records(1))


def test_production_state_preserves_source_and_blocks_non_promoted_results():
    production_input = build_production_input(_records(5), run_id="run-1")
    clean = audit_records(records_for_preclean(production_input))
    translations = {
        "B000000000": {"asin": "B000000000", "fields": {
            "title_zh": {"translated_text": "保温袋 500 ml", "candidate_text": "保温袋 500 ml",
                          "translation_status": "success", "qa_status": "pass", "qa_issues": []},
        }},
        "B000000002": {"asin": "B000000002", "fields": {
            "title_zh": {"translated_text": "商品 2", "candidate_text": "商品 2",
                          "translation_status": "qa_failed", "qa_status": "qa_failed",
                          "qa_issues": [{"code": "NUMERIC_MISMATCH"}]},
        }},
        "B000000003": {"asin": "B000000003", "fields": {
            "title_zh": {"translated_text": "商品 3", "candidate_text": "商品 3",
                          "translation_status": "failed", "qa_status": "pending",
                          "last_error": "HTTP 400 data_inspection_failed", "qa_issues": []},
        }},
    }
    before = [row["source_record_hash"] for row in production_input["records"]]
    state = build_production_state(production_input, clean["translation_input_records"], translations)
    assert state["summary"]["record_count"] == 5
    assert state["summary"]["master_writes"] == 0
    assert state["summary"]["production_apply"] is False
    assert state["records"][0]["fields"][0]["promotion_status"] == "PROMOTED"
    assert state["records"][1]["fields"][0]["promotion_status"] == "SOURCE_MISSING"
    assert state["records"][2]["fields"][0]["promotion_status"] == "QA_BLOCKED"
    assert state["records"][3]["fields"][0]["promotion_status"] == "POLICY_BLOCKED"
    assert [row["source_record_hash"] for row in production_input["records"]] == before
    assert state["release_candidate"]["records"][0]["fields"]["title_zh"] == "保温袋 500 ml"
    assert state["release_candidate"]["records"][2]["fields"].get("title_zh") is None


def test_synthetic_5000_input_is_stable_and_offline():
    records = _records(5000)
    started = time.monotonic()
    result = build_production_input(records, source_run_id="synthetic", run_id="synthetic-5000")
    elapsed = time.monotonic() - started
    assert result["manifest"]["record_count"] == 5000
    assert result["manifest"]["unique_asin_count"] == 5000
    assert len({row["asin"] for row in result["records"]}) == 5000
    assert elapsed < 10


def test_production_cli_stages_are_checkpointed(tmp_path):
    master = tmp_path / "master.json"
    config = tmp_path / "config.json"
    run_dir = tmp_path / "runtime" / "production" / "run-1"
    master.write_text(json.dumps(_records(2), ensure_ascii=False), encoding="utf-8")
    config.write_text(json.dumps({
        "prompt_version": "amazon-es-retail-v2", "fields": ["title_es_raw"],
        "providers": [
            {"name": "qwen-a", "api_key_env": "MISSING_A", "endpoint_env": "MISSING_EA"},
            {"name": "qwen-b", "api_key_env": "MISSING_B", "endpoint_env": "MISSING_EB"},
            {"name": "qwen-c", "api_key_env": "MISSING_C", "endpoint_env": "MISSING_EC"},
        ], "max_workers": 3,
    }), encoding="utf-8")
    assert main(["translation-production", "--stage", "build-input", "--master", str(master),
                 "--run-dir", str(run_dir), "--run-id", "run-1"]) == 0
    assert main(["translation-production", "--stage", "preclean", "--run-dir", str(run_dir),
                 "--run-id", "run-1"]) == 0
    assert main(["translation-production", "--stage", "plan", "--run-dir", str(run_dir),
                 "--run-id", "run-1", "--config", str(config)]) == 0
    plan = json.loads((run_dir / "plan" / "translation_plan.json").read_text(encoding="utf-8"))
    assert plan["total_records"] == 2
    assert plan["estimated_api_requests"] >= 0
    assert plan["pool"]["max_workers"] == 3
    assert set(plan["estimated_provider_requests"]) == {"qwen-a", "qwen-b", "qwen-c"}
    translation_dir = run_dir / "translations"
    translation_dir.mkdir(parents=True, exist_ok=True)
    (translation_dir / "translation_results.json").write_text(json.dumps({
        "B000000000": {"asin": "B000000000", "fields": {
            "title_zh": {"translated_text": "保温袋", "candidate_text": "保温袋",
                          "translation_status": "success", "qa_status": "pass", "qa_issues": []}
        }}}, ensure_ascii=False), encoding="utf-8")
    assert main(["translation-production", "--stage", "promote", "--run-dir", str(run_dir),
                 "--run-id", "run-1", "--config", str(config)]) == 0
    state = json.loads((run_dir / "state" / "translation_state.json").read_text(encoding="utf-8"))
    assert state["summary"]["master_writes"] == 0
    assert (run_dir / "release" / "production_release_candidate.json").exists()
    workbook = run_dir / "release" / "production_release.xlsx"
    assert main(["translation-production", "--stage", "export", "--run-dir", str(run_dir),
                 "--run-id", "run-1", "--force", "--out", str(workbook)]) == 0
    assert workbook.exists()
