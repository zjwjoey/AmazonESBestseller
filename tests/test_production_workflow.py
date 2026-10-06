import json
from datetime import datetime, timezone

import pytest

from amazon_es_bestseller.cli import main
from amazon_es_bestseller.collection.detail import reparse_saved_details
from amazon_es_bestseller.collection.ranking import parse_bestsellers_page
from amazon_es_bestseller.collection.planning import DetailState
from amazon_es_bestseller.monitoring.snapshot import build_ranking_snapshot
from amazon_es_bestseller.orchestration.production_run import ProductionRun
from amazon_es_bestseller.orchestration.task_config import TaskConfig
from amazon_es_bestseller.orchestration.translation_batch import create_selection_manifest, write_selection_manifest
from amazon_es_bestseller.orchestration.workflow import (
    ExistingV1DetailCollector,
    ExistingV1SnapshotCollector,
    ProductionWorkflow,
)
from amazon_es_bestseller.translation.budget import BudgetLedger, BudgetedProvider, VerifiedPriceCard
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider
from amazon_es_bestseller.translation.service import source_hash
from amazon_es_bestseller.quality.chinese import audit_field


ASIN = "B000000001"
ASINS = (ASIN, "B000000002", "B000000003")
SOURCE_URL = "https://www.amazon.es/Best-Sellers/zgbs/123"


class ReviewedOfflineFixtureProvider(TranslationProvider):
    """Test-only provider: reviewed provenance, but no transport implementation."""

    name = "qwen-mt"

    def __init__(self):
        self.calls = 0

    @property
    def model(self):
        return "qwen-mt-fixture"

    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls += 1
        translated = "不锈钢水瓶 500 ml" if field == "title_es_raw" else "厨房"
        return ProviderResponse(text=translated, provider=self.name, model=self.model,
                                raw={"fixture": True, "network": False})


def _write_fixture(root, *, rank=1, mode="initial", asins=(ASIN,)):
    html = root / "html"; html.mkdir(exist_ok=True)
    cards = []
    for index, asin in enumerate(asins, rank):
        (html / (asin + ".html")).write_text(
            "<html><body><input id='ASIN' value='%s'><h1 id='productTitle'>Botella de acero 500 ml</h1>"
            "<span class='a-price'><span class='a-offscreen'>12,00€</span></span></body></html>" % asin,
            encoding="utf-8")
        cards.append("<div id='gridItemRoot'><span class='a-badge-text'>#%d</span>"
                     "<a href='/dp/%s'>Botella de acero 500 ml</a></div>" % (index, asin))
    ranking_html = ("<html><body><div id='zg_browseRoot'>"
                    "<a href='/zgbs/1'>Hogar</a><a href='/zgbs/12'>Cocina</a>"
                    "<a href='/zgbs/123'>Botellas</a><a href='/zgbs/1234'>Termos</a>"
                    "</div>%s</body></html>") % "".join(cards)
    records = parse_bestsellers_page(ranking_html, SOURCE_URL, "")
    evidence = {
        "planned_sources": [{"source_url": SOURCE_URL, "page_number": 1}],
        "source_statuses": [{"source_url": SOURCE_URL, "page_number": 1,
                             "status": "NORMAL", "access_state": "NORMAL",
                             "parse_status": "PARSE_OK", "parsed_record_count": len(records)}],
        "html_files": {"ranking_000.html": ranking_html},
        "records": records,
    }
    (root / "ranking.json").write_text(json.dumps(evidence), encoding="utf-8")
    config = {"task_id": "offline-v1-fixture", "mode": mode, "network_mode": "offline",
              "profile": "production-research", "history_dir": "history",
              "source": {"ranking_evidence": "ranking.json", "detail_html_dirs": ["html"]},
              "translation": {"provider_mode": "fake"}}
    config_path = root / "task.json"; config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def _attach_live_plan(raw, root):
    plan_path = root / "reviewed_task_plan.json"
    plan_path.write_text(json.dumps({"fixture": True}), encoding="utf-8")
    raw["reviewed_task_plan"] = plan_path.name


