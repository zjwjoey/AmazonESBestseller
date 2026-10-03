# -*- coding: utf-8 -*-
import json
import socket

import pytest

from amazon_es_bestseller.monitoring.detail_planner import (
    DetailAction, build_detail_plan, validate_detail_plan, write_detail_plan,
)


def _ranking(asin="B078C6QR1C", rank=1, **extra):
    return {"snapshot_id": "snapshot_test", "ranking_asin": asin,
            "ranking_rank": rank, "ranking_product_url_raw": "/dp/" + asin,
            "ranking_product_url_normalized": "https://www.amazon.es/dp/" + asin,
            "ranking_link_asin": asin, "ranking_link_identity_status": "MATCH", **extra}


def _valid(asin="B078C6QR1C", **extra):
    return {"asin": asin, "title_es_raw": "Producto", "detail_schema_version": 2,
            "cache_classification": "VALID_PRODUCT_PAGE", "identity_status": "MATCH",
            "access_state": "NORMAL", **extra}


def _snapshot(rows):
    return {"snapshot_status": "AUTHORITATIVE", "snapshot_id": "snapshot_test",
            "records": rows}


def test_planner_is_offline_and_new_asin_is_fetch_new(monkeypatch):
    def no_network(*_args, **_kwargs):
        raise AssertionError("planner attempted network")
    monkeypatch.setattr(socket, "socket", no_network)
    plan = build_detail_plan(_snapshot([_ranking()]))
    assert plan["records"][0]["detail_action"] == DetailAction.FETCH_NEW.value


def test_valid_cache_is_reused_when_rank_and_tracking_url_change():
    row = _ranking(rank=12, ranking_product_url_raw="/dp/B078C6QR1C/ref=new")
    plan = build_detail_plan(_snapshot([row]), detail_cache=[_valid()])
    item = plan["records"][0]
    assert item["detail_action"] == DetailAction.REUSE_VALID_CACHE.value
    assert item["ranking_rank"] == 12


def test_planner_actions_for_failure_schema_and_access_states(tmp_path):
    rows = [_ranking("B000000001", rank=1), _ranking("B000000002", rank=2),
            _ranking("B000000003", rank=3), _ranking("B000000004", rank=4),
            _ranking("B000000005", rank=5)]
    cache = [
        {"asin": "B000000002", "status": "failed", "access_state": "NETWORK_ERROR"},
        {"asin": "B000000003", "cache_classification": "INVALID_OR_EMPTY"},
        {"asin": "B000000004", "cache_classification": "CHALLENGE", "access_state": "CHALLENGE"},
        {"asin": "B000000005", "title_es_raw": "Old", "detail_schema_version": 1,
         "cache_classification": "VALID_PRODUCT_PAGE"},
    ]
    (tmp_path / "B000000005.html").write_text("<html></html>", encoding="utf-8")
    plan = build_detail_plan(_snapshot(rows), cache, saved_html=tmp_path)
    actions = {row["ranking_asin"]: row["detail_action"] for row in plan["records"]}
    assert actions["B000000001"] == "FETCH_NEW"
    assert actions["B000000002"] == "RETRY_TRANSIENT_FAILURE"
    assert actions["B000000003"] == "REFETCH_INVALID_CACHE"
    assert actions["B000000004"] == "BLOCK_ACCESS_STATE"
    assert actions["B000000005"] == "REPARSE_SAVED_HTML"


def test_identity_states_and_bad_link_are_not_guessed():
    parent = _valid("B000000010", parent_asin="B000000010",
                    variation_family_asins=["B000000010", "B000000012"])
    variation = _valid("B000000011", variation_family_asins=["B000000011", "B000000012"])
    rows = [_ranking("B000000012"), _ranking("B000000011"), _ranking("B000000014",
        ranking_link_identity_status="LINK_ASIN_MISMATCH", ranking_link_asin="B000000099")]
    plan = build_detail_plan(_snapshot(rows), [parent, variation])
    by_asin = {row["ranking_asin"]: row for row in plan["records"]}
    assert by_asin["B000000012"]["identity_status"] == "PARENT_ASIN_MATCH"
    assert by_asin["B000000012"]["detail_action"] == "REUSE_VALID_CACHE"
    assert by_asin["B000000011"]["identity_status"] == "MATCH"
    assert by_asin["B000000011"]["detail_action"] == "REUSE_VALID_CACHE"
    assert by_asin["B000000014"]["detail_action"] == "BLOCK_LINK_IDENTITY"


def test_unrelated_detail_asin_requires_identity_verification():
    plan = build_detail_plan(_snapshot([_ranking("B000000014")]),
                             [_valid("B000000014", resolved_asin="B000000013")])
    item = plan["records"][0]
    assert item["identity_status"] == "IDENTITY_MISMATCH"
    assert item["detail_action"] == "VERIFY_IDENTITY"


