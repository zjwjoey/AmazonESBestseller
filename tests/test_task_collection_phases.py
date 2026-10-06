import json
from pathlib import Path

import pytest

from amazon_es_bestseller.collection import task as task_module
from amazon_es_bestseller.collection.task import _run_category_live, run_task
from amazon_es_bestseller.orchestration.worker import run_category_live
from amazon_es_bestseller.orchestration import scheduler as scheduler_module
from amazon_es_bestseller.orchestration.scheduler import _freeze_ranking_snapshot
from amazon_es_bestseller.quality.replay import audit_offline_replay
from amazon_es_bestseller.collection.ranking import parse_bestsellers_page


def _plan():
    return {
        "task_id": "phase-test", "batch_id": "phase-test", "target_unique": 2,
        "discovery_required": False, "sources_reviewed": True,
        "source_snapshot": str(Path(__file__).with_name("fixtures") / "task_source_snapshot.json"),
        "rank_end": 80, "pages_per_url": 2,
        "scheduler": {"mode": "parallel3", "max_parallel_categories": 3,
                      "cooldown_after_category_seconds": 0, "fallback_cooldown_seconds": 0},
        "categories": [
            {"research_category": "A", "target_unique": 1,
             "sources": [{"source_url": "https://www.amazon.es/gp/bestsellers/beauty/", "role": "primary"}]},
            {"research_category": "B", "target_unique": 1,
             "sources": [{"source_url": "https://www.amazon.es/gp/bestsellers/kitchen/", "role": "primary"}]},
        ],
    }


def _ranking_result(category):
    group = category["research_category"]
    asin = "A000000001" if group == "A" else "B000000001"
    url = category["sources"][0]["source_url"]
    return {
        "research_category": group, "status": "RANKING_COMPLETE",
        "rankings": [{"asin": asin, "research_category": group, "bestseller_rank": 1,
                      "ranking_source_url": url, "ranking_page_number": 1,
                      "source_role": "primary", "collection_batch": "phase-test",
                      "collection_time": "2026-10-06T00:00:00"}],
        "details": [], "completed_source_urls": [url], "source_status": {url: "completed"},
        "ranking_page_statuses": [{"source_url": url, "page_number": 1, "page_url": url,
                                   "access_state": "NORMAL", "http_status": 200,
                                   "parse_status": "PARSE_OK", "parsed_record_count": 1, "error": ""}],
        "raw_ranking_records": 1, "unique_asins": 1, "detail_records": 0,
        "pending_detail_asins": [], "detail_requested_asins": [],
    }


def test_ranking_phase_freezes_exact_candidates_and_never_calls_detail(monkeypatch, tmp_path):
    calls = []

    def fake_worker(category, plan, *args, **kwargs):
        calls.append(plan["collection_phase"])
        return _ranking_result(category)

    monkeypatch.setattr(task_module, "_run_category_live", fake_worker)
    report = run_task(_plan(), str(tmp_path / "run"), phase="ranking")

    assert calls == ["ranking", "ranking"]
    assert report["status"] == "COMPLETE"
    candidates = json.loads((tmp_path / "run" / "candidate_manifest.json").read_text(encoding="utf-8"))
    assert {row["asin"] for row in candidates} == {"A000000001", "B000000001"}
    assert all(row["ranking_contexts"] for row in candidates)
    assert len((tmp_path / "run" / "candidate_manifest.sha256").read_text(encoding="utf-8").strip()) == 64
    snapshot = tmp_path / "run" / report["ranking_snapshot_path"] / "manifest.json"
    assert json.loads(snapshot.read_text(encoding="utf-8"))["snapshot_status"] == "AUTHORITATIVE"
    resumed = run_task(_plan(), str(tmp_path / "run"), phase="ranking", resume=True)
    assert resumed["status"] == "COMPLETE"
    assert resumed["ranking_snapshot_path"] == report["ranking_snapshot_path"]


