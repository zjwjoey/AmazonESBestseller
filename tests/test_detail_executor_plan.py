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
        "B000000001", "B000000002"}} == {"PLAN_SKIPPED"}
    assert json.loads((tmp_path / "detail_execution_manifest.json").read_text())["requested_count"] == 1


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