def test_offline_source_only_executes_real_v1_snapshot_reparse_audit_and_master(tmp_path):
    config = _write_fixture(tmp_path)
    run_dir = tmp_path / "run"

    assert main(["--offline", "production-run", "--run-dir", str(run_dir), "--run-id", "initial",
                 "--config", str(config), "--profile", "source-only"]) == 0

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "DRAFT_SOURCE_ONLY"
    assert summary["formal_release"] is False
    assert (run_dir / "artifacts" / "spanish-master.json").exists()
    assert (run_dir / "work" / "details" / "plans" / "detail_plan.json").exists()
    master = json.loads((tmp_path / "history" / "spanish_master.json").read_text(encoding="utf-8"))
    assert master["records"][0]["asin"] == ASIN
    assert master["records"][0]["title_es_raw"] == "Botella de acero 500 ml"
    master_artifact = json.loads((run_dir / "artifacts" / "spanish-master.json").read_text(encoding="utf-8"))
    assert "stage_reports" not in master_artifact
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


def test_production_workflow_ready_end_to_end_offline_candidate_is_nonformal_with_fake_provider(tmp_path):
    config = _write_fixture(tmp_path)
    run_dir = tmp_path / "full"
    with pytest.raises(Exception, match="RELEASE_GATE_NOT_READY"):
        main(["--offline", "production-run", "--run-dir", str(run_dir), "--run-id", "full",
              "--config", str(config), "--profile", "full"])
    translation = json.loads((run_dir / "artifacts" / "translation.json").read_text(encoding="utf-8"))
    dictionary_input = json.loads((run_dir / "artifacts" / "dictionary.json").read_text(encoding="utf-8"))
    dictionary = json.loads((run_dir / "artifacts" / "dictionary-rerender.json").read_text(encoding="utf-8"))
    repair = json.loads((run_dir / "artifacts" / "field-repair.json").read_text(encoding="utf-8"))
    release = json.loads((run_dir / "artifacts" / "release.json").read_text(encoding="utf-8"))
    assert translation["provider_provenance"]["provider"] == "fake"
    assert dictionary["counts"]["evidence"] >= 1
    assert "repair_queue" in repair and "translation_state" in repair
    assert release["status"] == "READY" and release["formal_release"] is False
    assert translation["state"]["release_candidate"]["release_status"] == "READY"
    manifest = dictionary_input["dictionary_manifest"]
    execution_fields = [field for record in translation["execution"]["records"].values()
                        for field in record["fields"].values()]
    assert execution_fields and {field["dictionary_version"] for field in execution_fields} == {
        str(manifest["dictionary_version"])}
    for check in ("ranking_authority", "detail_identity", "offline_replay"):
        report = release["artifacts"][check]["payload"]
        assert report["status"] == "PASS" and report["produced_stage"] == check
        assert report["report_produced_by"] == "translation-input"
    assert release["artifacts"]["translation"]["payload"]["state"]["records"]
    errors = (run_dir / "errors.jsonl").read_text(encoding="utf-8")
    assert "RELEASE_GATE_NOT_READY" in errors


