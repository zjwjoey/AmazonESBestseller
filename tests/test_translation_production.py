import time
import csv
import json

import pytest

from amazon_es_bestseller.translation.preclean import audit_records
from amazon_es_bestseller.translation.production import (
    build_production_input, build_production_state, records_for_preclean,
    compute_release_status, merge_translation_shards,
)
from amazon_es_bestseller.translation.production_contract import (
    matches_category_filter, normalize_asin_filter,
)
from amazon_es_bestseller.translation.service import source_hash
from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.providers.qwen_mt import QwenMTProvider
from amazon_es_bestseller.translation.service import TranslationService
from amazon_es_bestseller.cli import _load_translation_products, main


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
    missing = _records(1)
    missing[0]["asin"] = ""
    with pytest.raises(ValueError, match="MISSING_ASIN"):
        build_production_input(missing)


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
    with pytest.raises(SystemExit, match="Release Gate"):
        main(["translation-production", "--stage", "export", "--run-dir", str(run_dir),
              "--run-id", "run-1", "--out", str(workbook)])
    with pytest.raises(SystemExit, match="不支持 --force"):
        main(["translation-production", "--stage", "export", "--run-dir", str(run_dir),
              "--run-id", "run-1", "--force", "--out", str(workbook)])
    assert main(["translation-production", "--stage", "export", "--run-dir", str(run_dir),
                 "--run-id", "run-1", "--debug-export", "--out", str(workbook)]) == 0
    assert workbook.exists()
    marker = json.loads(workbook.with_suffix(".release_status.json").read_text(encoding="utf-8"))
    assert marker == {"release_status": "FORCED_DEBUG", "formal_release": False,
                      "label": "NOT_FOR_RELEASE", "blocked_count": 6}


def test_csv_loader_preserves_every_source_column(tmp_path):
    path = tmp_path / "master.csv"
    fields = ["ASIN", "Parent ASIN", "商品名称（西语）", "当前售价", "商品链接", "备注"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"ASIN": "b000000001", "Parent ASIN": "P1", "商品名称（西语）": "Bolsa",
                         "当前售价": "19,99 €", "商品链接": "https://amazon.es/dp/B000000001", "备注": "人工备注"})
    rows = _load_translation_products(str(path))
    assert rows[0]["Parent ASIN"] == "P1"
    assert rows[0]["当前售价"] == "19,99 €"
    assert rows[0]["商品链接"].endswith("B000000001")
    assert rows[0]["备注"] == "人工备注"
    assert rows[0]["title_es_raw"] == "Bolsa"
    built = build_production_input(rows)
    source = built["records"][0]["source_record"]
    assert source["当前售价"] == "19,99 €"
    assert source["商品链接"].endswith("B000000001")
    assert source["备注"] == "人工备注"


def test_translation_shards_accumulate_and_merge_fields_idempotently():
    first = {"batch_id": "batch_1", "records": {"B1": {"asin": "B1", "fields": {
        "title_zh": {"source_hash": "h1", "translated_text": "商品", "translation_status": "success"},
    }}}}
    second = {"batch_id": "batch_2", "records": {"B1": {"asin": "B1", "fields": {
        "product_details_zh": {"source_hash": "h2", "translated_text": "详情", "translation_status": "success"},
    }}, "B2": {"asin": "B2", "fields": {
        "title_zh": {"source_hash": "h3", "translated_text": "另一个", "translation_status": "success"},
    }}}}
    aggregate = merge_translation_shards([first, second, first])
    assert set(aggregate) == {"B1", "B2"}
    assert set(aggregate["B1"]["fields"]) == {"title_zh", "product_details_zh"}
    assert aggregate["B1"]["fields"]["title_zh"]["translated_text"] == "商品"


def test_translation_shard_source_change_is_not_silent():
    aggregate = merge_translation_shards([
        {"records": {"B1": {"asin": "B1", "fields": {
            "title_zh": {"source_hash": "old", "translated_text": "旧", "translation_status": "success"}}}}},
        {"records": {"B1": {"asin": "B1", "fields": {
            "title_zh": {"source_hash": "new", "translated_text": "新", "translation_status": "success"}}}}},
    ])
    field = aggregate["B1"]["fields"]["title_zh"]
    assert field["promotion_status"] == "SOURCE_CHANGED"
    assert field["last_error"]["code"] == "SOURCE_CHANGED"
    assert field["history"][0]["source_hash"] == "old"


def test_release_status_covers_all_blocking_states():
    assert compute_release_status(["PROMOTED", "SOURCE_MISSING"]) == "READY"
    assert compute_release_status(["PROMOTED", "QA_BLOCKED"]) == "REVIEW_REQUIRED"
    for blocked in ("PRECLEAN_BLOCKED", "PENDING", "PROVIDER_FAILED", "POLICY_BLOCKED", "SOURCE_CHANGED"):
        assert compute_release_status(["PROMOTED", blocked]) == "BLOCKED"


def test_asin_filters_and_all_category_levels():
    assert normalize_asin_filter('["b1", {"ASIN": "B2"}, "B1"]') == ["B1", "B2"]
    assert normalize_asin_filter("b1,B2") == ["B1", "B2"]
    with pytest.raises(ValueError, match="INVALID_ASIN_FILTER_ELEMENT"):
        normalize_asin_filter([123, None, {}])
    row = {"category_l1": "Home", "category_l2": "Kitchen", "category_l3": "Storage", "leaf_category": "Boxes"}
    assert matches_category_filter(row, "kitchen")
    assert matches_category_filter(row, "storage")
    assert matches_category_filter(row, "boxes")
    assert not matches_category_filter(row, "garden")


