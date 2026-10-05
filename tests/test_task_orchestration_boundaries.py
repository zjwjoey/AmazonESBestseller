from __future__ import annotations

import json
from pathlib import Path

from amazon_es_bestseller.collection import task as task_module
from amazon_es_bestseller.orchestration import checkpoint, manifest, plan, scheduler, state, worker


def _plan() -> dict:
    fixture = Path(__file__).with_name("fixtures") / "task_source_snapshot.json"
    return {
        "task_id": "boundary-test",
        "target_unique": 1,
        "discovery_required": False,
        "sources_reviewed": True,
        "source_snapshot": str(fixture),
        "rank_end": 80,
        "pages_per_url": 2,
        "scheduler": {
            "mode": "serial",
            "max_parallel_categories": 3,
            "cooldown_after_category_seconds": 0,
            "fallback_cooldown_seconds": 0,
        },
        "categories": [{
            "research_category": "A",
            "target_unique": 1,
            "sources": [{"source_url": "https://www.amazon.es/gp/bestsellers/beauty/"}],
        }],
    }


def test_task_compatibility_facade_delegates_to_extracted_scheduler(monkeypatch, tmp_path):
    """Old monkeypatch seams keep working after orchestration is extracted."""
    calls = []

    def fake_worker(*args, **kwargs):
        calls.append(args[0]["research_category"])
        return {
            "research_category": "A", "status": "COMPLETE",
            "rankings": [{
                "asin": "A000000001", "research_category": "A", "bestseller_rank": 1,
                "ranking_source_url": args[0]["sources"][0]["source_url"],
            }],
            "details": [{"asin": "A000000001"}],
            "completed_source_urls": [args[0]["sources"][0]["source_url"]],
            "source_status": {args[0]["sources"][0]["source_url"]: "completed"},
            "raw_ranking_records": 1, "unique_asins": 1, "detail_records": 1,
            "pending_detail_asins": [],
        }

    monkeypatch.setattr(task_module, "_run_category_live", fake_worker)
    report = task_module.run_task(_plan(), str(tmp_path / "run"), mode="serial")

    assert calls == ["A"]
    assert report["run_status"] == "COMPLETE"


def test_serial_scheduler_keeps_one_slot_and_processes_categories_in_plan_order(tmp_path):
    serial_plan = _plan()
    serial_plan["target_unique"] = 2
    serial_plan["categories"].append({
        "research_category": "B", "target_unique": 1,
        "sources": [{"source_url": "https://www.amazon.es/gp/bestsellers/kitchen/"}],
    })
    seen = []

    def fake_worker(category, _plan, _output, worker_id, *_args, **_kwargs):
        group = category["research_category"]
        seen.append((group, worker_id))
        asin = "A000000001" if group == "A" else "B000000001"
        source = category["sources"][0]["source_url"]
        return {
            "research_category": group, "status": "COMPLETE",
            "rankings": [{"asin": asin, "research_category": group,
                          "bestseller_rank": 1, "ranking_source_url": source}],
            "details": [{"asin": asin}], "completed_source_urls": [source],
            "source_status": {source: "completed"}, "raw_ranking_records": 1,
            "unique_asins": 1, "detail_records": 1, "pending_detail_asins": [],
        }

    report = scheduler.run_reviewed_task(serial_plan, str(tmp_path / "run"),
                                         mode="serial", worker=fake_worker)

    assert seen == [("A", 1), ("B", 1)]
    assert report["run_status"] == "COMPLETE"


def test_extracted_state_and_checkpoint_preserve_legacy_resume_shape(tmp_path):
    repository = checkpoint.TaskCheckpointRepository(tmp_path)
    (tmp_path / "batch_state_v2.json").write_text(json.dumps({
        "run_status": "RUNNING",
        "categories": {"A": {"status": "RUNNING", "attempts": 1}},
        "completed_source_urls": ["https://www.amazon.es/gp/bestsellers/beauty"],
        "claimed_asins": ["A000000001"],
    }), encoding="utf-8")

    runtime = state.TaskRuntimeState.load(repository, _plan(), mode="serial", slots=1)

    assert runtime.category_states["A"]["status"] == "PENDING"
    assert runtime.completed_source_urls == ["https://www.amazon.es/gp/bestsellers/beauty"]
    # Stale in-flight claims are deliberately released on restart so a
    # persisted pending-detail queue can retry without fetching ranking again.
    assert runtime.claim_asins(["A000000001", "B000000001"]) == ["A000000001", "B000000001"]


def test_extracted_modules_keep_distinct_runtime_responsibilities():
    assert plan.validate_task_plan is task_module.validate_task_plan
    assert worker.run_category_live is not task_module._run_category_live
    assert scheduler.run_reviewed_task is not task_module.run_task
    assert manifest.merge_records.__module__.endswith("orchestration.manifest")