def test_detail_phase_only_consumes_frozen_candidates(monkeypatch, tmp_path):
    def ranking_worker(category, plan, *args, **kwargs):
        return _ranking_result(category)

    monkeypatch.setattr(task_module, "_run_category_live", ranking_worker)
    run_task(_plan(), str(tmp_path / "run"), phase="ranking")
    requested = []

    def detail_worker(category, plan, *args, **kwargs):
        assert plan["collection_phase"] == "detail"
        values = list(plan["frozen_candidates_by_category"][category["research_category"]])
        requested.extend(values)
        return {"research_category": category["research_category"], "status": "DETAIL_COMPLETE",
                "rankings": [], "details": [{"asin": values[0], "title_es_raw": "Producto"}],
                "completed_source_urls": [], "source_status": {}, "ranking_page_statuses": [],
                "raw_ranking_records": 1, "unique_asins": 1, "detail_records": 1,
                "pending_detail_asins": [], "detail_requested_asins": values}

    monkeypatch.setattr(task_module, "_run_category_live", detail_worker)
    report = run_task(_plan(), str(tmp_path / "run"), phase="detail")
    assert report["status"] == "COMPLETE"
    assert set(requested) == {"A000000001", "B000000001"}
    assert report["outside_candidate_requests"] == 0


def test_ranking_phase_reconciles_previous_details_and_writes_reextract_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(task_module, "_run_category_live", lambda category, plan, *args, **kwargs:
                        _ranking_result(category))
    previous = tmp_path / "previous_details.json"
    previous.write_text(json.dumps([{
        "asin": "A000000001", "title_es_raw": "Producto",
        "detail_schema_version": 2, "detail_parser_version": "collection.detail_v2",
        "identity_status": "MATCH",
    }]), encoding="utf-8")
    out = tmp_path / "run"
    report = run_task(_plan(), str(out), phase="ranking", previous_details=previous)

    assert report["status"] == "COMPLETE"
    assert report["detail_reuse_count"] == 1
    assert report["detail_reextract_count"] == 1
    reconciliation = json.loads((out / "detail_reconciliation.json").read_text(encoding="utf-8"))
    assert reconciliation["candidate_count"] == 2
    assert {row["ranking_asin"] for row in
            json.loads((out / "detail_reextract_queue.json").read_text(encoding="utf-8"))["records"]} == {
                "B000000001"}


def test_detail_phase_reuses_reconciled_cache_and_fetches_only_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(task_module, "_run_category_live", lambda category, plan, *args, **kwargs:
                        _ranking_result(category))
    previous = tmp_path / "previous_details.json"
    previous.write_text(json.dumps([{
        "asin": "A000000001", "title_es_raw": "Producto",
        "detail_schema_version": 2, "detail_parser_version": "collection.detail_v2",
        "identity_status": "MATCH",
    }]), encoding="utf-8")
    out = tmp_path / "run"
    run_task(_plan(), str(out), phase="ranking", previous_details=previous)
    requested = []

    def detail_worker(category, plan, *args, **kwargs):
        group = category["research_category"]
        fetch = list(plan["detail_fetch_asins_by_category"].get(group, []))
        reuse = list(plan["detail_reuse_records_by_category"].get(group, []))
        requested.extend(fetch)
        return {
            "research_category": group, "status": "DETAIL_COMPLETE",
            "rankings": [], "details": reuse +
                       [{"asin": asin, "title_es_raw": "Nuevo"} for asin in fetch],
            "completed_source_urls": [], "source_status": {}, "ranking_page_statuses": [],
            "raw_ranking_records": 1, "unique_asins": 1,
            "detail_records": len(reuse) + len(fetch),
            "pending_detail_asins": [], "detail_requested_asins": requested[-1:],
        }

    monkeypatch.setattr(task_module, "_run_category_live", detail_worker)
    report = run_task(_plan(), str(out), phase="detail")
    assert report["status"] == "COMPLETE"
    assert requested == ["B000000001"]
    assert report["detail_success"] == 2
    assert report["detail_reextract_count"] == 1


def test_ranking_worker_never_calls_detail(monkeypatch, tmp_path):
    class Session:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", Session)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery", lambda *_args: None)
    monkeypatch.setattr("amazon_es_bestseller.collection.detail.collect_details",
                        lambda *_args, **_kwargs: pytest.fail("ranking phase requested detail"))
    monkeypatch.setattr("amazon_es_bestseller.collection.ranking.collect_rankings", lambda urls, *_args, **_kwargs: [
        {"asin": "A000000001", "bestseller_rank": 1, "ranking_source_url": urls[0], "ranking_page_number": 1}])
    result = _run_category_live({"research_category": "A", "pages_per_url": 2,
                                 "sources": [{"source_url": "https://www.amazon.es/gp/bestsellers/beauty/"}]},
                                {"task_id": "x", "batch_id": "x", "pages_per_url": 2,
                                 "collection_phase": "ranking"}, tmp_path, 1, False, "",
                                lambda values: values, lambda _values: None, set())
    assert result["status"] == "RANKING_COMPLETE"
    assert result["detail_requested_asins"] == []


