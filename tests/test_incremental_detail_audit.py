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
