import json
from pathlib import Path

import pytest

from amazon_es_bestseller.canary import (
    CANARY_SCOPE_EXCEEDED,
    CANARY_RUNTIME_BUDGET_EXCEEDED,
    MAX_DETAIL_ASINS,
    CanaryProfile,
    CanaryScopeExceeded,
    CanaryRuntimeBudgetExceeded,
    RequestAccounting,
    build_canary_execution_plan,
    build_manifest,
    canonical_identity_status,
    compare_detail_replay,
    compare_replay_tuples,
    dry_run,
    gate_stage_a,
    gate_stage_b,
    gate_stage_c,
    sample_detail_asins,
    validate_canary_url,
    validate_scope,
)


def profile():
    return CanaryProfile()


def test_scope_limits_fail_closed_even_for_dry_run(tmp_path):
    with pytest.raises(CanaryScopeExceeded, match=CANARY_SCOPE_EXCEEDED):
        validate_scope(profile(), ["a", "b"], 1, [])
    with pytest.raises(CanaryScopeExceeded, match=CANARY_SCOPE_EXCEEDED):
        dry_run(profile(), ["a"], 3, [], tmp_path)
    with pytest.raises(CanaryScopeExceeded, match=CANARY_SCOPE_EXCEEDED):
        dry_run(profile(), ["a"], 1, ["B%09d" % i for i in range(6)], tmp_path)


def test_execution_plan_is_shared_scope_for_stage_a_and_full_canary():
    one = build_canary_execution_plan(profile(), ["https://www.amazon.es/zgbs/123"], 1)
    assert one.planned_stages == ("A",)
    assert one.planned_ranking_page_requests == 1
    assert one.planned_detail_page_requests == 0
    two = build_canary_execution_plan(profile(), ["https://www.amazon.es/gp/bestsellers/123"], 2)
    assert two.planned_stages == ("A", "B", "C")
    assert two.planned_ranking_page_requests == 3
    assert two.planned_detail_page_requests == 5


def test_profile_detail_limit_is_execution_limit_not_display_only():
    limited = CanaryProfile.from_mapping({"max_sources": 1, "max_pages_per_source": 2,
                                           "max_detail_asins": 3})
    plan = build_canary_execution_plan(limited, ["https://www.amazon.es/zgbs/123"], 2)
    assert plan.detail_limit == 3
    assert plan.planned_detail_page_requests == 3


def test_url_validation_accepts_only_amazon_es_bestseller_paths():
    assert validate_canary_url("https://www.amazon.es/zgbs/123")
    assert validate_canary_url("https://amazon.es/gp/bestsellers/xxx/123")
    for url in ("http://www.amazon.es/zgbs/123", "https://amazon.com/zgbs/123",
                "https://example.com/zgbs/123", "file:///tmp/x",
                "javascript:alert(1)"):
        with pytest.raises(CanaryScopeExceeded, match=CANARY_SCOPE_EXCEEDED):
            validate_canary_url(url)


def test_dry_run_has_zero_network_and_does_not_create_production_state(tmp_path):
    report = dry_run(profile(), ["https://www.amazon.es/zgbs/example"], 2, [], tmp_path)
    assert report["network_requests"] == 0
    assert report["actual_requests"]["total_requests"] == 0
    assert report["ranking_parser"] == "v2"
    assert report["transport"] == "PLAYWRIGHT"
    assert not list(tmp_path.rglob("*"))


def _page(page_number=1, **extra):
    row = {"page_number": page_number, "page_instance_id": f"p{page_number}",
           "ranking_source_url": "source", "ranking_page_url": f"page-{page_number}",
           "expected_count": 2, "server_rendered_count": 2,
           "acp_hydrated_count": 0, "page_authoritative": True,
           "page_complete": True, "access_state": "NORMAL"}
    row.update(extra)
    return row


