import json

import pytest

from amazon_es_bestseller.cli import main
from amazon_es_bestseller.orchestration.production_run import ProductionRun
from amazon_es_bestseller.orchestration.task_config import TaskConfig
from amazon_es_bestseller.orchestration.workflow import ProductionWorkflow


ASIN = "B000000001"
SOURCE_URL = "https://www.amazon.es/Best-Sellers/zgbs/123"


def _write_fixture(root, *, rank=1, mode="initial"):
    html = root / "html"; html.mkdir(exist_ok=True)
    (html / (ASIN + ".html")).write_text(
        "<html><body><input id='ASIN' value='%s'><h1 id='productTitle'>Botella de acero 500 ml</h1>"
        "<span class='a-price'><span class='a-offscreen'>12,00€</span></span></body></html>" % ASIN,
        encoding="utf-8")
    evidence = {
        "planned_sources": [{"source_url": SOURCE_URL, "page_number": 1}],
        "source_statuses": [{"source_url": SOURCE_URL, "page_number": 1,
                             "status": "NORMAL", "access_state": "NORMAL",
                             "parse_status": "PARSE_OK", "parsed_record_count": 1}],
        "records": [{"asin": ASIN, "ranking_asin": ASIN, "bestseller_rank": rank,
                     "ranking_source_url": SOURCE_URL, "ranking_page_number": 1,
                     "ranking_product_url_raw": "/dp/%s" % ASIN,
                     "ranking_product_url_normalized": "https://www.amazon.es/dp/%s" % ASIN,
                     "ranking_link_asin": ASIN, "ranking_link_identity_status": "MATCH",
                     "leaf_category": "Cocina", "browse_node_id": "123"}],
    }
    (root / "ranking.json").write_text(json.dumps(evidence), encoding="utf-8")
    config = {"task_id": "offline-v1-fixture", "mode": mode, "network_mode": "offline",
              "profile": "production-research", "history_dir": "history",
              "source": {"ranking_evidence": "ranking.json", "detail_html_dirs": ["html"]},
              "translation": {"provider_mode": "fake"}}
    config_path = root / "task.json"; config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def test_offline_source_only_executes_real_v1_snapshot_reparse_audit_and_master(tmp_path):
    config = _write_fixture(tmp_path)
    run_dir = tmp_path / "run"

    assert main(["--offline", "production-run", "--run-dir", str(run_dir), "--run-id", "initial",
                 "--config", str(config), "--profile", "source-only"]) == 0

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "DRAFT_SOURCE_ONLY"
    assert (run_dir / "artifacts" / "spanish-master.json").exists()
    assert (run_dir / "work" / "details" / "plans" / "detail_plan.json").exists()
    master = json.loads((tmp_path / "history" / "spanish_master.json").read_text(encoding="utf-8"))
    assert master["records"][0]["asin"] == ASIN
    assert master["records"][0]["title_es_raw"] == "Botella de acero 500 ml"
    metrics = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert metrics["ranking_records"] == 1 and metrics["detail_offline_reparsed"] == 1
    assert metrics["detail_requested"] == 0