def test_synthetic_5000_offline_batch_merge_promote_and_ready(tmp_path):
    records = [{"asin": f"B{index:09d}", "title_es_raw": f"Producto {index}",
                "当前售价": f"{index},99 €", "备注": "human"}
               for index in range(5000)]
    production_input = build_production_input(records, source_run_id="synthetic-5000", run_id="run-5000")
    original_hash = production_input["manifest"]["dataset_hash"]
    clean = audit_records(records_for_preclean(production_input))
    service = TranslationService(
        QwenMTProvider(model="qwen-mt-flash", api_key="unused"),
        TranslationCache(tmp_path / "synthetic-cache.json"),
        prompt_version="amazon-es-retail-v2",
    )
    plan = service.translate_records(clean["translation_input_records"], dry_run=True)
    assert plan["summary"]["total_records"] == 5000
    assert plan["summary"]["estimated_api_requests"] == 5000

    def shard_for(rows, name):
        return {"batch_id": name, "records": {
            row["asin"]: {"asin": row["asin"], "fields": {
                "title_zh": {"source_hash": row["fields"]["title_es_raw"]["source_hash"],
                              "translated_text": "商品 " + row["asin"],
                              "candidate_text": "商品 " + row["asin"],
                              "translation_status": "success", "qa_status": "pass", "qa_issues": []}
            }} for row in rows}}

    first = shard_for(production_input["records"][:100], "batch_000001")
    second = shard_for(production_input["records"][100:200], "batch_000002")
    aggregate = merge_translation_shards([first, second, first])
    assert len(aggregate) == 200
    state = build_production_state(production_input, clean["translation_input_records"], aggregate)
    assert state["summary"]["record_count"] == 5000
    assert state["summary"]["master_writes"] == 0
    assert state["release_candidate"]["release_status"] == "BLOCKED"
    assert state["input_manifest"]["dataset_hash"] == original_hash
    assert production_input["records"][0]["source_record"]["备注"] == "human"
    assert state["repair_queue"]


def test_cli_translation_batches_accumulate_and_repeat_idempotently(tmp_path):
    master = tmp_path / "master.json"
    config = tmp_path / "config.json"
    run_dir = tmp_path / "runtime" / "production" / "batch-run"
    master.write_text(json.dumps([
        {"asin": "B000000001", "brand": "Marca A", "category_l1": "Hogar"},
        {"asin": "B000000002", "brand": "Marca B", "category_l2": "Cocina"},
    ], ensure_ascii=False), encoding="utf-8")
    config.write_text(json.dumps({
        "fields": ["brand"], "providers": [
            {"name": "qwen-a", "api_key_env": "MISSING_A", "endpoint_env": "MISSING_EA"},
            {"name": "qwen-b", "api_key_env": "MISSING_B", "endpoint_env": "MISSING_EB"},
            {"name": "qwen-c", "api_key_env": "MISSING_C", "endpoint_env": "MISSING_EC"},
        ], "max_workers": 3,
    }), encoding="utf-8")
    assert main(["translation-production", "--stage", "build-input", "--master", str(master),
                 "--run-dir", str(run_dir), "--run-id", "batch-run"]) == 0
    assert main(["translation-production", "--stage", "preclean", "--run-dir", str(run_dir),
                 "--run-id", "batch-run"]) == 0
    for offset, asin_filter in ((0, '["b000000001"]'), (0, '[{"asin":"B000000002"}]')):
        assert main(["translation-production", "--stage", "translate", "--run-dir", str(run_dir),
                     "--run-id", "batch-run", "--config", str(config), "--asin-list", asin_filter,
                     "--yes"]) == 0
    # Repeat the first exact selection: its immutable shard is reused and the
    # aggregate remains two records rather than creating duplicate history.
    assert main(["translation-production", "--stage", "translate", "--run-dir", str(run_dir),
                 "--run-id", "batch-run", "--config", str(config),
                 "--asin-list", "B000000001", "--yes"]) == 0
    aggregate = json.loads((run_dir / "translations" / "translation_results.json").read_text(encoding="utf-8"))
    shards = list((run_dir / "translations" / "shards").glob("batch_*.json"))
    assert set(aggregate) == {"B000000001", "B000000002"}
    assert len(shards) == 2


def test_regression_namespace_cannot_become_production_state(tmp_path):
    master = tmp_path / "master.json"
    master.write_text(json.dumps([{"asin": "B000000001", "brand": "Marca"}], ensure_ascii=False), encoding="utf-8")
    production_dir = tmp_path / "runtime" / "translation_v2" / "production" / "run-a"
    regression_dir = tmp_path / "runtime" / "translation_v2" / "regression" / "run-700"
    regression_dir.mkdir(parents=True)
    (regression_dir / "translation_results.json").write_text(json.dumps({
        "B000000001": {"asin": "B000000001", "fields": {
            "brand": {"translated_text": "回归结果", "translation_status": "success", "qa_status": "pass"}
        }}}, ensure_ascii=False), encoding="utf-8")
    assert main(["translation-production", "--stage", "build-input", "--master", str(master),
                 "--run-dir", str(production_dir), "--run-id", "run-a"]) == 0
    assert main(["translation-production", "--stage", "preclean", "--run-dir", str(production_dir),
                 "--run-id", "run-a"]) == 0
    with pytest.raises(SystemExit, match="production-translate"):
        main(["translation-production", "--stage", "promote", "--run-dir", str(production_dir),
              "--run-id", "run-a"])
    assert not (production_dir / "state" / "translation_state.json").exists()
