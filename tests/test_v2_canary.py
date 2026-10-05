from copy import deepcopy
from pathlib import Path

import pytest

from amazon_es_bestseller.production.canary import (
    CanaryProfile, CanaryScopeError, detail_diff, dry_run, evaluate_candidate,
    load_profile, seal_stage, shadow_diff, validate_scope,
)


URL = "https://www.amazon.es/zgbs/kitchen"
ASINS = ("B012345678", "B012345679")


def rankings():
    return [{"asin": asin, "bestseller_rank": index, "product_url": "https://www.amazon.es/dp/%s" % asin,
             "ranking_source_url": URL, "ranking_page_number": 1}
            for index, asin in enumerate(ASINS, 1)]


def details():
    return [{"asin": asin, "requested_asin": asin, "resolved_asin": asin, "identity_status": "IDENTITY_MATCH",
             "parent_asin": None, "variation_family_asins": [asin], "attributes": [{"label_raw": "Color"}],
             "category_evidence": {"source": "breadcrumb"}, "access_state": "NORMAL"}
            for asin in ASINS]


def stages(stable, candidate, detail_rows):
    a_input = {"stable_rankings": stable, "expected_count": len(stable)}
    a = seal_stage("A", a_input, {"access_state": "NORMAL", "expected_count": len(stable),
                                   "server_rendered_count": len(stable), "duplicate_asins": 0, "missing_slots": 0})
    b_input = {"candidate_rankings": candidate, "expected_count": len(stable)}
    b = seal_stage("B", b_input, {"shadow_equal": True})
    c_input = {"details": detail_rows, "detail_replay": deepcopy(detail_rows)}
    c = seal_stage("C", c_input, {"detail_replay_equal": True, "details": detail_rows, "replay_equal": True})
    return a, b, c


def candidate(**extra):
    stable = rankings(); v2 = deepcopy(stable); rows = details(); a, b, c = stages(stable, v2, rows)
    value = {"profile": CanaryProfile(), "stable_rankings": stable, "candidate_rankings": v2,
             "expected_count": 2, "stage_a": a, "stage_b": b, "stage_c": c,
             "candidate_replay_rankings": deepcopy(v2), "details": rows, "detail_replay": deepcopy(rows),
             "request_count": 0, "access_state": "NORMAL"}
    value.update(extra)
    return value


def test_profile_scope_and_offline_dry_run_are_strict():
    profile = load_profile(Path("configs/canary/amazon_es_v2_canary_v2.json"))
    plan = dry_run(profile, [URL], pages_per_source=2, detail_asins=ASINS)
    assert plan["network_requests"] == 0 and not plan["default_parser_changed"]
    with pytest.raises(CanaryScopeError):
        validate_scope(profile, [URL, URL], pages_per_source=2)
    with pytest.raises(CanaryScopeError):
        validate_scope(profile, [URL], pages_per_source=2, task_id="amazon_es_bestseller_5000_202610")
    with pytest.raises(CanaryScopeError):
        CanaryProfile.from_mapping({"enabled": True})


def test_shadow_and_detail_diff_include_identity_parent_attributes_and_evidence():
    stable = rankings(); changed = deepcopy(stable); changed[1]["bestseller_rank"] = 3
    diff = shadow_diff(stable, changed, expected_count=2)
    assert not diff["equal"] and ASINS[1] in diff["changed_asins"]
    live = details(); replay = deepcopy(live); replay[0]["parent_asin"] = "B000000000"
    assert not detail_diff(live, replay)["equal"]


def test_complete_bound_offline_fixture_is_promotable_but_never_promoted():
    result = evaluate_candidate(**candidate())
    assert result["status"] == "PROMOTABLE"
    assert result["promoted"] is False
    assert result["default_parser_changed"] is False


def test_missing_or_stale_stage_challenge_and_network_stop_candidate():
    value = candidate(); value["stage_b"] = None
    assert "STAGE_B_STAGE_MISSING_OR_VERSION_INVALID" in evaluate_candidate(**value)["findings"]
    value = candidate(); value["stage_a"]["evidence"]["expected_count"] = 999
    assert "STAGE_A_STAGE_HASH_INVALID" in evaluate_candidate(**value)["findings"]
    value = candidate(access_state="CHALLENGE")
    assert "ACCESS_STOP_ALL" in evaluate_candidate(**value)["findings"]
    value = candidate(request_count=1)
    assert "NETWORK_REQUESTS_NOT_ALLOWED_BY_OFFLINE_EVALUATOR" in evaluate_candidate(**value)["findings"]


def test_duplicate_missing_slots_and_detail_identity_review_block_candidate():
    value = candidate()
    duplicate = deepcopy(value["candidate_rankings"]); duplicate.append(deepcopy(duplicate[0]))
    value["candidate_rankings"] = duplicate
    value["candidate_replay_rankings"] = deepcopy(duplicate)
    value["stage_b"] = seal_stage("B", {"candidate_rankings": duplicate, "expected_count": 2}, {"shadow_equal": True})
    assert "STAGE_B_SHADOW_OR_REPLAY_DIFF" in evaluate_candidate(**value)["findings"]
    value = candidate(); bad = deepcopy(value["details"]); bad[0]["identity_status"] = "IDENTITY_UNCONFIRMED"
    value["details"] = bad
    value["detail_replay"] = deepcopy(bad)
    value["stage_c"] = seal_stage("C", {"details": bad, "detail_replay": bad},
                                  {"detail_replay_equal": True, "details": bad, "replay_equal": True})
    assert "STAGE_C_DETAIL_OR_REPLAY_DIFF" in evaluate_candidate(**value)["findings"]


def test_detail_budget_and_candidate_replay_mismatch_block_even_with_fresh_evidence():
    value = candidate()
    extras = []
    for suffix in range(4):
        extra = deepcopy(value["details"][0]); extra["asin"] = extra["requested_asin"] = extra["resolved_asin"] = "B01234567%s" % suffix
        extras.append(extra)
    value["details"] = value["details"] + extras
    value["detail_replay"] = deepcopy(value["details"])
    value["stage_c"] = seal_stage("C", {"details": value["details"], "detail_replay": value["detail_replay"]},
                                  {"detail_replay_equal": True, "details": value["details"], "replay_equal": True})
    assert "DETAIL_BUDGET_EXCEEDED" in evaluate_candidate(**value)["findings"]

    value = candidate(); value["candidate_replay_rankings"][0]["product_url"] += "?replay-drift"
    value["stage_c"] = value["stage_c"]
    assert "STAGE_B_SHADOW_OR_REPLAY_DIFF" in evaluate_candidate(**value)["findings"]