def _manifest(pages):
    return {"final_authoritative": True, "access_state_summary": {"NORMAL": len(pages)},
            "ranking_complete": True,
            "ranking_identity_ready": True, "ranking_identity_complete": True,
            "ranking_slot_complete": True, "identity_conflict_count": 0,
            "authority_gates": {"identity_ready": True, "identity_complete": True,
                                "ranking_slots_complete": True},
            "ranking_v2_audit": {"pages": pages, "ranking_complete": True,
                                 "page_authoritative": True,
                                 "rank_gap_count": 0, "rank_duplicate_count": 0}}


def test_stage_a_and_b_gates_require_authority_and_replay():
    one = _manifest([_page()])
    assert gate_stage_a(one)["status"] == "PASS"
    two = _manifest([_page(), _page(2)])
    same = [{"asin": "B000000001", "ranking_rank": 1,
             "ranking_source_url": "s", "ranking_page_url": "p", "ranking_page_number": 1}]
    assert gate_stage_b(two, compare_replay_tuples(same, same))["status"] == "PASS"
    different = list(same)
    different[0] = {**different[0], "ranking_page_number": 2}
    assert gate_stage_b(two, compare_replay_tuples(same, different))["status"] == "FAIL"


def test_access_summary_unknown_or_blocked_fails_stage_a():
    blocked = _manifest([_page()])
    blocked["access_state_summary"] = {"NORMAL": 1, "BLOCKED": 1}
    assert gate_stage_a(blocked)["checks"]["access_normal"] is False
    unknown = _manifest([_page()])
    unknown["access_state_summary"] = {"UNKNOWN": 1}
    assert gate_stage_a(unknown)["status"] == "FAIL"


def test_stage_c_samples_from_snapshot_and_reports_unobserved_variation():
    rows = [{"asin": "B%09d" % i, "ranking_rank": i} for i in range(1, 8)]
    sampled, sampling = sample_detail_asins(rows)
    assert len(sampled) == MAX_DETAIL_ASINS
    assert sampling["variation_status"] == "VARIATION_CASE_NOT_OBSERVED"
    details = [{"asin": asin, "requested_asin": asin,
                "parser_version": "collection.detail_v2",
                "detail_parser_version": "collection.detail_v2",
                "detail_parser_contract_version": 2,
                "identity_status": "MATCH", "access_state": "NORMAL",
                "final_url": "https://www.amazon.es/dp/%s" % asin,
                "resolved_asin": asin, "parent_asin": "",
                "variation_family_asins": [],
                "ordered_detail_evidence": [], "category_evidence": {}}
               for asin in sampled]
    from amazon_es_bestseller.canary import gate_stage_c_with_replay
    assert gate_stage_c_with_replay(details, sampled, sampling, {"equal": True})["status"] == "PASS"


def test_access_stop_marker_and_request_accounting():
    accounting = RequestAccounting(1, 2, 3)
    assert accounting.to_dict() == {"location_requests": 0,
                                    "ranking_page_requests": 1,
                                    "acp_requests": 2,
                                    "detail_page_requests": 3,
                                    "total_requests": 6}
    # The public marker is intentionally stable for operators and automation.
    from amazon_es_bestseller.canary import CANARY_STOPPED_BY_ACCESS_GATE
    assert CANARY_STOPPED_BY_ACCESS_GATE == "CANARY_STOPPED_BY_ACCESS_GATE"


def test_runtime_budget_guard_stops_before_second_request():
    accounting = RequestAccounting(budget={"planned_location_requests": 0,
                                            "planned_ranking_page_requests": 1,
                                            "planned_acp_requests_max": 0,
                                            "planned_detail_page_requests": 0})
    accounting.reserve("ranking_page_requests")
    with pytest.raises(CanaryRuntimeBudgetExceeded, match=CANARY_RUNTIME_BUDGET_EXCEEDED):
        accounting.reserve("ranking_page_requests")
    assert accounting.ranking_page_requests == 1


