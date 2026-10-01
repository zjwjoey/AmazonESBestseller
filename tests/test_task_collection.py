import json
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.quota import QuotaError, select_research_quota
from amazon_es_bestseller.collection.discovery import parse_bestseller_navigation
from amazon_es_bestseller.collection.task import (_cooldown_seconds, _needs_reserve_sources,
                                                   _run_category_live, run_task, validate_task_plan)
from amazon_es_bestseller.export.excel import export_workbook
from amazon_es_bestseller import cli


def _plan():
    return {
        "task_id": "test-5000",
        "target_unique": 2,
        "discovery_required": False,
        "sources_reviewed": True,
        "source_snapshot": str(Path(__file__).with_name("fixtures") / "task_source_snapshot.json"),
        "rank_end": 80,
        "pages_per_url": 2,
        "scheduler": {"mode": "parallel3", "max_parallel_categories": 3,
                       "cooldown_after_category_seconds": 600,
                       "fallback_cooldown_seconds": 1800},
        "categories": [
            {"research_category": "A", "target_unique": 1,
             "sources": [{"source_url": "https://www.amazon.es/gp/bestsellers/beauty/"}]},
            {"research_category": "B", "target_unique": 1,
             "sources": [{"source_url": "https://www.amazon.es/gp/bestsellers/kitchen/"}]},
        ],
    }


def test_reviewed_task_plan_requires_real_sources_and_three_slots():
    plan = validate_task_plan(_plan())
    assert plan["scheduler"]["max_parallel_categories"] == 3
    assert plan["scheduler"]["cooldown_after_category_seconds"] == 600
    assert _cooldown_seconds("parallel3", plan["scheduler"]) == 600
    assert _cooldown_seconds("serial", plan["scheduler"]) == 1800
    assert all(row["pages_per_url"] == 2 for row in plan["categories"])


def test_reserve_source_preflight_uses_unique_and_source_spread():
    category = {"target_unique": 2, "max_single_source_share": 0.5}
    assert _needs_reserve_sources(category, [{"asin": "A", "ranking_source_url": "u1"}])
    assert not _needs_reserve_sources(category, [
        {"asin": "A", "ranking_source_url": "u1"},
        {"asin": "B", "ranking_source_url": "u2"},
    ])


def test_task_and_discovery_commands_are_registered():
    parser = cli.build_parser()
    task_args = parser.parse_args(["task-collect", "--plan", "p.json", "--out-dir", "out"])
    tree_args = parser.parse_args(["discover-tree", "--urls", "https://www.amazon.es/gp/bestsellers/",
                                   "--out-dir", "out"])
    assert task_args.command == "task-collect"
    assert task_args.mode is None
    assert tree_args.command == "discover-tree"


def test_reviewed_task_plan_rejects_duplicate_source():
    plan = _plan()
    plan["categories"][1]["sources"] = plan["categories"][0]["sources"]
    with pytest.raises(ValueError, match="榜单来源重复"):
        validate_task_plan(plan)


def test_reviewed_task_plan_rejects_source_outside_snapshot():
    plan = _plan()
    plan["categories"][0]["sources"][0]["source_url"] = "https://www.amazon.es/gp/bestsellers/toys/"
    with pytest.raises(ValueError, match="source_snapshot"):
        validate_task_plan(plan)


def test_reviewed_task_plan_requires_review_marker_and_second_page_for_1_to_80():
    plan = _plan()
    plan["sources_reviewed"] = False
    with pytest.raises(ValueError, match="sources_reviewed"):
        validate_task_plan(plan)
    plan = _plan()
    plan["pages_per_url"] = 1
    with pytest.raises(ValueError, match="pages_per_url"):
        validate_task_plan(plan)