def test_detail_worker_does_not_run_ranking_and_resumes_only_pending(monkeypatch, tmp_path):
    class Session:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", Session)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery", lambda *_args: None)
    monkeypatch.setattr("amazon_es_bestseller.collection.ranking.collect_rankings",
                        lambda *_args, **_kwargs: pytest.fail("detail phase ran ranking"))
    calls = []
    monkeypatch.setattr("amazon_es_bestseller.collection.detail.collect_details",
                        lambda values, *_args, **_kwargs: calls.append(list(values)) or
                        [{"asin": asin, "title_es_raw": "Producto"} for asin in values])
    category = {"research_category": "A", "sources": []}
    plan = {"task_id": "x", "batch_id": "x", "collection_phase": "detail",
            "frozen_candidates_by_category": {"A": ["A000000001", "B000000001"]}}
    first = run_category_live(category, plan, tmp_path, 1, False, "", lambda values: values,
                              lambda _values: None, set())
    second = run_category_live(category, plan, tmp_path, 1, False, "", lambda values: values,
                               lambda _values: None, set())
    assert first["status"] == second["status"] == "DETAIL_COMPLETE"
    assert calls == [["A000000001", "B000000001"]]


def test_detail_worker_passes_frozen_ranking_url_and_context_to_collector(monkeypatch, tmp_path):
    class Session:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", Session)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery", lambda *_args: None)
    captured = {}

    def collector(values, *_args, **kwargs):
        captured["values"] = values
        captured["urls"] = kwargs["request_urls"]
        captured["context"] = kwargs["execution_context"]
        return [{"asin": values[0], "title_es_raw": "Producto"}]

    monkeypatch.setattr("amazon_es_bestseller.collection.detail.collect_details", collector)
    asin = "A000000001"
    plan = {
        "task_id": "x", "batch_id": "x", "collection_phase": "detail",
        "ranking_snapshot_id": "snapshot-1",
        "frozen_candidates_by_category": {"A": [asin]},
        "frozen_candidate_rows_by_category": {"A": [{
            "asin": asin, "ranking_product_url_raw": "/Producto/dp/B000000099/ref=ranking",
            "ranking_product_url_normalized": "https://www.amazon.es/dp/A000000001",
            "ranking_link_asin": "B000000099", "ranking_link_identity_status": "LINK_ASIN_MISMATCH",
            "ranking_source_url": "https://www.amazon.es/Best-Sellers/zgbs/123",
            "ranking_page_number": 1, "bestseller_rank": 7,
        }]},
    }
    result = run_category_live({"research_category": "A", "sources": []}, plan, tmp_path,
                               1, False, "", lambda values: values, lambda _values: None, set())
    assert result["status"] == "DETAIL_COMPLETE"
    assert captured["values"] == [asin]
    assert captured["urls"][asin] == "https://www.amazon.es/Producto/dp/B000000099/ref=ranking"
    assert captured["context"][asin]["request_source"] == "RANKING_RAW"
    assert captured["context"][asin]["ranking_link_asin"] == "B000000099"
    assert captured["context"][asin]["candidate_asin"] == asin
    assert captured["context"][asin]["snapshot_id"] == "snapshot-1"
    assert captured["context"][asin]["action"] == "FETCH_NEW"


def test_detail_worker_reuses_checkpoint_success_without_re_request(monkeypatch, tmp_path):
    class Session:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", Session)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery", lambda *_args: None)
    monkeypatch.setattr("amazon_es_bestseller.collection.detail.collect_details",
                        lambda *_args, **_kwargs: pytest.fail("cached success must not be re-requested"))
    asin = "A000000001"
    checkpoint = tmp_path / "categories" / "A" / "detail_cache" / "checkpoints" / f"{asin}.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(json.dumps({"asin": asin, "status": "success", "record": {
        "asin": asin, "title_es_raw": "Producto", "detail_status": "SUCCESS",
    }}), encoding="utf-8")
    plan = {"task_id": "x", "batch_id": "x", "collection_phase": "detail",
            "frozen_candidates_by_category": {"A": [asin]}}
    result = run_category_live({"research_category": "A", "sources": []}, plan, tmp_path,
                               1, False, "", lambda values: values, lambda _values: None, set())
    assert result["status"] == "DETAIL_COMPLETE"
    assert result["details"][0]["request_source"] == "ASIN_FALLBACK_LEGACY"