def test_detail_replay_canonicalizes_identity_alias_but_not_relationship():
    online = [{"requested_asin": "B000000001", "resolved_asin": "B000000001",
               "identity_status": "MATCH", "ordered_detail_evidence": [],
               "category_evidence": {}}]
    offline = [{**online[0], "identity_status": "IDENTITY_MATCH"}]
    assert canonical_identity_status("EXACT_ASIN") == "IDENTITY_MATCH"
    assert compare_detail_replay(online, offline)["equal"] is True
    offline[0]["identity_status"] = "PARENT_ASIN_MATCH"
    assert compare_detail_replay(online, offline)["equal"] is False


def test_detail_replay_uses_v2_parser_and_online_normalization():
    from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2

    html = """
    <html><body><input id="ASIN" value="B000000001">
    <link rel="canonical" href="https://www.amazon.es/dp/B000000001">
    <span id="productTitle">Producto</span></body></html>
    """
    parsed = parse_detail_evidence_v2(
        html, "B000000001", requested_url="https://www.amazon.es/dp/B000000001")
    offline = dict(parsed)
    online = dict(parsed)
    online["identity_status"] = "MATCH"
    assert compare_detail_replay([online], [offline])["equal"] is True


def test_live_access_stop_blocks_later_stages_and_writes_blocked_manifest(tmp_path, monkeypatch):
    from amazon_es_bestseller.access.detector import AccessStopError
    from amazon_es_bestseller.canary import run_live

    class FakeSession:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", FakeSession)
    monkeypatch.setattr(
        "amazon_es_bestseller.monitoring.snapshot.collect_ranking_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AccessStopError("HTTP 403")),
    )
    report = run_live(profile(), ["https://www.amazon.es/zgbs/test"], 1, tmp_path,
                      git_sha="test-sha")
    assert report["final_status"] == "CANARY_BLOCKED_BY_ACCESS"
    assert report["stop_code"] == "CANARY_STOPPED_BY_ACCESS_GATE"
    assert report["stage_status"] == {"A": "BLOCKED_BY_ACCESS", "B": "NOT_RUN", "C": "NOT_RUN"}
    assert report["actual_requests"]["total_requests"] == 0


def test_live_stage_b_exception_is_recorded_as_fail_not_not_run(tmp_path, monkeypatch):
    from amazon_es_bestseller.canary import run_live

    class FakeSession:
        def __init__(self, **kwargs):
            pass
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False

    records = [{"asin": "B000000001", "ranking_asin": "B000000001",
                "ranking_rank": 1, "bestseller_rank": 1,
                "ranking_source_url": "https://www.amazon.es/zgbs/test",
                "ranking_page_url": "https://www.amazon.es/zgbs/test",
                "ranking_page_number": 1}]
    stage_a = {"manifest": _manifest([_page()]), "path": tmp_path / "unused",
               "records": records}
    calls = []
    def collect(*args, **kwargs):
        calls.append(kwargs.get("pages_per_url"))
        if len(calls) == 1:
            return stage_a
        raise RuntimeError("network failure")

    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", FakeSession)
    monkeypatch.setattr("amazon_es_bestseller.transport.playwright.PlaywrightTransport",
                        lambda session: object())
    monkeypatch.setattr("amazon_es_bestseller.monitoring.snapshot.collect_ranking_snapshot", collect)
    monkeypatch.setattr("amazon_es_bestseller.collection.ranking_v2.replay_ranking_snapshot_v2",
                        lambda path: {"records": records})
    report = run_live(profile(), ["https://www.amazon.es/zgbs/test"], 2, tmp_path)
    assert calls == [1, 2]
    assert report["stage_status"] == {"A": "PASS", "B": "FAIL", "C": "NOT_RUN"}
    assert report["final_status"] == "CANARY_FAILED"


