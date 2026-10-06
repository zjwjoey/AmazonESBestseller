# -*- coding: utf-8 -*-
import json

import pytest

from amazon_es_bestseller.access.detector import AccessStopError
from amazon_es_bestseller.collection.checkpoints import write_checkpoint
from amazon_es_bestseller.collection import detail as detail_module
from amazon_es_bestseller.collection.detail import collect_details
from amazon_es_bestseller.collection.detail_executor import execute_detail_plan


def _plan():
    return {"snapshot_id": "snapshot_test", "records": [
        {"snapshot_id": "snapshot_test", "ranking_asin": "B000000001",
         "detail_action": "REUSE_VALID_CACHE", "preferred_request_url": "https://www.amazon.es/dp/B000000001"},
        {"snapshot_id": "snapshot_test", "ranking_asin": "B000000002",
         "detail_action": "BLOCK_ACCESS_STATE", "preferred_request_url": "https://www.amazon.es/dp/B000000002"},
        {"snapshot_id": "snapshot_test", "ranking_asin": "B000000003",
         "detail_action": "FETCH_NEW", "preferred_request_url": "https://www.amazon.es/dp/B000000003"},
    ]}


def test_executor_only_passes_network_actions_and_records_checkpoint(tmp_path):
    calls = []

    def fake_collector(asins, session, out_dir, **kwargs):
        calls.append((list(asins), kwargs["request_urls"].copy()))
        write_checkpoint(tmp_path / "checkpoints", asins[0], {
            "asin": asins[0], "status": "success", "snapshot_id": "snapshot_test",
            "action": "FETCH_NEW", "requested_url": kwargs["request_urls"][asins[0]],
        })
        return [{"asin": asins[0], "title_es_raw": "Producto"}]

    result = execute_detail_plan(_plan(), object(), str(tmp_path), collector=fake_collector)
    assert calls == [(["B000000003"], {"B000000003": "https://www.amazon.es/dp/B000000003"})]
    assert {row["status"] for row in result["records"] if row["ranking_asin"] in {
        "B000000001", "B000000002"}} == {"REUSED", "BLOCKED"}
    assert json.loads((tmp_path / "detail_execution_manifest.json").read_text())["requested_count"] == 1


def test_executor_forwards_detail_parser_version_and_records_it(tmp_path):
    calls = []

    def fake_collector(asins, _session, _out_dir, **kwargs):
        calls.append(kwargs)
        return [{"asin": asins[0], "title_es_raw": "Producto",
                 "detail_schema_version": 2}]

    plan = {"snapshot_id": "snapshot_v2", "records": [
        {"snapshot_id": "snapshot_v2", "ranking_asin": "B000000001",
         "detail_action": "FETCH_NEW"},
    ]}
    execute_detail_plan(plan, object(), str(tmp_path), collector=fake_collector,
                        parser_version="v2")
    assert calls[0]["parser_version"] == "v2"
    manifest = json.loads((tmp_path / "detail_execution_manifest.json").read_text())
    assert manifest["parser_version"] == "v2"


def test_executor_falls_back_for_legacy_collector_signature(tmp_path):
    calls = []

    def legacy_collector(asins, _session, _out_dir, *, request_urls, execution_context):
        calls.append(list(asins))
        return [{"asin": asins[0], "title_es_raw": "Producto legado"}]

    plan = {"snapshot_id": "snapshot_legacy", "records": [
        {"snapshot_id": "snapshot_legacy", "ranking_asin": "B000000001",
         "detail_action": "FETCH_NEW"},
    ]}
    result = execute_detail_plan(plan, object(), str(tmp_path), collector=legacy_collector,
                                 parser_version="v2")

    assert calls == [["B000000001"]]
    assert result["details_delta"][0]["title_es_raw"] == "Producto legado"