def test_detail_worker_retries_old_mismatch_and_network_failure_with_ranking_url(monkeypatch, tmp_path):
    class Session:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", Session)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery", lambda *_args: None)
    captured = {}
    monkeypatch.setattr("amazon_es_bestseller.collection.detail.collect_details",
                        lambda values, *_args, **kwargs: captured.update(kwargs) or
                        [{"asin": asin, "title_es_raw": "Producto"} for asin in values])
    mismatch, failed = "A000000001", "B000000001"
    root = tmp_path / "categories" / "A" / "detail_cache" / "checkpoints"
    root.mkdir(parents=True)
    (root / f"{mismatch}.json").write_text(json.dumps({
        "asin": mismatch, "status": "asin_mismatch", "attempt_count": 2}), encoding="utf-8")
    (root / f"{failed}.json").write_text(json.dumps({
        "asin": failed, "status": "failed", "attempt_count": 3}), encoding="utf-8")
    plan = {"task_id": "x", "batch_id": "x", "collection_phase": "detail",
            "frozen_candidates_by_category": {"A": [mismatch, failed]},
            "frozen_candidate_rows_by_category": {"A": [
                {"asin": mismatch, "ranking_product_url_raw": f"/x/dp/{mismatch}"},
                {"asin": failed, "ranking_product_url_raw": f"/x/dp/{failed}"},
            ]}}
    run_category_live({"research_category": "A", "sources": []}, plan, tmp_path,
                      1, False, "", lambda values: values, lambda _values: None, set())
    contexts = captured["execution_context"]
    assert contexts[mismatch]["action"] == "REEXTRACT_WITH_RANKING_URL"
    assert contexts[mismatch]["attempt"] == 3
    assert contexts[failed]["action"] == "RETRY_WITH_RANKING_URL"
    assert contexts[failed]["attempt"] == 4
    assert captured["request_urls"][mismatch].endswith("/dp/A000000001")
    assert captured["request_urls"][failed].endswith("/dp/B000000001")


def test_resume_rejects_plan_or_candidate_fingerprint_drift(monkeypatch, tmp_path):
    monkeypatch.setattr(task_module, "_run_category_live", lambda category, plan, *args, **kwargs: _ranking_result(category))
    plan = _plan()
    run_task(plan, str(tmp_path / "run"), phase="ranking")
    altered = _plan()
    altered["batch_id"] = "changed"
    with pytest.raises(ValueError, match="PLAN_FINGERPRINT_MISMATCH"):
        run_task(altered, str(tmp_path / "run"), phase="ranking")
    sidecar = tmp_path / "run" / "candidate_manifest.sha256"
    sidecar.write_text("0" * 64 + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CANDIDATE_FINGERPRINT_MISMATCH"):
        run_task(plan, str(tmp_path / "run"), phase="detail")


def test_ranking_shortfall_never_creates_candidates_or_details(monkeypatch, tmp_path):
    def duplicate_worker(category, plan, *args, **kwargs):
        result = _ranking_result(category)
        result["rankings"][0]["asin"] = "A000000001"
        return result

    monkeypatch.setattr(task_module, "_run_category_live", duplicate_worker)
    report = run_task(_plan(), str(tmp_path / "run"), phase="ranking")
    assert report["status"] == "QUOTA_UNIQUE_SHORTFALL"
    assert not (tmp_path / "run" / "candidate_manifest.json").exists()
    assert json.loads((tmp_path / "run" / "details.json").read_text(encoding="utf-8")) == []


def test_reserve_ranking_is_requested_only_after_primary_shortfall(monkeypatch, tmp_path):
    class Session:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    class Result(list):
        page_statuses = []

    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", Session)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery", lambda *_args: None)
    calls = []

    def rankings(urls, *_args, **_kwargs):
        calls.extend(urls)
        asin = "A000000001" if "primary" in urls[0] else "B000000001"
        return Result([{"asin": asin, "bestseller_rank": 1,
                        "ranking_source_url": urls[0], "ranking_page_number": 1}])

    monkeypatch.setattr("amazon_es_bestseller.collection.ranking.collect_rankings", rankings)
    sources = [{"source_url": "https://www.amazon.es/gp/bestsellers/primary/", "role": "primary"},
               {"source_url": "https://www.amazon.es/gp/bestsellers/reserve/", "role": "reserve"}]
    base = {"task_id": "x", "batch_id": "x", "collection_phase": "ranking", "pages_per_url": 1}
    run_category_live({"research_category": "A", "target_unique": 1, "sources": sources}, base,
                      tmp_path / "enough", 1, False, "", lambda values: values,
                      lambda _values: None, set())
    assert calls == [sources[0]["source_url"].rstrip("/")]
    calls.clear()
    run_category_live({"research_category": "A", "target_unique": 2, "sources": sources}, base,
                      tmp_path / "short", 1, False, "", lambda values: values,
                      lambda _values: None, set())
    assert calls == [sources[0]["source_url"].rstrip("/"), sources[1]["source_url"].rstrip("/")]


