import gzip
import hashlib
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.quick_selection import select_candidates
from amazon_es_bestseller.collection.detail_bridge5500 import (
    DetailBridge5500, cache_index, create_plan, find_valid_cache, parse_candidate_detail, safe_url)
from amazon_es_bestseller.multisource.storage import digest, immutable_json, read_sealed, sealed


def candidate(cid="a", rating=4.8, count=500, group=None):
    return dict(candidate_id=cid, observation_id=cid, product_title_es="Producto",
                absolute_product_url=f"https://www.amazon.es/dp/B00000000{cid.upper()}?psc=1",
                raw_product_url=f"/dp/B00000000{cid.upper()}?psc=1",
                normalized_product_url=f"https://www.amazon.es/dp/B00000000{cid.upper()}?psc=1",
                rating_value=rating, rating_status="VALID", rating_count=count, rating_count_status="VALID",
                target_review_group=group or cid, source_memberships=[dict(observation_id=cid,
                    source_type="BEST_SELLERS", source_url="https://www.amazon.es/gp/bestsellers/kitchen/1",
                    rank_context="kitchen/1", ranking_position=1, ranking_semantics="BESTSELLER_SALES_RANKING")])


def cert(*ids):
    return dict(certified_observation_ids=list(ids), source_snapshot_id="real_snapshot", manifest_sha256="0" * 64)


def test_rating_first_and_repeatable_no_global_rank():
    rows = [candidate("a", 4.8, 20), candidate("b", 4.9, 3), candidate("c", 4.8, 30)]
    first = select_candidates(rows, cert("a", "b", "c"), selected_count=2)
    second = select_candidates(list(reversed(rows)), cert("a", "b", "c"), selected_count=2)
    assert [r["candidate_id"] for r in first[0]] == ["b", "c"]
    assert first[:2] == second[:2]


def test_missing_not_low_rating_and_real_shortfall():
    rows = [candidate("a", None), candidate("b", 2.9), candidate("c", 3.0)]
    selected, _, report, review, excluded = select_candidates(rows, cert("a", "b", "c"), selected_count=3)
    assert [r["candidate_id"] for r in selected] == ["c"]
    assert report["shortfall"] == 2 and len(review) == 1
    assert excluded[0]["reasons"] == ["CONFIRMED_RATING_BELOW_3"]


def test_conflicting_rating_goes_review():
    row = candidate()
    row["observation_conflicts"] = {"rating_value": [4.8, 2.0]}
    assert len(select_candidates([row], cert("a"))[3]) == 1


def test_no_title_guess_and_parent_not_dedup_key():
    a, b = candidate("a"), candidate("b")
    a.update(product_title_es="Libro de ejercicios para perro", parent_asin="B0PARENT00")
    b.update(parent_asin="B0PARENT00")
    assert len(select_candidates([a, b], cert("a", "b"))[0]) == 2


def test_conservative_group_not_double_counted():
    a, b = candidate("a", group="same"), candidate("b", group="same")
    result = select_candidates([a, b], cert("a", "b"))
    assert len(result[0]) == 1 and result[2]["duplicate_group_count"] == 1


def test_bad_source_membership_not_whitewashed():
    a = candidate()
    a["source_memberships"].append({"observation_id": "bad"})
    assert not select_candidates([a], cert("a"))[0]


@pytest.mark.parametrize("route", ["books", "digital-text", "gift-cards", "mobile-apps"])
def test_explicit_nonphysical_source_excluded(route):
    a = candidate()
    a["source_memberships"][0]["source_url"] = f"https://www.amazon.es/gp/bestsellers/{route}/1"
    assert not select_candidates([a], cert("a"))[0]


@pytest.mark.parametrize("url", ["https://amazon.es.evil.test/dp/B000000001", "http://www.amazon.es/dp/B000000001",
    "https://user@www.amazon.es/dp/B000000001", "https://www.amazon.es:444/dp/B000000001"])
def test_unsafe_url_rejected(url):
    assert not safe_url(url)


def task():
    url = "https://www.amazon.es/dp/B0GK9PLT15?th=1"
    return dict(candidate_id="real", selection_id="sel_real", raw_product_url=url,
        normalized_product_url=url, request_url=url, selection_order=1, source_snapshot_id="snapshot",
        source_memberships=[], run_id="test", task_id="detail_real")


@pytest.fixture(scope="module")
def real_page():
    root = Path(__file__).parent / "fixtures/detail_bridge5500"
    raw = gzip.decompress((root / "actual_product.html.gz").read_bytes())
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf8"))
    assert hashlib.sha256(raw).hexdigest() == provenance["sha256"]
    return raw.decode("utf8"), provenance["metadata"]