def test_formal_ready_offline_fixture_provider_uses_selected_batch_reports_and_exports_excel(tmp_path):
    """Exercise the real ProductionWorkflow, not a hand-built release blob."""
    config_path = _write_fixture(tmp_path, asins=ASINS)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["translation"] = {"provider_mode": "qwen-mt-fixture"}
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    source_task = TaskConfig.from_mapping(raw, base_dir=tmp_path)
    run_dir = tmp_path / "formal"
    source_result = ProductionRun(run_dir, run_id="formal", config=source_task.runner_config(), offline=True).run(
        ProductionWorkflow(source_task, run_dir, run_id="formal").handlers(), profile="source-only")
    assert source_result["status"] == "DRAFT_SOURCE_ONLY"

    selection = create_selection_manifest(
        master_artifact_path=run_dir / "artifacts" / "spanish-master.json",
        selected_asins=list(ASINS[:2]), selection_id="formal-subset")
    write_selection_manifest(tmp_path / "selection.json", selection)
    raw["translation"]["selection_manifest"] = "selection.json"
    task = TaskConfig.from_mapping(raw, base_dir=tmp_path)

    provider = ReviewedOfflineFixtureProvider()
    result = ProductionRun(run_dir, run_id="formal", config=task.runner_config(), offline=True).run(
        ProductionWorkflow(task, run_dir, run_id="formal", translation_provider=provider).handlers(),
        from_stage="translation-input", profile="full")
    assert result["status"] == "READY" and result["formal_release"] is True
    assert provider.calls > 0
    translation = json.loads((run_dir / "artifacts" / "translation.json").read_text(encoding="utf-8"))
    rerender = json.loads((run_dir / "artifacts" / "dictionary-rerender.json").read_text(encoding="utf-8"))
    release = json.loads((run_dir / "artifacts" / "release.json").read_text(encoding="utf-8"))
    raw_versions = {
        str(field["dictionary_version"])
        for record in translation["state"]["records"]
        for field in record["fields"]
        if field.get("source_text")
    }
    final_translation_state = release["artifacts"]["translation"]["payload"]["state"]
    final_versions = {
        str(candidate["dictionary_version"])
        for record in final_translation_state["release_candidate"]["records"]
        for candidate in record["field_candidates"]
    }
    assert raw_versions == {"0"}
    assert rerender["dictionary_sync"]["manifest"]["dictionary_version"] == 1
    assert rerender["rerender"]["updates"]
    assert final_versions == {"1"}
    run_manifest = json.loads((run_dir / "runmanifest.json").read_text(encoding="utf-8"))
    assert run_manifest["stages"]["release"]["payload"]["release_decision"]["ready"]
    reports = json.loads((run_dir / "artifacts" / "translation-input.json").read_text(encoding="utf-8"))["translation_batch_reports"]
    for check in ("ranking_authority", "detail_identity", "offline_replay"):
        assert reports[check]["status"] == "PASS"
        assert reports[check]["summary"]["records_checked"] == 2
        assert {row["asin"] for row in reports[check]["records"]} == set(ASINS[:2])
    assert (run_dir / "output" / "selection.xlsx").exists()


