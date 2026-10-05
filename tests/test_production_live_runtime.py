"""No-network tests for the CLI-owned reviewed V1 runtime factory."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from amazon_es_bestseller.cli import main
from amazon_es_bestseller.monitoring.snapshot import build_ranking_snapshot
from amazon_es_bestseller.orchestration.live_runtime import (
    LiveRuntimeError,
    _TruncationFailClosedProvider,
    build_qwen_provider,
    load_live_transport_scope,
)
from amazon_es_bestseller.orchestration.task_config import TaskConfig
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider


ASIN = "B000000001"
URL = "https://www.amazon.es/gp/bestsellers/kitchen/123"


def _task_config(root: Path) -> Path:
    html_root = root / "details"; html_root.mkdir()
    (html_root / (ASIN + ".html")).write_text(
        "<input id='ASIN' value='%s'><h1 id='productTitle'>Botella 500 ml</h1>" % ASIN,
        encoding="utf-8")
    snapshot_root = root / "source_snapshot"; (snapshot_root / "html").mkdir(parents=True)
    (snapshot_root / "html" / "source.html").write_text("<html>reviewed source</html>", encoding="utf-8")
    snapshot = snapshot_root / "snapshot.json"
    snapshot.write_text(json.dumps({
        "pages": [{"source_url": URL, "http_status": 200, "html_file": "html/source.html"}],
        "page_count": 1, "links": [], "link_count": 0,
    }), encoding="utf-8")
    plan = root / "reviewed_plan.json"
    plan.write_text(json.dumps({
        "task_id": "live-fixture", "target_unique": 1,
        "discovery_required": False, "sources_reviewed": True,
        "source_snapshot": str(snapshot), "rank_end": 50, "pages_per_url": 1,
        "scheduler": {"mode": "parallel3", "max_parallel_categories": 3,
                      "cooldown_after_category_seconds": 600,
                      "fallback_cooldown_seconds": 1800},
        "categories": [{"research_category": "fixture", "target_unique": 1,
                        "sources": [{"source_url": URL, "role": "primary"}]}],
    }), encoding="utf-8")
    config = root / "task.json"
    config.write_text(json.dumps({
        "task_id": "live-fixture", "mode": "initial", "network_mode": "live",
        "profile": "production-research", "history_dir": "history",
        "reviewed_task_plan": plan.name,
        "source": {"source_urls": [URL], "pages_per_url": 1, "detail_html_dirs": ["details"]},
        "live_transport": {"enabled": True, "kind": "browser-v1", "postal_code": "28001",
                           "request_budget": {"max_ranking_pages": 1, "max_detail_requests": 1}},
        "translation": {"provider_mode": "fake"},
    }), encoding="utf-8")
    return config


def test_cli_live_factory_uses_existing_v1_adapters_only_after_explicit_opt_in(tmp_path, monkeypatch):
    config = _task_config(tmp_path)
    observed = {"entered": 0, "exited": 0, "delivery": []}

    class FakeBrowserSession:
        def __init__(self, *, headless, profile_dir):
            observed["headless"] = headless; observed["profile_dir"] = profile_dir
            self.challenge_wait_seconds = 0; self.manual_assist = False

        def __enter__(self):
            observed["entered"] += 1; return self

        def __exit__(self, *_args):
            observed["exited"] += 1; return False

    def fake_delivery(session, postal_code):
        observed["delivery"].append((session, postal_code)); return None

    def fake_snapshot(urls, session, output_root, **kwargs):
        observed["snapshot"] = {"urls": tuple(urls), "session": session, **kwargs}
        return build_ranking_snapshot([{
            "asin": ASIN, "ranking_asin": ASIN, "bestseller_rank": 1,
            "ranking_source_url": URL, "ranking_page_number": 1,
            "ranking_product_url_raw": "/dp/%s" % ASIN,
            "ranking_product_url_normalized": "https://www.amazon.es/dp/%s" % ASIN,
            "ranking_link_asin": ASIN, "ranking_link_identity_status": "MATCH",
            "leaf_category": "Cocina", "browse_node_id": "123",
        }], output_root, planned_sources=[{"source_url": URL, "page_number": 1}],
            source_statuses=[{"source_url": URL, "page_number": 1, "status": "NORMAL",
                              "access_state": "NORMAL", "parse_status": "PARSE_OK",
                              "parsed_record_count": 1}],
            snapshot_id="snapshot_live_fixture", publish_authoritative_pointer=False)

    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession", FakeBrowserSession)
    monkeypatch.setattr("amazon_es_bestseller.access.location.ensure_spain_delivery", fake_delivery)
    monkeypatch.setattr("amazon_es_bestseller.monitoring.snapshot.collect_ranking_snapshot", fake_snapshot)
    run_dir = tmp_path / "run"
    assert main(["production-run", "--allow-live-transport", "--run-dir", str(run_dir),
                 "--run-id", "live", "--config", str(config), "--profile", "source-only"]) == 0
    assert observed["entered"] == observed["exited"] == 1
    assert observed["delivery"][0][1] == "28001"
    assert observed["snapshot"]["urls"] == (URL,)
    assert observed["snapshot"]["parser_version"] == "v1"
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "DRAFT_SOURCE_ONLY"

    # A verified continuation after V1 evidence exists does not reopen the
    # browser merely to visit a later source/translation stage.
    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession",
                        lambda **_kwargs: pytest.fail("resume must not reopen browser"))
    assert main(["production-run", "--allow-live-transport", "--resume", "--from-stage", "source-audit",
                 "--run-dir", str(run_dir), "--run-id", "live", "--config", str(config),
                 "--profile", "source-only"]) == 0
    assert observed["entered"] == observed["exited"] == 1


def test_cli_live_factory_is_fail_closed_before_browser_creation(tmp_path, monkeypatch):
    config = _task_config(tmp_path)
    monkeypatch.setattr("amazon_es_bestseller.access.browser.BrowserSession",
                        lambda **_kwargs: pytest.fail("browser must not start"))
    with pytest.raises(SystemExit, match="LIVE_TRANSPORT_NOT_AUTHORIZED"):
        main(["production-run", "--run-dir", str(tmp_path / "run"), "--run-id", "live",
              "--config", str(config), "--profile", "source-only"])


def test_scope_hash_and_qwen_authorization_are_checked_before_transport(tmp_path):
    config_path = _task_config(tmp_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    task = TaskConfig.from_mapping(raw, base_dir=tmp_path)
    scope = load_live_transport_scope(task, config_dir=tmp_path)
    assert scope.target_unique == 1 and scope.source_counts == {"primary": 1, "reserve": 0}
    with pytest.raises(LiveRuntimeError, match="QWEN_TRANSLATION_NOT_AUTHORIZED"):
        qwen_task = TaskConfig.from_mapping({**raw, "translation": {"provider_mode": "qwen-mt"}},
                                            base_dir=tmp_path)
        build_qwen_provider(qwen_task, run_dir=tmp_path / "run",
                            allow_qwen_translation=False, offline=False)
    plan_path = tmp_path / raw["reviewed_task_plan"]
    plan_path.write_text(plan_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(LiveRuntimeError, match="FINGERPRINT_MISMATCH"):
        load_live_transport_scope(task, config_dir=tmp_path)


def test_5500_scope_is_frozen_to_the_reviewed_15_category_source_plan():
    root = Path(__file__).parents[1]
    plan_path = root / "configs" / "tasks" / "amazon_es_bestseller_5500_202610_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    first_url = plan["categories"][0]["sources"][0]["source_url"]
    task = TaskConfig.from_mapping({
        "task_id": "amazon_es_bestseller_5500_202610", "network_mode": "live",
        "reviewed_task_plan": str(plan_path),
        "source": {"source_urls": [first_url], "pages_per_url": 2, "detail_html_dirs": []},
        "live_transport": {"enabled": True, "kind": "browser-v1",
                           "request_budget": {"max_ranking_pages": 2, "max_detail_requests": 1}},
        "translation": {"provider_mode": "fake"},
    }, base_dir=root)
    scope = load_live_transport_scope(task, config_dir=root)
    assert scope.target_unique == 5500 and scope.category_count == 15
    assert scope.source_counts == {"primary": 55, "reserve": 31}


def test_qwen_length_finish_reason_is_not_a_successful_translation():
    class LengthProvider(TranslationProvider):
        name = "qwen-mt"

        @property
        def model(self):
            return "qwen-mt-flash"

        def translate(self, _text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
            return ProviderResponse(text="partial", provider=self.name, model=self.model,
                                    raw={"choices": [{"finish_reason": "length"}]})

    response = _TruncationFailClosedProvider(LengthProvider()).translate(
        "source", asin=ASIN, field="title")
    assert response.status == "failed" and response.error == "QWEN_OUTPUT_TRUNCATED"
