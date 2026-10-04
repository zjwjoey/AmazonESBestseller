import json
from pathlib import Path

import pytest

from amazon_es_bestseller.canary import (
    CANARY_SCOPE_EXCEEDED,
    MAX_DETAIL_ASINS,
    CanaryProfile,
    CanaryScopeExceeded,
    RequestAccounting,
    build_manifest,
    compare_replay_tuples,
    dry_run,
    gate_stage_a,
    gate_stage_b,
    gate_stage_c,
    sample_detail_asins,
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
           "page_complete": True}
    row.update(extra)
    return row


def _manifest(pages):
    return {"final_authoritative": True, "ranking_complete": True,
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
                "ordered_detail_evidence": [], "category_evidence": {}}
               for asin in sampled]
    from amazon_es_bestseller.canary import gate_stage_c_with_replay
    assert gate_stage_c_with_replay(details, sampled, sampling, {"equal": True})["status"] == "PASS"


def test_access_stop_marker_and_request_accounting():
    accounting = RequestAccounting(1, 2, 3)
    assert accounting.to_dict() == {"ranking_page_requests": 1,
                                    "acp_requests": 2,
                                    "detail_page_requests": 3,
                                    "total_requests": 6}
    # The public marker is intentionally stable for operators and automation.
    from amazon_es_bestseller.canary import CANARY_STOPPED_BY_ACCESS_GATE
    assert CANARY_STOPPED_BY_ACCESS_GATE == "CANARY_STOPPED_BY_ACCESS_GATE"


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
    report = run_live(profile(), ["https://example.invalid/source"], 1, tmp_path,
                      git_sha="test-sha")
    assert report["final_status"] == "CANARY_BLOCKED_BY_ACCESS"
    assert report["stop_code"] == "CANARY_STOPPED_BY_ACCESS_GATE"
    assert report["stage_status"] == {"A": "BLOCKED_BY_ACCESS", "B": "NOT_RUN", "C": "NOT_RUN"}
    assert report["actual_requests"]["total_requests"] == 0


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