def test_plan_is_deterministic_and_writes_three_artifacts(tmp_path):
    rows = [_ranking("B000000002", rank=2), _ranking("B000000001", rank=1)]
    plan1 = build_detail_plan(_snapshot(rows))
    plan2 = build_detail_plan(_snapshot(rows))
    assert plan1 == plan2
    paths = write_detail_plan(plan1, tmp_path)
    assert all(path.exists() for path in paths.values())


def test_planner_rejects_incomplete_authoritative_input():
    with pytest.raises(ValueError, match="AUTHORITATIVE"):
        build_detail_plan({"snapshot_status": "INCOMPLETE", "records": [_ranking()]})


def test_planner_rejects_snapshot_without_authority_status():
    with pytest.raises(ValueError, match="AUTHORITATIVE"):
        build_detail_plan({"records": [_ranking()]})


def test_unrelated_parent_or_family_evidence_cannot_reuse_cache():
    parent = _valid("B000000003", resolved_asin="B000000099", parent_asin="B000000099")
    family = _valid("B000000004", resolved_asin="B000000098",
                    variation_family_asins=["B000000098"])
    plan = build_detail_plan(_snapshot([_ranking("B000000003"), _ranking("B000000004")]),
                             [parent, family])
    by_asin = {row["ranking_asin"]: row for row in plan["records"]}
    assert by_asin["B000000003"]["identity_status"] == "IDENTITY_MISMATCH"
    assert by_asin["B000000003"]["detail_action"] == "VERIFY_IDENTITY"
    assert by_asin["B000000004"]["identity_status"] == "IDENTITY_MISMATCH"
    assert by_asin["B000000004"]["detail_action"] == "VERIFY_IDENTITY"


def test_checkpoint_failure_is_planned_as_retry_and_history_url_is_safe():
    row = _ranking()
    row["ranking_product_url_normalized"] = ""
    cache = [{"asin": "B078C6QR1C", "title_es_raw": "Producto",
              "detail_schema_version": 2, "cache_classification": "VALID_PRODUCT_PAGE",
              "ranking_product_url_normalized": "https://www.amazon.es/dp/B078C6QR1C/ref=old"}]
    plan = build_detail_plan(_snapshot([row]), cache, checkpoints=[{
        "asin": "B000000009", "status": "failed", "access_state": "NETWORK_ERROR"}])
    assert plan["records"][0]["detail_action"] == "REUSE_VALID_CACHE"
    assert plan["records"][0]["preferred_request_url_source"] == "historical_valid_ranking_product_url"

    retry = build_detail_plan(_snapshot([_ranking("B000000009")]), checkpoints=[{
        "asin": "B000000009", "status": "failed", "access_state": "NETWORK_ERROR"}])
    assert retry["records"][0]["detail_action"] == "RETRY_TRANSIENT_FAILURE"


def test_missing_product_link_uses_canonical_fallback_instead_of_blocking():
    row = _ranking("B000000015", ranking_product_url_raw="", ranking_product_url_normalized="",
                   ranking_link_asin=None, ranking_link_identity_status="NO_PRODUCT_URL",
                   ranking_asin_source="CARD_DATA_ASIN")
    item = build_detail_plan(_snapshot([row]))["records"][0]
    assert item["detail_action"] == "FETCH_NEW"
    assert item["preferred_request_url"] == "https://www.amazon.es/dp/B000000015"
    assert item["preferred_request_url_source"] == "asin_canonical_fallback"


def test_best_request_context_is_not_selected_by_rank_alone():
    rows = [
        _ranking("B000000016", rank=1, ranking_product_url_raw="",
                 ranking_product_url_normalized="", ranking_link_asin=None,
                 ranking_link_identity_status="NO_PRODUCT_URL", ranking_asin_source="CARD_DATA_ASIN"),
        _ranking("B000000016", rank=8, ranking_product_url_raw="/dp/B000000016/ref=x",
                 ranking_product_url_normalized="https://www.amazon.es/dp/B000000016",
                 ranking_link_asin="B000000016", ranking_link_identity_status="MATCH"),
    ]
    item = build_detail_plan(_snapshot(rows))["records"][0]
    assert item["preferred_request_url_source"] == "latest_ranking_product_url"
    assert item["best_ranking_rank"] == 1
    assert item["ranking_rank"] == 8


def test_plan_artifact_hash_and_mixed_snapshot_validation(tmp_path):
    plan = build_detail_plan(_snapshot([_ranking()]))
    path = write_detail_plan(plan, tmp_path)["json"]
    artifact = json.loads(path.read_text(encoding="utf-8"))
    assert artifact["plan_id"]
    assert len(artifact["plan_hash"]) == 64
    validate_detail_plan(artifact)
    artifact["records"][0]["detail_action"] = "BLOCK_CODE_FIX"
    with pytest.raises(ValueError, match="hash"):
        validate_detail_plan(artifact)
    with pytest.raises(ValueError, match="snapshot_id"):
        write_detail_plan({"snapshot_id": "s1", "records": [
            {"snapshot_id": "s1"}, {"snapshot_id": "s2"}]}, tmp_path / "mixed")