def test_reviewed_task_plan_requires_saved_html_snapshot_evidence(tmp_path):
    plan = _plan()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({
        "pages": [{"source_url": "https://www.amazon.es/gp/bestsellers/beauty",
                   "http_status": 200, "html_file": "html/missing.html"}],
        "page_count": 1, "links": [], "link_count": 0,
    }), encoding="utf-8")
    plan["source_snapshot"] = str(snapshot)
    with pytest.raises(ValueError, match="原始 HTML"):
        validate_task_plan(plan)


def test_research_selector_prefers_rare_shared_asins_and_enforces_source_cap():
    categories = [
        {"research_category": "A", "target_unique": 2, "max_single_source_share": 0.5},
        {"research_category": "B", "target_unique": 1, "max_single_source_share": 1.0},
    ]
    records = [
        {"asin": "AONLY", "research_category": "A", "bestseller_rank": 5,
         "ranking_source_url": "https://www.amazon.es/gp/bestsellers/a/"},
        {"asin": "SHARED", "research_category": "A", "bestseller_rank": 1,
         "ranking_source_url": "https://www.amazon.es/gp/bestsellers/a/"},
        {"asin": "SHARED", "research_category": "B", "bestseller_rank": 1,
         "ranking_source_url": "https://www.amazon.es/gp/bestsellers/b/"},
        {"asin": "BONLY", "research_category": "B", "bestseller_rank": 2,
         "ranking_source_url": "https://www.amazon.es/gp/bestsellers/b/"},
        {"asin": "A2", "research_category": "A", "bestseller_rank": 8,
         "ranking_source_url": "https://www.amazon.es/gp/bestsellers/a2/"},
    ]
    selected = select_research_quota(records, categories, target_unique=3)
    assert {row["asin"] for row in selected["A"]} == {"AONLY", "A2"}
    assert selected["B"][0]["asin"] == "BONLY"


def test_research_selector_reports_global_shortfall():
    categories = [{"research_category": "A", "target_unique": 2}]
    with pytest.raises(QuotaError, match="QUOTA_UNIQUE_SHORTFALL"):
        select_research_quota([{"asin": "ONLY", "research_category": "A"}], categories,
                              target_unique=2)


def test_discovery_parser_preserves_real_node_and_url_evidence():
    html = """
    <html><head><title>Amazon Best Sellers</title></head><body>
      <a href='/gp/bestsellers/kitchen/2165553031/'>Kitchen Tools</a>
      <a href='/gp/bestsellers/kitchen/2165553031/?pg=2'>duplicate page</a>
      <a href='https://example.com/search'>ignore</a>
    </body></html>
    """
    result = parse_bestseller_navigation(
        html, "https://www.amazon.es/gp/bestsellers/kitchen/")
    assert result["links"] == [{
        "name": "Kitchen Tools", "url": "https://www.amazon.es/gp/bestsellers/kitchen/2165553031",
        "browse_node_id": "2165553031",
        "parent_url": "https://www.amazon.es/gp/bestsellers/kitchen/",
    }]


def test_parallel_task_runner_writes_manifest_and_report(monkeypatch, tmp_path):
    plan = _plan()

    def fake_category(category, plan, output, worker_id, headful, profile_dir,
                      claim_asins, release_asins, completed_urls, stop_event=None):
        group = category["research_category"]
        asin = "A000000001" if group == "A" else "B000000001"
        return {
            "research_category": group,
            "status": "COMPLETE",
            "rankings": [{"asin": asin, "research_category": group,
                          "bestseller_rank": 1,
                          "ranking_source_url": category["sources"][0]["source_url"]}],
            "details": [{"asin": asin, "title_es_raw": "producto"}],
            "completed_source_urls": [category["sources"][0]["source_url"]],
            "raw_ranking_records": 1,
            "unique_asins": 1,
            "detail_records": 1,
        }

    monkeypatch.setattr("amazon_es_bestseller.collection.task._run_category_live", fake_category)
    report = run_task(plan, str(tmp_path / "run"), mode="parallel3")
    assert report["run_status"] == "COMPLETE"
    assert report["final_unique_asins"] == 2
    assert json.loads((tmp_path / "run" / "final_manifest.json").read_text(encoding="utf-8"))
    assert (tmp_path / "run" / "category_summary.csv").exists()
    assert (tmp_path / "run" / "run_report.json").exists()