def test_live_pages_one_does_not_execute_stage_b_or_c(tmp_path, monkeypatch):
    from amazon_es_bestseller.canary import run_live

    class FakeSession:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False

    records = [{"asin": "B000000001", "ranking_asin": "B000000001",
                "ranking_rank": 1, "bestseller_rank": 1,
                "ranking_source_url": "https://www.amazon.es/zgbs/test",
                "ranking_page_url": "https://www.amazon.es/zgbs/test",
                "ranking_page_number": 1}]
    calls = []
    result = {"manifest": _manifest([_page()]), "path": tmp_path / "unused",
              "records": records}
    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", FakeSession)
    monkeypatch.setattr("amazon_es_bestseller.transport.playwright.PlaywrightTransport",
                        lambda session: object())
    monkeypatch.setattr("amazon_es_bestseller.monitoring.snapshot.collect_ranking_snapshot",
                        lambda *args, **kwargs: calls.append(kwargs["pages_per_url"]) or result)
    monkeypatch.setattr("amazon_es_bestseller.collection.ranking_v2.replay_ranking_snapshot_v2",
                        lambda path: {"records": records})
    report = run_live(profile(), ["https://www.amazon.es/zgbs/test"], 1, tmp_path)
    assert calls == [1]
    assert report["planned_stages"] == ["A"]
    assert report["executed_stages"] == ["A"]
    assert report["stage_status"] == {"A": "PASS", "B": "NOT_RUN", "C": "NOT_RUN"}
    assert report["final_status"] == "CANARY_STAGE_A_PASS"


def test_live_stage_c_uses_profile_detail_limit(tmp_path, monkeypatch):
    from amazon_es_bestseller.canary import CanaryProfile, run_live

    limited = CanaryProfile.from_mapping({"max_sources": 1, "max_pages_per_source": 2,
                                          "max_detail_asins": 3})
    class FakeSession:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False

    def rows(count):
        return [{"asin": "B00000000%d" % i, "ranking_asin": "B00000000%d" % i,
                 "ranking_rank": i, "bestseller_rank": i,
                 "ranking_source_url": "https://www.amazon.es/zgbs/test",
                 "ranking_page_url": "https://www.amazon.es/zgbs/test?pg=%d" % i,
                 "ranking_page_number": 1 if i == 1 else 2}
                for i in range(1, count + 1)]

    ranking_records = rows(3)
    manifests = [_manifest([_page()]), _manifest([_page(), _page(2)])]
    calls = []
    def collect(*args, **kwargs):
        index = len(calls)
        calls.append(kwargs["pages_per_url"])
        return {"manifest": manifests[index], "path": tmp_path / ("snapshot%d" % index),
                "records": ranking_records}

    detail_rows = {}
    def make_detail_plan(snapshot, **kwargs):
        return {"records": [{"asin": row["asin"], "detail_action": "FETCH_NEW"}
                             for row in ranking_records], "snapshot_id": "snapshot"}

    def execute(plan, session, out_dir, **kwargs):
        assert len(plan["records"]) == 3
        html_dir = Path(out_dir) / "html"
        html_dir.mkdir(parents=True, exist_ok=True)
        details = []
        for row in plan["records"]:
            asin = row["asin"]
            (html_dir / (asin + ".html")).write_text("<html></html>", encoding="utf-8")
            detail = {"asin": asin, "requested_asin": asin,
                      "resolved_asin": asin, "parent_asin": "",
                      "variation_family_asins": [], "identity_status": "MATCH",
                      "detail_parser_version": "collection.detail_v2",
                      "detail_parser_contract_version": 2,
                      "ordered_detail_evidence": [], "category_evidence": {},
                      "access_state": "NORMAL", "final_url": "https://www.amazon.es/dp/%s" % asin,
                      "requested_url": "https://www.amazon.es/dp/%s" % asin}
            detail_rows[asin] = detail
            details.append(detail)
        return {"details": details}

    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", FakeSession)
    monkeypatch.setattr("amazon_es_bestseller.transport.playwright.PlaywrightTransport",
                        lambda session: object())
    monkeypatch.setattr("amazon_es_bestseller.monitoring.snapshot.collect_ranking_snapshot", collect)
    monkeypatch.setattr("amazon_es_bestseller.collection.ranking_v2.replay_ranking_snapshot_v2",
                        lambda path: {"records": ranking_records})
    monkeypatch.setattr("amazon_es_bestseller.monitoring.detail_planner.build_detail_plan",
                        make_detail_plan)
    monkeypatch.setattr("amazon_es_bestseller.collection.detail_executor.execute_detail_plan", execute)
    monkeypatch.setattr("amazon_es_bestseller.collection.detail_v2.parse_detail_evidence_v2",
                        lambda html, asin, **kwargs: dict(detail_rows[asin]))
    report = run_live(limited, ["https://www.amazon.es/zgbs/test"], 2, tmp_path)
    assert calls == [1, 2]
    assert report["request_budget"]["planned_detail_page_requests"] == 3
    assert report["stage_c_gate"]["checks"]["sample_count"] is True
    assert report["final_status"] == "CANARY_PASS"


