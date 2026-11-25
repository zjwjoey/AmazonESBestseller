import json
from types import SimpleNamespace

import pytest

from amazon_es_bestseller.collection import detail
from amazon_es_bestseller.collection.detail_executor import execute_detail_plan
from amazon_es_bestseller.task_workspace import TaskWorkspace
from test_detail_extraction_regressions import Session


def test_detail_runner_publishes_batch_before_next_request_and_resume_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(detail, "time", SimpleNamespace(sleep=lambda _: None))
    workspace = TaskWorkspace.create(tmp_path / "managed", "task", audit_batch_size=2)
    root = workspace.root / "detail"
    asins = [f"B{index:09d}" for index in range(1, 4)]
    plan = {
        "task_id": "task", "task_root": str(workspace.root), "plan_id": "offline-plan",
        "snapshot_id": "fixture", "records": [
            {"ranking_asin": asin, "snapshot_id": "fixture", "detail_action": "FETCH_NEW"} for asin in asins
        ],
    }

    class InspectCommit(Session):
        def goto(self, url):
            if len(self.calls) == 2:
                records = json.loads((root / "details.json").read_text(encoding="utf-8"))
                assert [row["asin"] for row in records] == asins[:2]
            return super().goto(url)

    session = InspectCommit()
    result = execute_detail_plan(plan, session, str(root), parser_version="v2")
    assert result["state_record_count"] == 3
    assert len(session.calls) == 3
    reports = [json.loads(p.read_bytes()) for p in (workspace.root / "audit" / "batches").glob("*.json")]
    assert sorted(len(report["asins"]) for report in reports) == [1, 2]
    assert not list((workspace.root / "cache" / "active").glob("*.html"))
    same = execute_detail_plan(plan, session, str(root), parser_version="v2")
    assert same["state_record_count"] == 3
    assert len(session.calls) == 3
    assert len(list((workspace.root / "audit" / "batches").glob("*.json"))) == 2

def test_cli_managed_offline_replay_and_final_reconciliation(tmp_path):
    from amazon_es_bestseller.cli import main
    from amazon_es_bestseller.monitoring.detail_planner import write_detail_plan
    from amazon_es_bestseller.task_quality import finalize_task

    workspace = TaskWorkspace.create(tmp_path / "managed", "task")
    source = tmp_path / "saved"
    source.mkdir()
    asin = "B000000001"
    body = f'<html><body><input id="ASIN" value="{asin}"><span id="productTitle">Bolso</span></body></html>'
    import gzip
    (source / f"{asin}.html.gz").write_bytes(gzip.compress(body.encode()))
    plan = {"task_id": "task", "task_root": str(workspace.root), "snapshot_id": "fixture",
            "records": [{"ranking_asin": asin, "snapshot_id": "fixture", "detail_action": "REPARSE_SAVED_HTML"}]}
    path = write_detail_plan(plan, workspace.root / "detail" / "plan")["json"]
    assert main(["--offline", "detail-run", "--plan", str(path), "--html-dir", str(source)]) == 0
    assert main(["task-finalize", "--task-root", str(workspace.root)]) == 0
    result = finalize_task(workspace.root)
    assert result["state"] == "QUALITY_REVIEW_COMPLETE", result["errors"]
    assert result["detail"]["batch_covered"] == 1
    products = workspace.root / "detail" / "details.json"
    original = products.read_bytes()
    altered = json.loads(original)
    altered[0]["title_es_raw"] = "un producto distinto"
    products.write_text(json.dumps(altered), encoding="utf-8")
    recalculated = finalize_task(workspace.root)
    assert recalculated["state"] == "COLLECTION_COMPLETE"
    assert recalculated["detail"]["mismatched_output_asins"] == [asin]
    products.write_bytes(original)
    execution = workspace.root / "detail" / "detail_execution_manifest.json"
    recorded_execution = execution.read_bytes()
    pending = json.loads(recorded_execution)
    pending["records"][0]["status"] = "PENDING"
    execution.write_text(json.dumps(pending), encoding="utf8")
    assert "DETAIL_EXECUTION_SCOPE_MISMATCH" in finalize_task(workspace.root)["errors"]
    execution.write_bytes(recorded_execution)
    archive, = (workspace.root / "evidence" / "archive").glob("*.gz")
    archive.write_bytes(b"fixture corrupted")
    assert finalize_task(workspace.root)["state"] == "COLLECTION_COMPLETE"