def test_task_export_keeps_core_sheets_and_adds_provenance_sheet():
    wb = export_workbook([{
        "asin": "A000000001", "title_es_raw": "Producto",
        "research_category": "居家生活", "collection_batch": "batch-1",
        "collection_time": "2026-10-01T12:00:00",
    }], profile="task")
    assert wb.sheetnames == ["类目规划", "西班牙语选品清单", "中文选品清单", "采集任务元数据"]
    assert [cell.value for cell in wb["采集任务元数据"][2]] == [
        "A000000001", "居家生活", "batch-1", "2026-10-01T12:00:00"
    ]


def test_category_detail_failure_is_persisted_and_retried(monkeypatch, tmp_path):
    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            self.challenge_wait_seconds = 0
            self.manual_assist = False
            return self

        def __exit__(self, *args):
            return False

    detail_calls = []
    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", FakeSession)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery",
                        lambda session, postal_code="28001": None)
    monkeypatch.setattr("amazon_es_bestseller.collection.ranking.collect_rankings",
                        lambda urls, session, out_dir, pages_per_url=1: [{
                            "asin": "A000000001", "bestseller_rank": 1,
                            "ranking_source_url": urls[0], "ranking_page_number": 1,
                        }])

    def fake_details(asins, session, out_dir):
        detail_calls.append(list(asins))
        return [] if len(detail_calls) == 1 else [{"asin": asins[0], "title_es_raw": "Producto"}]

    monkeypatch.setattr("amazon_es_bestseller.collection.detail.collect_details", fake_details)
    category = {"research_category": "A", "pages_per_url": 2,
                "sources": [{"source_url": "https://www.amazon.es/gp/bestsellers/a/"}]}
    plan = {"task_id": "retry", "batch_id": "retry", "pages_per_url": 2}
    claimed = set()

    def claim(values):
        fresh = [value for value in values if value not in claimed]
        claimed.update(fresh)
        return fresh

    def release(values):
        for value in values:
            claimed.discard(value)

    first = _run_category_live(category, plan, tmp_path, 1, False, "",
                               claim, release, set())
    assert first["status"] == "DETAIL_INCOMPLETE"
    assert first["pending_detail_asins"] == ["A000000001"]

    second = _run_category_live(category, plan, tmp_path, 1, False, "",
                                claim, release, set())
    assert second["status"] == "COMPLETE"
    assert second["pending_detail_asins"] == []
    assert len(detail_calls) == 2


def test_task_completion_gate_rejects_missing_selected_details(monkeypatch, tmp_path):
    plan = _plan()
    plan["target_unique"] = 1
    plan["categories"] = [plan["categories"][0]]
    plan["scheduler"]["cooldown_after_category_seconds"] = 0
    plan["scheduler"]["fallback_cooldown_seconds"] = 0

    def incomplete_category(category, *args, **kwargs):
        return {
            "research_category": "A", "status": "DETAIL_INCOMPLETE",
            "rankings": [{"asin": "A000000001", "research_category": "A",
                          "bestseller_rank": 1,
                          "ranking_source_url": category["sources"][0]["source_url"]}],
            "details": [], "completed_source_urls": [],
            "raw_ranking_records": 1, "unique_asins": 1, "detail_records": 0,
            "pending_detail_asins": ["A000000001"],
        }

    monkeypatch.setattr("amazon_es_bestseller.collection.task._run_category_live",
                        incomplete_category)
    report = run_task(plan, str(tmp_path / "run"), mode="parallel3")
    assert report["run_status"] == "DETAIL_INCOMPLETE"
    assert report["final_unique_asins"] == 0
    assert json.loads((tmp_path / "run" / "final_manifest.json").read_text(encoding="utf-8")) == []