def test_executor_resume_does_not_duplicate_successful_request(tmp_path):
    calls = []

    def fake_collector(asins, session, out_dir, **kwargs):
        calls.append(list(asins))
        write_checkpoint(tmp_path / "checkpoints", asins[0], {
            "asin": asins[0], "status": "success", "snapshot_id": "snapshot_test",
            "action": "FETCH_NEW"})
        return []

    execute_detail_plan(_plan(), object(), str(tmp_path), collector=fake_collector)
    execute_detail_plan(_plan(), object(), str(tmp_path), collector=fake_collector)
    assert calls == [["B000000003"]]


def test_executor_preserves_access_stop_semantics(tmp_path):
    def stop(*_args, **_kwargs):
        raise AccessStopError("challenge")

    with pytest.raises(AccessStopError):
        execute_detail_plan(_plan(), object(), str(tmp_path), collector=stop)
    manifest = json.loads((tmp_path / "detail_execution_manifest.json").read_text())
    assert manifest["access_stop"] == "challenge"


def test_existing_collector_checkpoint_carries_request_evidence(tmp_path, monkeypatch):
    class Page:
        url = ""

        def content(self):
            return ("<html><body><input id='ASIN' value='B000000003'>"
                    "<h1 id='productTitle'>Producto</h1></body></html>")

    class Session:
        page = Page()

        def goto(self, url):
            self.page.url = url
            return 200

        def wait_for_product_page(self):
            pass

        def wait_for_price_text(self):
            pass

        def wait_between_requests(self):
            pass

    monkeypatch.setattr(detail_module.time, "sleep", lambda _seconds: None)
    records = collect_details(["B000000003"], Session(), str(tmp_path),
                              request_urls={"B000000003": "https://www.amazon.es/dp/B000000003"},
                              execution_context={"B000000003": {
                                  "snapshot_id": "snapshot_test", "ranking_asin": "B000000003",
                                  "ranking_product_url_raw": "/dp/B000000003/ref=x",
                                  "preferred_request_url": "https://www.amazon.es/dp/B000000003",
                                  "action": "FETCH_NEW", "attempt": 1,
                              }})
    assert records[0]["resolved_asin"] == "B000000003"
    checkpoint = json.loads((tmp_path / "checkpoints" / "B000000003.json").read_text())
    assert checkpoint["requested_asin"] == "B000000003"
    assert checkpoint["resolved_asin"] == "B000000003"
    assert checkpoint["snapshot_id"] == "snapshot_test"
    assert checkpoint["action"] == "FETCH_NEW"
    assert checkpoint["detail_status"] == "SUCCESS"
    assert checkpoint["http_status"] == 200
    assert checkpoint["final_access_state"] == "NORMAL"
    assert checkpoint["collected_at"]


def test_incremental_executor_merges_delta_without_overwriting_existing_state(tmp_path):
    existing = [{"asin": "B%09d" % i, "title_es_raw": "old-%d" % i}
                for i in range(1, 101)]
    (tmp_path / "details.json").write_text(json.dumps(existing), encoding="utf-8")
    plan = {"snapshot_id": "snapshot_delta", "records": [
        {"snapshot_id": "snapshot_delta", "ranking_asin": "B000000101", "detail_action": "FETCH_NEW"},
        {"snapshot_id": "snapshot_delta", "ranking_asin": "B000000102", "detail_action": "FETCH_NEW"},
        {"snapshot_id": "snapshot_delta", "ranking_asin": "B000000103", "detail_action": "FETCH_NEW"},
    ]}

    def fake_collector(asins, _session, _out_dir, **_kwargs):
        return [{"asin": asin, "title_es_raw": "new"} for asin in asins]

    execute_detail_plan(plan, object(), str(tmp_path), collector=fake_collector)
    merged = json.loads((tmp_path / "details.json").read_text(encoding="utf-8"))
    assert len(merged) == 103
    assert {row["asin"] for row in merged[:3]} == {"B000000001", "B000000002", "B000000003"}