def test_formal_ready_offline_fixture_provider_with_dictionary_no_change(tmp_path):
    config_path = _write_fixture(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["translation"] = {"provider_mode": "qwen-mt-fixture"}
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    task = TaskConfig.from_mapping(raw, base_dir=tmp_path)
    run_dir = tmp_path / "formal-no-change"
    provider = ReviewedOfflineFixtureProvider()
    result = ProductionRun(run_dir, run_id="formal-no-change", config=task.runner_config(), offline=True).run(
        ProductionWorkflow(task, run_dir, run_id="formal-no-change", translation_provider=provider).handlers(),
        profile="full")
    assert result["status"] == "READY" and provider.calls > 0
    rerender = json.loads((run_dir / "artifacts" / "dictionary-rerender.json").read_text(encoding="utf-8"))
    assert rerender["rerender"]["status"] == "NO_CHANGE"
    assert rerender["dictionary_sync"]["change_log"] == []


def test_batch_reports_block_real_identity_mismatch_and_missing_replay_html(tmp_path):
    config_path = _write_fixture(tmp_path)
    evidence_path = tmp_path / "ranking.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence.pop("html_files")
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    task = TaskConfig.from_mapping(json.loads(config_path.read_text(encoding="utf-8")), base_dir=tmp_path)
    run_dir = tmp_path / "blocked-reports"
    ProductionRun(run_dir, run_id="blocked-reports", config=task.runner_config(), offline=True).run(
        ProductionWorkflow(task, run_dir, run_id="blocked-reports").handlers(), profile="source-only")
    detail_path = run_dir / "work" / "details" / "details.json"
    details = json.loads(detail_path.read_text(encoding="utf-8"))
    details[0]["final_url"] = "https://www.amazon.es/dp/B099999999"
    detail_path.write_text(json.dumps(details), encoding="utf-8")

    with pytest.raises(Exception, match="RELEASE_GATE_NOT_READY"):
        ProductionRun(run_dir, run_id="blocked-reports", config=task.runner_config(), offline=True).run(
            ProductionWorkflow(task, run_dir, run_id="blocked-reports").handlers(),
            from_stage="translation-input", profile="full")
    reports = json.loads((run_dir / "artifacts" / "translation-input.json").read_text(encoding="utf-8"))["translation_batch_reports"]
    assert reports["detail_identity"]["status"] == "BLOCK"
    assert any(row.get("issue_code") == "IDENTITY_MISMATCH" for row in reports["detail_identity"]["issues"])
    assert reports["offline_replay"]["status"] == "BLOCK"
    assert any(row.get("issue_code") == "REPLAY_EVIDENCE_MISSING" for row in reports["offline_replay"]["issues"])


def test_task_config_rejects_ready_payload_injection(tmp_path):
    config = _write_fixture(tmp_path)
    raw = json.loads(config.read_text(encoding="utf-8")); raw["stages"] = {"preflight": {"status": "READY"}}
    with pytest.raises(Exception, match="STAGE_PAYLOADS_FORBIDDEN"):
        TaskConfig.from_mapping(raw, base_dir=tmp_path)


def test_live_v1_adapter_is_explicit_and_fake_transport_exercises_source_flow(tmp_path, monkeypatch):
    config_path = _write_fixture(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["network_mode"] = "live"
    _attach_live_plan(raw, tmp_path)
    raw["source"] = {"source_urls": [SOURCE_URL], "pages_per_url": 1, "detail_html_dirs": ["html"]}
    task = TaskConfig.from_mapping(raw, base_dir=tmp_path)
    evidence = json.loads((tmp_path / "ranking.json").read_text(encoding="utf-8"))

    class FakeCollector:
        calls = 0

        def collect(self, supplied_task, output_root):
            self.calls += 1
            assert supplied_task.source_urls == (SOURCE_URL,)
            return build_ranking_snapshot(evidence["records"], output_root,
                                          planned_sources=evidence["planned_sources"],
                                          source_statuses=evidence["source_statuses"],
                                          snapshot_id="snapshot_fake_live", publish_authoritative_pointer=False)

    collector = FakeCollector()
    run_dir = tmp_path / "live"
    result = ProductionRun(run_dir, run_id="live", config=task.runner_config(), offline=False).run(
        ProductionWorkflow(task, run_dir, run_id="live", snapshot_collector=collector).handlers(),
        profile="source-only")
    assert result["status"] == "DRAFT_SOURCE_ONLY" and collector.calls == 1

    observed = {}
    monkeypatch.setattr("amazon_es_bestseller.monitoring.snapshot.collect_ranking_snapshot",
                        lambda urls, session, output_root, **kwargs: observed.update(
                            urls=tuple(urls), session=session, output_root=output_root, **kwargs) or {"ok": True})
    session = object()
    assert ExistingV1SnapshotCollector(session).collect(task, tmp_path / "adapter") == {"ok": True}
    assert observed["urls"] == (SOURCE_URL,) and observed["session"] is session
    assert observed["pages_per_url"] == 1 and observed["parser_version"] == "v1"


def test_live_v1_detail_adapter_uses_fake_transport_only_when_plan_requires_fetch(tmp_path, monkeypatch):
    config_path = _write_fixture(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["network_mode"] = "live"
    _attach_live_plan(raw, tmp_path)
    raw["source"] = {"source_urls": [SOURCE_URL], "pages_per_url": 1, "detail_html_dirs": []}
    task = TaskConfig.from_mapping(raw, base_dir=tmp_path)
    evidence = json.loads((tmp_path / "ranking.json").read_text(encoding="utf-8"))

    class FakeSnapshotCollector:
        def collect(self, supplied_task, output_root):
            assert supplied_task is task
            return build_ranking_snapshot(evidence["records"], output_root,
                                          planned_sources=evidence["planned_sources"],
                                          source_statuses=evidence["source_statuses"],
                                          snapshot_id="snapshot_fake_detail", publish_authoritative_pointer=False)

    class FakeDetailCollector:
        calls = []

        def __call__(self, asins, _session, _output_root, **kwargs):
            self.calls.append((list(asins), dict(kwargs)))
            return reparse_saved_details(tmp_path / "html", DetailState(tmp_path / "fake_detail_state.json"),
                                         asins=asins, parser_version=kwargs["parser_version"])

    detail_collector = FakeDetailCollector()
    run_dir = tmp_path / "live-detail"
    result = ProductionRun(run_dir, run_id="live-detail", config=task.runner_config(), offline=False).run(
        ProductionWorkflow(task, run_dir, run_id="live-detail", snapshot_collector=FakeSnapshotCollector(),
                           detail_collector=detail_collector).handlers(), profile="source-only")
    assert result["status"] == "DRAFT_SOURCE_ONLY"
    assert detail_collector.calls[0][0] == [ASIN]
    assert detail_collector.calls[0][1]["parser_version"] == "v1"
    metrics = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert metrics["detail_requested"] == 1 and metrics["detail_success"] == 1

    observed = {}
    monkeypatch.setattr("amazon_es_bestseller.collection.detail.collect_details",
                        lambda asins, session, out_dir, **kwargs: observed.update(
                            asins=asins, session=session, out_dir=out_dir, **kwargs) or [])
    session = object()
    ExistingV1DetailCollector(session)([ASIN], None, str(tmp_path / "adapter"), parser_version="v1")
    assert observed["asins"] == [ASIN] and observed["session"] is session
    assert observed["parser_version"] == "v1"


def test_qwen_mode_requires_injected_budgeted_provider_without_real_transport(tmp_path):
    config_path = _write_fixture(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["network_mode"] = "live"
    _attach_live_plan(raw, tmp_path)
    raw["source"] = {"source_urls": [SOURCE_URL], "pages_per_url": 1, "detail_html_dirs": ["html"]}
    raw["translation"] = {"provider_mode": "qwen-mt"}
    task = TaskConfig.from_mapping(raw, base_dir=tmp_path)
    evidence = json.loads((tmp_path / "ranking.json").read_text(encoding="utf-8"))

    class FakeSnapshotCollector:
        def collect(self, _task, output_root):
            return build_ranking_snapshot(evidence["records"], output_root,
                                          planned_sources=evidence["planned_sources"],
                                          source_statuses=evidence["source_statuses"],
                                          snapshot_id="snapshot_fake_%s" % output_root.parent.parent.name,
                                          publish_authoritative_pointer=False)

    class FakeBudgetedQwen(TranslationProvider):
        name = "qwen-mt"

        def __init__(self):
            self.calls = 0

        @property
        def model(self):
            return "qwen-mt-fixture"

        def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
            self.calls += 1
            return ProviderResponse(text="离线预算测试", provider=self.name, model=self.model,
                                    raw={"usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    # A billable-mode run consumes a selection only after a source-only run
    # wrote its immutable Spanish Master producer artifact.
    source_task = TaskConfig.from_mapping(raw, base_dir=tmp_path)
    source_dir = tmp_path / "source-only"
    ProductionRun(source_dir, run_id="source-only", config=source_task.runner_config(), offline=False).run(
        ProductionWorkflow(source_task, source_dir, run_id="source-only",
                           snapshot_collector=FakeSnapshotCollector()).handlers(), profile="source-only")
    selection = create_selection_manifest(master_artifact_path=source_dir / "artifacts" / "spanish-master.json",
                                          selected_asins=[ASIN], selection_id="qwen-test")
    write_selection_manifest(tmp_path / "selection.json", selection)
    raw["translation"]["selection_manifest"] = "selection.json"
    task = TaskConfig.from_mapping(raw, base_dir=tmp_path)

    raw_provider = FakeBudgetedQwen()
    with pytest.raises(Exception, match="QWEN_PROVIDER_MUST_BE_BUDGETED"):
        ProductionRun(source_dir, run_id="source-only", config=task.runner_config(), offline=False).run(
            ProductionWorkflow(task, source_dir, run_id="source-only",
                               snapshot_collector=FakeSnapshotCollector(),
                               translation_provider=raw_provider).handlers(), from_stage="translation-input", profile="full")
    assert raw_provider.calls == 0
    card = VerifiedPriceCard.from_mapping({
        "provider": raw_provider.name, "model": raw_provider.model, "currency": "CNY",
        "input_per_million_cny": "0.01", "output_per_million_cny": "0.01",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://help.aliyun.com/pricing/qwen-mt",
    }, provider=raw_provider.name, model=raw_provider.model)
    ledger = BudgetLedger(tmp_path / "budget.json", limit_cny="5.00", price_card=card,
                          max_output_tokens=32, prompt_overhead_tokens=16, max_unique_asins=1500)
    provider = BudgetedProvider(raw_provider, ledger)
    with pytest.raises(Exception, match="RELEASE_GATE_NOT_READY"):
        ProductionRun(source_dir, run_id="source-only", config=task.runner_config(), offline=False).run(
            ProductionWorkflow(task, source_dir, run_id="source-only", snapshot_collector=FakeSnapshotCollector(),
                               translation_provider=provider).handlers(), from_stage="translation", profile="full")
    translation = json.loads((source_dir / "artifacts" / "translation.json").read_text(encoding="utf-8"))
    assert raw_provider.calls > 0
    assert translation["provider_provenance"] == {
        "provider": "qwen-mt", "model": "qwen-mt-fixture", "verified": True,
        "request_count": translation["counts"]["total"],
    }
    assert ledger.snapshot()["selected_asins"] == [ASIN]


def test_field_repair_retries_only_failed_field_with_real_provider_result_and_reqa(tmp_path):
    task = TaskConfig.from_mapping(json.loads(_write_fixture(tmp_path).read_text(encoding="utf-8")), base_dir=tmp_path)
    run_dir = tmp_path / "repair"
    class RepairProvider(TranslationProvider):
        name = "fake-repair"

        @property
        def model(self):
            return "fake-repair-v1"

        def translate(self, _text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
            return ProviderResponse(text="\u5305\u542b LED 9V", provider=self.name, model=self.model,
                                    raw={"offline": True, "asin": asin, "field": field, "context": context or {}})

    workflow = ProductionWorkflow(task, run_dir, run_id="repair", translation_provider=RepairProvider())
    source = "Incluye LED 9V"
    failed = audit_field(asin=ASIN, field="description", source_es=source,
                         translated_zh="\u5305\u542b", source_hash=source_hash(source))
    assert failed["status"] == "REPAIR"
    state = {"records": [{"asin": ASIN, "source_record_hash": "record-hash", "fields": [{
        "field": "description", "target_field": "description_zh", "source_text": source,
        "source_hash": source_hash(source), "final_zh": "\u5305\u542b",
        "candidate_text": "\u5305\u542b", "promotion_status": "QA_BLOCKED",
    }]}]}
    qa_payload = workflow._store("chinese-qa", {"status": "READY", "fields": [failed],
                                                  "translation_state": state})
    repair = workflow.stage_field_repair({"manifest": {"stages": {"chinese-qa": {"payload": qa_payload}}}})
    result = repair["repair_results"]
    assert result and result[0]["status"] == "PASS" and result[0]["attempt"] == 1
    repaired = repair["translation_state"]["records"][0]["fields"][0]
    assert repaired["repair_status"] == "READY" and repaired["final_zh"] != "\u5305\u542b"
    reqa = workflow.stage_re_qa({"manifest": {"stages": {"field-repair": {"payload": repair}}}})
    assert reqa["fields"][0]["status"] == "PASS"
