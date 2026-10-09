"""Run-scoped no-failover policy leaves every claimed dispatch single-attempt."""
import pytest

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.pool import PoolProviderAdapter, TranslationTask, build_qwen_provider_pool
from amazon_es_bestseller.translation.service import TranslationService


def configured_pool(monkeypatch, transport, *, failover=False):
    monkeypatch.setenv("OFFLINE_TEST_POOL_KEY", "offline-fixture-only")
    return build_qwen_provider_pool({
        "failover": failover,
        "max_workers": 2,
        "model": "qwen-mt-flash",
        "providers": [{"name": alias, "api_key_env": "OFFLINE_TEST_POOL_KEY",
                       "endpoint": "https://offline.invalid/v1/chat/completions",
                       "rate": 0, "max_retries": 0}
                      for alias in ("A", "B")],
    }, transport_factory=lambda alias: lambda *args: transport(alias, *args))


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504, 599])
def test_disabled_failover_and_retries_issue_one_attempt_per_dispatch(monkeypatch, tmp_path, code):
    calls = []

    def transport(alias, *_args):
        calls.append(alias)
        return {"status_code": code, "body": {}}

    pool = configured_pool(monkeypatch, transport)
    assert pool.failover is False
    service = TranslationService(PoolProviderAdapter(pool), TranslationCache(tmp_path / "cache.json"))
    rows = [{"asin": "B000000001", "product_description": "30L"}]
    first = service.translate_records(rows)
    assert len(calls) == 1
    assert first["records"]["B000000001"]["fields"]["description_zh"]["translation_status"] == (
        "pending" if code == 599 else "failed")
    restarted_pool = configured_pool(monkeypatch, transport)
    restarted = TranslationService(PoolProviderAdapter(restarted_pool), TranslationCache(tmp_path / "cache.json"))
    assert restarted.plan(rows)["estimated_api_requests"] == 0
    restarted.translate_records(rows)
    assert len(calls) == 1


def test_disabled_failover_keeps_real_qa_failure_without_another_attempt(monkeypatch, tmp_path):
    calls = []

    def transport(alias, *_args):
        calls.append(alias)
        return {"status_code": 200, "body": {"text": "9L"}}

    pool = configured_pool(monkeypatch, transport)
    service = TranslationService(PoolProviderAdapter(pool), TranslationCache(tmp_path / "cache.json"))
    rows = [{"asin": "B000000001", "product_description": "30L"}]
    result = service.translate_records(rows)
    assert result["records"]["B000000001"]["fields"]["description_zh"]["translation_status"] == "qa_failed"
    service.translate_records(rows)
    assert len(calls) == 1


def test_failure_disables_only_that_route_for_unattempted_new_keys(monkeypatch):
    calls = []

    def transport(alias, *_args):
        calls.append(alias)
        return {"status_code": 500 if alias == "A" else 200, "body": {"text": "ok"}}

    pool = configured_pool(monkeypatch, transport)
    first = TranslationTask.from_values("first", asin="B000000001", field="product_description")
    second = TranslationTask.from_values("second", asin="B000000002", field="product_description")
    assert pool.submit([first])[0].response.status == "failed"
    assert calls == ["A"]
    assert pool.submit([second])[0].response.status == "success"
    assert calls == ["A", "B"]


def test_unspecified_policy_retains_default_failover(monkeypatch):
    monkeypatch.setenv("OFFLINE_TEST_POOL_KEY", "offline-fixture-only")
    pool = build_qwen_provider_pool({"providers": [{"name": "A", "api_key_env": "OFFLINE_TEST_POOL_KEY"}]})
    assert pool.failover is True
