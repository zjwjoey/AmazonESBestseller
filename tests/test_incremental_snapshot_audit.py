from copy import deepcopy

from amazon_es_bestseller.highspeed.incremental_audit import SnapshotPageAudit
from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
from amazon_es_bestseller.task_workspace import TaskWorkspace
from test_highspeed_v4_execution import FakeTransport, setup_scope


def test_single_page_replay_is_idempotent_and_new_bytes_are_rechecked(tmp_path, monkeypatch):
    from amazon_es_bestseller.highspeed import incremental_audit as module

    plan, approval = setup_scope(tmp_path / "fixture")
    workspace = TaskWorkspace.create(tmp_path / "task", "offline")
    audit = SnapshotPageAudit(workspace)
    source = plan["sources"][0]
    url = source["actual_source_url"]
    body = FakeTransport(plan, approval).fetch_page(url)["text"]

    def capture(html):
        path, _ = audit.ledger.evidence.save(html.encode())
        result = parse_v4_page(html, source=source, page_url=url, run_id=plan["run_id"],
                               snapshot_id=plan["snapshot_id"], observed_at="2026-10-14T14:10:00Z",
                               evidence_path=str(path), http_status=200, origin="OFFLINE_FIXTURE")
        return {**result["page"], "page_key": "fixture-page", "run_id": plan["run_id"], "snapshot_id": plan["snapshot_id"]}, result["observations"]

    checked = []
    real = module.audit_v4

    def count(pages, observations, sources, **kwargs):
        checked.append(len(pages))
        assert kwargs["include_history"] is False
        return real(pages, observations, sources, **kwargs)

    monkeypatch.setattr(module, "audit_v4", count)
    page, rows = capture(body)
    assert audit.check(plan, page, rows, require_live=False) in {"PASS", "WARN"}, audit.coverage(plan, [page])
    assert audit.check(plan, page, rows, require_live=False) in {"PASS", "WARN"}, audit.coverage(plan, [page])
    assert audit.coverage(plan, [page])["central_full_dom_review_passed"]
    changed, changed_rows = capture(body + "<!-- second capture -->")
    assert not audit.coverage(plan, [changed])["central_full_dom_review_passed"]
    assert audit.check(plan, changed, changed_rows, require_live=False) in {"PASS", "WARN"}
    assert checked == [1, 1]
    invalid = deepcopy(changed_rows)
    invalid[0]["product_title_es"] = "Título distinto de la evidencia"
    assert audit.check(plan, changed, invalid, require_live=False) == "FAIL"
    assert not audit.coverage(plan, [changed])["central_full_dom_review_passed"]
    assert checked == [1, 1, 1]
    assert audit.ledger.evidence.archive_stats(verify=True)["files"] == 2

def test_final_snapshot_receipt_reconciliation_uses_sealed_records_not_dom(tmp_path):
    from types import SimpleNamespace

    from amazon_es_bestseller.evidence_store import sha256
    from amazon_es_bestseller.multisource.storage import digest, sealed
    from amazon_es_bestseller.task_quality import _snapshot_receipt_errors
    import json

    plan = {"run_id": "fixture", "sources": []}
    directory = tmp_path / "runs" / plan["run_id"]
    (directory / "responses").mkdir(parents=True)
    (directory / "requests").mkdir()
    reservation = {"request_id": "request-1", "run_id": "fixture",
                   "plan_sha256": digest(plan), "source_id": "source", "kind": "PAGE",
                   "url": "https://www.amazon.es/list", "status": "NORMAL"}
    receipt = {"request_id": "request-1", "run_id": "fixture", "source_id": "source",
               "kind": "PAGE", "access_state": "NORMAL", "request_url": reservation["url"], "html_path": "read-prohibited.html"}
    with (directory / "requests" / "request-1.reserved.json").open("w", encoding="utf-8") as handle:
        json.dump(sealed(reservation), handle)
    path = directory / "responses" / "request-1.json"
    path.write_text(json.dumps(sealed(receipt)), encoding="utf-8")
    store = SimpleNamespace(root=tmp_path, iter_responses=lambda _run: iter([receipt]))
    pages = [{"page_key": "page", "capture_receipt_reference": {
        "path": str(path), "sha256": sha256(path.read_bytes())}}]
    assert _snapshot_receipt_errors(store, plan, pages, [reservation]) == []
    path.write_text(json.dumps(sealed({**receipt, "source_id": "other"})), encoding="utf-8")
    assert _snapshot_receipt_errors(store, plan, pages, [reservation]) == [
        "SNAPSHOT_RECEIPT_INVALID:request-1", "SNAPSHOT_PAGE_RECEIPT_INVALID:page",
    ]