def test_real_saved_page_v2_fields_and_true_url(real_page):
    html, meta = real_page
    result = parse_candidate_detail(task(), html, meta)
    assert result["status"] == "SUCCESS"
    record = result["record"]
    assert record["title_es_raw"] and record["product_url"] == meta["final_url"]
    assert isinstance(record["ordered_detail_evidence"], list)
    assert record["asin"] == "B0GK9PLT15"


def test_real_page_wrong_requested_child_even_same_parent_rejected(real_page):
    html, meta = real_page
    t = task(); t["request_url"] = "https://www.amazon.es/dp/B000000001"
    assert parse_candidate_detail(t, html, meta)["status"] == "IDENTITY_REVIEW"


@pytest.mark.parametrize("code", [403, 429, None, 503])
def test_access_states_stop_before_parsing(real_page, code):
    html, meta = real_page
    assert parse_candidate_detail(task(), html, {**meta, "status_code": code})["status"] == "ACCESS_STOP"


def test_http200_challenge_stops(real_page):
    _, meta = real_page
    assert parse_candidate_detail(task(), "<html>Robot Check</html>", meta)["status"] == "ACCESS_STOP"


def test_recommendation_asin_not_active_identity(real_page):
    _, meta = real_page
    html = '<div id="productTitle">Recommended</div><div data-asin="B0GK9PLT15"></div>'
    assert parse_candidate_detail(task(), html, meta)["status"] == "IDENTITY_REVIEW"


def test_missing_asin_real_url_can_be_processed_without_inventing_it(real_page):
    html, _ = real_page
    t = task(); t["request_url"] = t["normalized_product_url"] = "https://www.amazon.es/product/opaque-key"
    meta = dict(status_code=200, final_url=t["request_url"])
    result = parse_candidate_detail(t, html, meta)
    assert result["status"] == "SUCCESS" and result["record"]["asin"] == "B0GK9PLT15"


def test_plan_real_url_preserved_and_no_parent_merge():
    selected = select_candidates([candidate("a"), candidate("b")], cert("a", "b"))[0]
    plan = create_plan(selected, run_id="new", selection_sha256="x", source_certificate_sha256="y", shared_gate_path="gate")
    assert plan["tasks"][0]["request_url"] == selected[0]["absolute_product_url"]
    assert len(plan["tasks"]) == 2 and plan["parallelism"] == 1
    selected[0]["absolute_product_url"] = "https://www.amazon.es/dp/B000000009"
    with pytest.raises(ValueError, match="REAL_SNAPSHOT"):
        create_plan(selected, run_id="new", selection_sha256="x", source_certificate_sha256="y", shared_gate_path="gate")


def test_budget_requires_separate_new_authorization(tmp_path):
    bridge = DetailBridge5500(tmp_path, {"tasks": [], "run_id": "new"})
    with pytest.raises(ValueError, match="INDEPENDENT_DETAIL"):
        bridge.validate_authorization({"approved": True, "scope": "old_ranking_6000"})


def test_state_per_candidate_not_overwritten_and_success_hash_guard(tmp_path, real_page):
    html, meta = real_page
    t1, t2 = task(), {**task(), "task_id": "other", "candidate_id": "other"}
    plan = {"tasks": [t1, t2], "run_id": "test"}
    bridge = DetailBridge5500(tmp_path, plan)
    hp = tmp_path / "real.html"; hp.write_text(html, encoding="utf8")
    mp = tmp_path / "meta.json"; mp.write_text(json.dumps(meta), encoding="utf8")
    for t in (t1, t2):
        result = parse_candidate_detail(t, html, meta)
        result.update(status="REUSED_VALID_CACHE", html_path=str(hp), html_sha256=hashlib.sha256(hp.read_bytes()).hexdigest(),
                      cache_metadata_path=str(mp), cache_metadata_sha256=hashlib.sha256(mp.read_bytes()).hexdigest())
        bridge.save_result(t, result)
    assert bridge.progress()["success"] == 2
    old = read_sealed(tmp_path / "tasks/detail_real/checkpoint.json")
    bridge.verify_saved_success(t1, old)
    hp.write_text("changed", encoding="utf8")
    with pytest.raises(ValueError, match="SUCCESS_EVIDENCE"):
        bridge.verify_saved_success(t1, old)


def test_stage_cannot_be_skipped(tmp_path):
    bridge = DetailBridge5500(tmp_path, {"tasks": [], "run_id": "new"})
    with pytest.raises((OSError, ValueError)):
        bridge.validate_stage("T1", 30)