def test_frozen_snapshot_keeps_replay_discoverable_ranking_html(tmp_path):
    source_url = "https://www.amazon.es/Best-Sellers/zgbs/123"
    html = (Path(__file__).parent / "fixtures" / "html" / "bestsellers_grid.html").read_text(encoding="utf-8")
    rankings = parse_bestsellers_page(html, source_url, "2026-10-06T00:00:00")
    for row in rankings:
        row.update({"research_category": "A", "source_role": "primary"})
    html_path = tmp_path / "categories" / "A" / "runs" / "saved" / "html" / "ranking_000.html"
    html_path.parent.mkdir(parents=True)
    html_path.write_text(html, encoding="utf-8")
    snapshot = _freeze_ranking_snapshot(
        tmp_path, {**_plan(), "categories": [_plan()["categories"][0]], "target_unique": 1,
                   "canonical_plan_sha256": "f" * 64}, plan_path=None, project_root=None,
        rankings=rankings, statuses=[{"source_url": source_url, "page_number": 1,
                                      "access_state": "NORMAL", "http_status": 200,
                                      "parse_status": "PARSE_OK", "parsed_record_count": len(rankings)}])
    frozen_rows = json.loads((snapshot["path"] / "rankings.json").read_text(encoding="utf-8"))
    replay = audit_offline_replay(frozen_rows, [], run_dir=snapshot["path"])
    assert replay.summary["ranking_pages_replayed"] == 1
    assert not any(issue.issue_code == "REPLAY_EVIDENCE_MISSING" for issue in replay.issues)


def test_formal_5500_rejects_all_and_hash_drift_before_worker(monkeypatch, tmp_path):
    root = Path(__file__).parents[1]
    formal = json.loads((root / "configs/tasks/amazon_es_bestseller_5500_202610_plan.json").read_text(encoding="utf-8"))
    called = []
    monkeypatch.setattr(task_module, "_run_category_live", lambda *_args, **_kwargs: called.append(True))
    with pytest.raises(ValueError, match="FORMAL_5500_REQUIRES_EXPLICIT_PHASE"):
        run_task(formal, str(tmp_path / "all"), phase="all", project_root=root)
    altered = dict(formal, target_unique=5400)
    with pytest.raises(ValueError, match="FORMAL_5500_PLAN_HASH_MISMATCH"):
        run_task(altered, str(tmp_path / "bad"), phase="ranking", project_root=root,
                 runtime_overrides={"postal_code": "28001"})
    assert called == []


def test_formal_5500_runtime_overrides_follow_fingerprint_validation(monkeypatch, tmp_path):
    root = Path(__file__).parents[1]
    plan_path = root / "configs/tasks/amazon_es_bestseller_5500_202610_plan.json"
    formal = json.loads(plan_path.read_text(encoding="utf-8"))
    postal_codes = []
    # Preserve the formal plan bytes/fingerprint, but do not make this offline
    # regression test wait through the reviewed live-collection cooldown.
    monkeypatch.setattr(scheduler_module, "cooldown_seconds", lambda *_args: 0)

    def empty_ranking_worker(category, plan, *_args, **_kwargs):
        postal_codes.append(plan["postal_code"])
        return {
            "research_category": category["research_category"], "status": "RANKING_COMPLETE",
            "rankings": [], "details": [], "completed_source_urls": [], "source_status": {},
            "ranking_page_statuses": [], "raw_ranking_records": 0, "unique_asins": 0,
            "detail_records": 0, "pending_detail_asins": [], "detail_requested_asins": [],
        }

    monkeypatch.setattr(task_module, "_run_category_live", empty_ranking_worker)
    report = run_task(formal, str(tmp_path / "ranking"), phase="ranking",
                      plan_path=plan_path, project_root=root,
                      runtime_overrides={"postal_code": "28001"})
    assert report["status"] == "QUOTA_UNIQUE_SHORTFALL"
    assert postal_codes == ["28001"] * len(formal["categories"])