def test_incremental_executor_failed_delta_preserves_old_and_successful_records(tmp_path):
    existing = [{"asin": "B%09d" % i, "title_es_raw": "old"} for i in range(1, 101)]
    (tmp_path / "details.json").write_text(json.dumps(existing), encoding="utf-8")
    plan = {"snapshot_id": "snapshot_delta", "records": [
        {"snapshot_id": "snapshot_delta", "ranking_asin": "B000000101", "detail_action": "FETCH_NEW"},
        {"snapshot_id": "snapshot_delta", "ranking_asin": "B000000102", "detail_action": "FETCH_NEW"},
        {"snapshot_id": "snapshot_delta", "ranking_asin": "B000000103", "detail_action": "FETCH_NEW"},
    ]}

    def fake_collector(asins, _session, _out_dir, **_kwargs):
        write_checkpoint(tmp_path / "checkpoints", asins[-1], {
            "asin": asins[-1], "status": "failed", "detail_status": "TIMEOUT"})
        return [{"asin": asin, "title_es_raw": "new"} for asin in asins[:2]]

    execute_detail_plan(plan, object(), str(tmp_path), collector=fake_collector)
    merged = json.loads((tmp_path / "details.json").read_text(encoding="utf-8"))
    assert len(merged) == 102
    assert {row["asin"] for row in merged[-2:]} == {"B000000101", "B000000102"}


def test_reparse_is_executed_offline_and_updates_state(tmp_path):
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    (html_dir / "B000000104.html").write_text(
        "<html><body><input id='ASIN' value='B000000104'>"
        "<h1 id='productTitle'>Producto reparseado</h1></body></html>", encoding="utf-8")
    plan = {"snapshot_id": "snapshot_reparse", "records": [{
        "snapshot_id": "snapshot_reparse", "ranking_asin": "B000000104",
        "detail_action": "REPARSE_SAVED_HTML", "saved_html_dir": str(html_dir)}]}
    result = execute_detail_plan(plan, None, str(tmp_path), offline=True, saved_html=html_dir)
    assert result["requested_count"] == 0
    assert json.loads((tmp_path / "details.json").read_text(encoding="utf-8"))[0]["asin"] == "B000000104"
    assert json.loads((tmp_path / "checkpoints" / "B000000104.json").read_text())["action"] == "REPARSE_SAVED_HTML"


def test_reparse_keeps_mislabeled_saved_html_as_identity_review_evidence(tmp_path):
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    (html_dir / "B000000107.html").write_text(
        "<html><body><input id='ASIN' value='B000000108'>"
        "<h1 id='productTitle'>Producto equivocado</h1></body></html>", encoding="utf-8")
    plan = {"snapshot_id": "snapshot_reparse_mismatch", "records": [{
        "snapshot_id": "snapshot_reparse_mismatch", "ranking_asin": "B000000107",
        "detail_action": "REPARSE_SAVED_HTML", "saved_html_dir": str(html_dir)}]}

    result = execute_detail_plan(plan, None, str(tmp_path), offline=True, saved_html=html_dir)

    assert result["requested_count"] == 0
    assert result["records"][0]["status"] == "SUCCESS"
    detail = json.loads((tmp_path / "details.json").read_text(encoding="utf-8"))[0]
    assert detail["detail_status"] == "SUCCESS_WITH_IDENTITY_CHANGE"
    assert detail["identity_review_required"] is True


def test_verify_identity_writes_review_queue_without_network(tmp_path):
    plan = {"snapshot_id": "snapshot_review", "records": [{
        "snapshot_id": "snapshot_review", "ranking_asin": "B000000105",
        "detail_action": "VERIFY_IDENTITY", "resolved_asin": "B000000106",
        "identity_status": "IDENTITY_MISMATCH", "action_reason": "unrelated"}]}
    execute_detail_plan(plan, None, str(tmp_path), offline=True)
    queue = json.loads((tmp_path / "identity_review_queue.json").read_text(encoding="utf-8"))
    assert queue[0]["ranking_asin"] == "B000000105"