def test_real_cache_requires_exact_request_url_and_normal_metadata(tmp_path, real_page):
    html, meta = real_page
    p = tmp_path / "B0GK9PLT15.html"; p.write_text(html, encoding="utf8")
    mp = p.with_suffix(".meta.json")
    mp.write_text(json.dumps({**meta, "requested_url": task()["request_url"]}), encoding="utf8")
    index = cache_index([tmp_path])
    assert task()["normalized_product_url"] in index
    # A changed URL is never matched merely by ASIN/parent identity.
    changed = {**task(), "normalized_product_url": task()["normalized_product_url"] + "&smid=OTHER"}
    assert find_valid_cache(changed, index) is None
    mp.write_text(json.dumps({**meta, "requested_url": task()["request_url"], "status_code": 403}), encoding="utf8")
    assert find_valid_cache(task(), index) is None


def auth_for(bridge, tmp_path):
    from datetime import datetime, timedelta, timezone
    gate = tmp_path / "shared_gate.json"; immutable_json(gate, sealed({"state": "NORMAL"}))
    assert bridge.plan["shared_gate_path"] == str(gate)
    # Test-only authorization evidence. This fixture never runs a browser.
    ci = tmp_path / "ci.json"; immutable_json(ci, {"headSha": "test-code", "conclusion": "success"})
    offline = tmp_path / "offline.json"; immutable_json(offline, {"passed": True, "code_sha": "test-code"})
    now = datetime.now(timezone.utc)
    return dict(approved=True, scope="DETAIL_5500_INDEPENDENT", run_id=bridge.plan["run_id"],
        runtime_root=str(bridge.root.resolve()), plan_sha256=digest(bridge.plan), approved_by="offline-test",
        approval_id="test-only", code_sha="test-code", starts_at=(now-timedelta(hours=1)).isoformat(),
        deadline=(now+timedelta(hours=1)).isoformat(), max_requests=5, max_page_requests=5,
        max_other_requests=0, request_start_interval_seconds=3, max_attempts_per_task=1,
        ci_success_evidence={"path": str(ci), "sha256": hashlib.sha256(ci.read_bytes()).hexdigest()},
        offline_acceptance_reference={"path": str(offline), "sha256": hashlib.sha256(offline.read_bytes()).hexdigest()})


def test_adapter_live_boundary_fake_session_success_no_repeat(tmp_path, real_page, monkeypatch):
    from types import SimpleNamespace
    html, meta = real_page
    # This boundary test simulates the session location separately; the real
    # historical HTML is not claimed to have a verified Spain destination.
    monkeypatch.setattr("amazon_es_bestseller.collection.detail_bridge5500.inspect_delivery_location",
                        lambda _: SimpleNamespace(is_spain=True))
    t = task()
    bridge = DetailBridge5500(tmp_path / "run", {"tasks": [t], "run_id": "test", "shared_gate_path": str(tmp_path / "shared_gate.json")})
    auth = auth_for(bridge, tmp_path)
    calls = []
    session = SimpleNamespace(page=SimpleNamespace(url=meta["final_url"], content=lambda: html),
        goto=lambda url: calls.append(url) or 200, wait_for_product_page=lambda: None,
        wait_for_price_text=lambda: None)
    bridge.run(session, auth, until_completed=1)
    bridge.run(session, auth, until_completed=1)
    assert calls == [t["request_url"]]
    assert bridge.progress()["paid_requests"] == 1
    assert bridge.progress()["success"] == 1


def test_adapter_403_stops_and_preserves_receipt_and_shared_gate(tmp_path, real_page):
    from types import SimpleNamespace
    from amazon_es_bestseller.access.detector import AccessStopError
    html, meta = real_page
    bridge = DetailBridge5500(tmp_path / "run", {"tasks": [task()], "run_id": "test", "shared_gate_path": str(tmp_path / "shared_gate.json")})
    auth = auth_for(bridge, tmp_path)
    session = SimpleNamespace(page=SimpleNamespace(url=meta["final_url"], content=lambda: html),
                              goto=lambda url: 403)
    with pytest.raises(AccessStopError):
        bridge.run(session, auth, until_completed=1)
    assert read_sealed(bridge.plan["shared_gate_path"])["state"] == "BLOCKED"
    assert bridge.progress()["success"] == 0 and bridge.progress()["paid_requests"] == 1
    with pytest.raises(AccessStopError):
        bridge.run(session, auth, until_completed=1)


def test_invalid_ci_blocks_before_network(tmp_path):
    bridge = DetailBridge5500(tmp_path / "run", {"tasks": [], "run_id": "test", "shared_gate_path": str(tmp_path / "shared_gate.json")})
    auth = auth_for(bridge, tmp_path)
    auth["code_sha"] = "new-unverified-code"
    with pytest.raises(ValueError, match="CURRENT_CODE_OFFLINE"):
        bridge.validate_authorization(auth)