def test_location_request_is_counted_once_for_idempotent_location_check():
    from amazon_es_bestseller.canary import _CountingSession

    class Delegate:
        _delivery_location_checked = False
        def ensure_spain_delivery(self):
            self._delivery_location_checked = True

    accounting = RequestAccounting(budget={"planned_location_requests": 1,
                                            "planned_ranking_page_requests": 0,
                                            "planned_acp_requests_max": 0,
                                            "planned_detail_page_requests": 0})
    wrapped = _CountingSession(Delegate(), accounting)
    wrapped.ensure_spain_delivery()
    wrapped.ensure_spain_delivery()
    assert accounting.location_requests == 1
    assert accounting.total_requests == 1


def test_cli_offline_real_canary_is_blocked_before_runner(monkeypatch):
    from amazon_es_bestseller.cli import main

    def should_not_run(*args, **kwargs):
        raise AssertionError("run_live must not be called under --offline")

    monkeypatch.setattr("amazon_es_bestseller.canary.run_live", should_not_run)
    with pytest.raises(SystemExit) as exc:
        main(["--offline", "canary", "--source-url", "https://www.amazon.es/zgbs/123",
              "--execute-real-amazon"])
    assert exc.value.code != 0


def test_cli_failed_live_canary_returns_nonzero(monkeypatch):
    from amazon_es_bestseller.cli import main

    monkeypatch.setattr("amazon_es_bestseller.canary.run_live",
                        lambda *args, **kwargs: {"final_status": "CANARY_FAILED"})
    with pytest.raises(SystemExit) as exc:
        main(["canary", "--source-url", "https://www.amazon.es/zgbs/123",
              "--execute-real-amazon"])
    assert exc.value.code == 4


def test_cli_only_full_canary_pass_returns_zero(monkeypatch):
    from amazon_es_bestseller.cli import main

    monkeypatch.setattr("amazon_es_bestseller.canary.run_live",
                        lambda *args, **kwargs: {"final_status": "CANARY_PASS"})
    assert main(["canary", "--source-url", "https://www.amazon.es/zgbs/123",
                 "--execute-real-amazon"]) == 0


def test_manifest_isolated_and_records_budget(tmp_path):
    run = tmp_path / "2026-10-04" / "run1"
    run.mkdir(parents=True)
    manifest = build_manifest(profile(), run, source_urls=["s"],
                              stage_status={"A": "PASS", "B": "NOT_RUN", "C": "NOT_RUN"},
                              accounting=RequestAccounting(), final_status="CANARY_FAILED")
    saved = json.loads((run / "canary_manifest.json").read_text(encoding="utf-8"))
    assert saved["request_budget"]["max_detail_asins"] == 5
    assert saved["actual_requests"]["total_requests"] == 0
    assert saved["ranking_parser_version"] == "v2"
    assert saved["detail_parser_version"] == "v2"
    assert saved["detail_v2_pass"] is False
    assert saved == manifest


def test_detail_replay_comparator_is_strict_on_identity_and_evidence():
    from amazon_es_bestseller.canary import compare_detail_replay

    online = [{"requested_asin": "B000000001", "resolved_asin": "B000000001",
               "identity_status": "MATCH", "ordered_detail_evidence": [],
               "category_evidence": {}}]
    offline = [dict(online[0])]
    assert compare_detail_replay(online, offline)["equal"] is True
    offline[0]["identity_status"] = "IDENTITY_REVIEW"
    assert compare_detail_replay(online, offline)["equal"] is False