def test_offline_resume_uses_archived_audit_and_rechecks_changed_source(tmp_path, monkeypatch):
    from amazon_es_bestseller.audit_ledger import AuditLedger
    from amazon_es_bestseller.monitoring.detail_planner import write_detail_plan
    from amazon_es_bestseller.task_quality import finalize_task

    workspace = TaskWorkspace.create(tmp_path / "task", "resume")
    source = tmp_path / "saved"
    source.mkdir()
    asin = "B000000001"
    html = source / (asin + ".html")
    html.write_text(f'<input id="ASIN" value="{asin}"><h1 id="productTitle">Bolso</h1>', encoding="utf8")
    plan = {"task_id": "resume", "task_root": str(workspace.root), "snapshot_id": "fixture",
            "records": [{"ranking_asin": asin, "snapshot_id": "fixture", "detail_action": "REPARSE_SAVED_HTML"}]}
    path = write_detail_plan(plan, workspace.root / "detail" / "plan")["json"]
    plan = json.loads(path.read_bytes())

    def run():
        return execute_detail_plan(plan, None, str(workspace.root / "detail"),
                                   offline=True, parser_version="v2", saved_html=source)

    assert run()["completed_count"] == 1
    with monkeypatch.context() as offline_resume:
        def unexpected_parser(*args, **kwargs):
            pytest.fail("unchanged evidence must reuse its audit")
        offline_resume.setattr(detail, "_classify_saved_page", unexpected_parser)
        assert run()["completed_count"] == 1
        html.rename(source / "retired.txt")
        assert run()["completed_count"] == 1
        version = AuditLedger.qa_version
        offline_resume.setattr(AuditLedger, "qa_version",
                               lambda self, stage: version(self, stage) + ":updated-rule")
        assert run()["completed_count"] == 1
        assert finalize_task(workspace.root)["state"] == "QUALITY_REVIEW_COMPLETE"
        with workspace.transaction() as connection:
            assert connection.execute("SELECT count(*) FROM audit_attempts").fetchone()[0] == 2
    ledger = AuditLedger(workspace)
    assert ledger.current("detail")[0]["status"] == "PASS"
    html.write_text('<input id="ASIN" value="B000000002"><h1 id="productTitle">Otro producto</h1>', encoding="utf8")
    assert run()["records"][0]["status"] == "TERMINAL_FAILURE"
    result = finalize_task(workspace.root)
    assert result["state"] == "QUALITY_REVIEW_COMPLETE", result["errors"]
    assert result["detail"]["passed"] == 0
    assert result["archive"]["files"] == 2
    assert json.loads((workspace.root / "detail" / "details.json").read_bytes()) == []

@pytest.mark.parametrize(("count", "sizes"), [(1, [1]), (49, [49]), (50, [50]),
                                             (51, [50, 1]), (100, [50, 50]), (137, [50, 50, 37])])
def test_terminal_batches_at_boundaries(tmp_path, count, sizes):
    from amazon_es_bestseller.collection.detail_batch_audit import DetailBatchAudit
    workspace = TaskWorkspace.create(tmp_path / "task", "terminal")
    applied = []
    coordinator = DetailBatchAudit(workspace, lambda rows: applied.append(len(rows)))
    data = {"status": "failed", "detail_status": "PRODUCT_NOT_FOUND"}
    for i in range(count):
        coordinator.accept(f"B{i:09d}", data)
    coordinator.flush(partial=True)
    assert applied == sizes
    assert len(coordinator.ledger.current("detail")) == count
    assert all(row["batch_id"] for row in coordinator.ledger.current("detail"))


def test_real_batch_boundaries_reconcile_field_totals_after_resume(tmp_path):
    from amazon_es_bestseller.collection.detail_batch_audit import DetailBatchAudit
    from amazon_es_bestseller.collection.field_completeness import EXTRACTED, PARSER_MISSED, SOURCE_ABSENT

    workspace = TaskWorkspace.create(tmp_path / "task", "field-counts")
    report_root = workspace.root / "audit"
    field = "feature_bullets_raw"

    for offset in (0, 50):
        coordinator = DetailBatchAudit(workspace, lambda _rows: None)
        for index in range(offset, offset + 50):
            state = (SOURCE_ABSENT, PARSER_MISSED, EXTRACTED, EXTRACTED, EXTRACTED)[index % 5]
            coordinator.ledger.record(
                stage="detail", unit_id=f"B{index:09d}", digests=[], record={"index": index},
                status="PASS", anomalies=[],
                fields={field: {"eligible": True, "state": state, "source_present": state != SOURCE_ABSENT}},
            )
        coordinator.flush(enforce_pause=False)
        totals = json.loads((report_root / "field_completeness.json").read_bytes())[field]
        assert totals["eligible_count"] == offset + 50
        assert totals["source_absent_count"] == totals["parser_missed_count"] == (offset + 50) // 5
        assert totals["extracted_count"] == (offset + 50) * 3 // 5

    batches = [json.loads(file.read_bytes())["fields"][field] for file in (report_root / "batches").glob("*.json")]
    assert len(batches) == 2
    for key in ("eligible_count", "source_present_count", "extracted_count", "parser_missed_count", "source_absent_count"):
        assert sum(batch[key] for batch in batches) == totals[key]