def test_incremental_history_emits_rank_status_and_keeps_human_notes(tmp_path):
    initial = _write_fixture(tmp_path, rank=1)
    history = tmp_path / "history"
    first = tmp_path / "first"
    assert main(["--offline", "production-run", "--run-dir", str(first), "--run-id", "first",
                 "--config", str(initial), "--profile", "source-only"]) == 0
    master_path = history / "spanish_master.json"
    master = json.loads(master_path.read_text(encoding="utf-8")); master["records"][0]["notes"] = "human-owned"
    master_path.write_text(json.dumps(master), encoding="utf-8")
    config = _write_fixture(tmp_path, rank=5, mode="incremental")
    second = tmp_path / "second"
    assert main(["--offline", "production-run", "--run-dir", str(second), "--run-id", "second",
                 "--config", str(config), "--profile", "source-only"]) == 0

    history_rows = [json.loads(line) for line in (history / "ranking_snapshots.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["status"] == "RANK_DOWN" for event in history_rows[-1]["changes"])
    next_master = json.loads(master_path.read_text(encoding="utf-8"))
    assert next_master["records"][0]["notes"] == "human-owned"


def test_resume_reuses_actual_completed_artifacts_and_rejects_changed_evidence(tmp_path):
    config_path = _write_fixture(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    task = TaskConfig.from_mapping(raw, base_dir=tmp_path)
    run_dir = tmp_path / "run"
    workflow = ProductionWorkflow(task, run_dir, run_id="resume")
    handlers = workflow.handlers()
    original = handlers["normalize"]
    handlers["normalize"] = lambda _context: (_ for _ in ()).throw(RuntimeError("simulated crash"))
    runner = ProductionRun(run_dir, run_id="resume", config=task.runner_config(), offline=True)
    with pytest.raises(RuntimeError, match="simulated crash"):
        runner.run(handlers, profile="source-only")
    runner.run(workflow.handlers(), from_stage="normalize", profile="source-only")
    manifest = json.loads((run_dir / "runmanifest.json").read_text(encoding="utf-8"))
    assert manifest["stages"]["ranking-authority"]["status"] == "READY"

    evidence = json.loads((tmp_path / "ranking.json").read_text(encoding="utf-8"))
    evidence["records"][0]["bestseller_rank"] = 2
    (tmp_path / "ranking.json").write_text(json.dumps(evidence), encoding="utf-8")
    changed = TaskConfig.from_mapping(raw, base_dir=tmp_path)
    with pytest.raises(Exception, match="RESUME_FINGERPRINT_MISMATCH"):
        ProductionRun(run_dir, run_id="resume", config=changed.runner_config(), offline=True).run(
        ProductionWorkflow(changed, run_dir, run_id="resume").handlers(), profile="source-only")


def test_incremental_reparses_old_detail_schema_from_saved_html(tmp_path):
    initial = _write_fixture(tmp_path)
    assert main(["--offline", "production-run", "--run-dir", str(tmp_path / "first"), "--run-id", "first",
                 "--config", str(initial), "--profile", "source-only"]) == 0
    details_path = tmp_path / "history" / "details.json"
    details = json.loads(details_path.read_text(encoding="utf-8"))
    details[0]["detail_schema_version"] = 1
    details[0]["detail_parser_version"] = "legacy-parser"
    details_path.write_text(json.dumps(details), encoding="utf-8")
    incremental = _write_fixture(tmp_path, mode="incremental")
    run_dir = tmp_path / "schema-reparse"
    assert main(["--offline", "production-run", "--run-dir", str(run_dir), "--run-id", "schema-reparse",
                 "--config", str(incremental), "--profile", "source-only"]) == 0
    detail_stage = json.loads((run_dir / "artifacts" / "detail-evidence.json").read_text(encoding="utf-8"))
    assert detail_stage["seed_reparsed_asins"] == [ASIN]
    assert json.loads(details_path.read_text(encoding="utf-8"))[0]["detail_schema_version"] == 2


def test_refresh_due_is_targeted_and_offline_run_refuses_fetch(tmp_path):
    config = _write_fixture(tmp_path, mode="incremental")
    raw = json.loads(config.read_text(encoding="utf-8")); raw["detail"] = {"refresh_due_asins": [ASIN]}
    config.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SystemExit, match="OFFLINE_NETWORK_ACTION_REQUIRED:%s" % ASIN):
        main(["--offline", "production-run", "--run-dir", str(tmp_path / "refresh"), "--run-id", "refresh",
              "--config", str(config), "--profile", "source-only"])


def test_full_graph_calls_real_translation_qa_and_release_gate_but_fake_provider_cannot_release(tmp_path):
    config = _write_fixture(tmp_path)
    run_dir = tmp_path / "full"
    with pytest.raises(Exception, match="RELEASE_GATE_NOT_READY"):
        main(["--offline", "production-run", "--run-dir", str(run_dir), "--run-id", "full",
              "--config", str(config), "--profile", "full"])
    translation = json.loads((run_dir / "artifacts" / "translation.json").read_text(encoding="utf-8"))
    dictionary = json.loads((run_dir / "artifacts" / "dictionary-rerender.json").read_text(encoding="utf-8"))
    repair = json.loads((run_dir / "artifacts" / "field-repair.json").read_text(encoding="utf-8"))
    release = json.loads((run_dir / "artifacts" / "release.json").read_text(encoding="utf-8"))
    assert translation["provider_provenance"]["provider"] == "fake"
    assert dictionary["counts"]["evidence"] >= 1
    assert "repair_queue" in repair and "translation_state" in repair
    assert release["artifacts"]["translation"]["payload"]["state"]["records"]
    errors = (run_dir / "errors.jsonl").read_text(encoding="utf-8")
    assert "RELEASE_GATE_NOT_READY" in errors


def test_task_config_rejects_ready_payload_injection(tmp_path):
    config = _write_fixture(tmp_path)
    raw = json.loads(config.read_text(encoding="utf-8")); raw["stages"] = {"preflight": {"status": "READY"}}
    with pytest.raises(Exception, match="STAGE_PAYLOADS_FORBIDDEN"):
        TaskConfig.from_mapping(raw, base_dir=tmp_path)