def test_interrupted_batch_republishes_without_new_attempt(tmp_path):
    from amazon_es_bestseller.collection.detail_batch_audit import DetailBatchAudit
    workspace = TaskWorkspace.create(tmp_path / "task", "restart", audit_batch_size=2)

    def crash(_rows):
        raise RuntimeError("fixture interruption during publication")

    coordinator = DetailBatchAudit(workspace, crash)
    data = {"status": "failed", "detail_status": "PRODUCT_NOT_FOUND"}
    coordinator.accept("B000000001", data)
    with pytest.raises(RuntimeError, match="fixture interruption"):
        coordinator.accept("B000000002", data)
    applied = []
    resumed = DetailBatchAudit(workspace, lambda rows: applied.append([row["unit_id"] for row in rows]))
    resumed.flush()
    resumed.accept("B000000002", data)
    assert applied == [["B000000001", "B000000002"]]
    assert len(resumed.ledger.current("detail")) == 2

def test_access_stop_preserves_partial_batch_and_prevents_next_request(tmp_path, monkeypatch):
    from amazon_es_bestseller.access.detector import AccessStopError
    monkeypatch.setattr(detail, "time", SimpleNamespace(sleep=lambda _: None))
    workspace = TaskWorkspace.create(tmp_path / "task", "access-stop")
    asins = [f"B{i:09d}" for i in range(1, 4)]
    plan = {"task_id": "access-stop", "task_root": str(workspace.root), "plan_id": "scope",
            "snapshot_id": "fixture", "records": [
                {"ranking_asin": asin, "snapshot_id": "fixture", "detail_action": "FETCH_NEW"} for asin in asins]}
    root = workspace.root / "detail"

    class Restricted(Session):
        def goto(self, url):
            super().goto(url)
            return 429 if len(self.calls) == 2 else 200

    transport = Restricted()
    with pytest.raises(AccessStopError):
        execute_detail_plan(plan, transport, str(root), parser_version="v2")
    assert len(transport.calls) == 2
    assert [row["asin"] for row in json.loads((root / "details.json").read_bytes())] == [asins[0]]
    report = json.loads((root / "detail_execution_manifest.json").read_bytes())
    assert report["execution_status"] == "ACCESS_STOP"
    assert len(list((workspace.root / "audit" / "batches").glob("*.json"))) == 1

def test_cumulative_counts_use_latest_attempt_instead_of_both_versions(tmp_path):
    from amazon_es_bestseller.collection.detail_batch_audit import DetailBatchAudit

    workspace = TaskWorkspace.create(tmp_path / "task", "latest", audit_batch_size=1)
    client = DetailBatchAudit(workspace, lambda _rows: None)
    asin = "B000000001"
    url = "https://www.amazon.es/dp/" + asin
    root = '<html><input id="ASIN" value="' + asin + '"><span id="productTitle">Bolso azul</span>'
    record = dict(asin=asin, requested_asin=asin, resolved_asin=asin, identity_status="MATCH",
                  title_es_raw="Bolso azul")

    def accept(markup, fields):
        raw = (root + markup + "</html>").encode()
        path, digest = client.ledger.evidence.save(raw)
        checkpoint = dict(status="success", record={**record, **fields},
                          capture_meta={"status_code": 200, "access_state": "NORMAL", "final_url": url},
                          html_evidence={"path": str(path), "sha256": digest})
        return client.accept(asin, checkpoint)

    assert accept("", {}) == "PASS"
    assert accept('<div id="feature-bullets"><ul><li>Dos bolsillos</li></ul></div>',
                  {"feature_bullets_raw": ["Dos bolsillos"]}) == "PASS"
    report = json.loads((workspace.root / "audit" / "field_completeness.json").read_bytes())
    field = report["feature_bullets_raw"]
    assert field["eligible_count"] == field["extracted_count"] == 1
    assert field["source_absent_count"] == field["parser_missed_count"] == 0
    missing_bullets = '<div id="feature-bullets"><ul><li>Tres bolsillos</li></ul></div>'
    assert accept(missing_bullets, {}) == "WARN"
    anomaly_file = workspace.root / "audit" / "anomaly_asins.json"
    [first] = json.loads(anomaly_file.read_bytes())
    assert first["first_seen_batch"] == first["batch_id"]
    assert accept(missing_bullets.replace("Tres", "Cuatro"), {}) == "WARN"
    [current] = json.loads(anomaly_file.read_bytes())
    assert current["first_seen_batch"] == first["batch_id"]
    assert current["batch_id"] != first["batch_id"]
    assert current["html_sha256"] != first["html_sha256"]
    from amazon_es_bestseller.evidence_store import read_evidence
    assert b"Cuatro bolsillos" in read_evidence(current["evidence_path"], current["html_sha256"])
    with workspace.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM audit_attempts").fetchone()[0] == 4
