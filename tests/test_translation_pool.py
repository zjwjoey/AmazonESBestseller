import json
import threading
import time

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.pool import (
    DEGRADED, ProviderPool, TranslationTask, build_qwen_provider_pool,
)
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider


class PoolFake(TranslationProvider):
    model = "fake-model"

    def __init__(self, name, *, text_prefix="ok", fail=False, barrier=None):
        self.name = name
        self.text_prefix = text_prefix
        self.fail = fail
        self.barrier = barrier
        self.calls = []

    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((text, asin, field))
        if self.barrier is not None:
            try:
                self.barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
        if self.fail:
            return ProviderResponse(provider=self.name, model=self.model, status="failed", error="HTTP 599")
        return ProviderResponse(text=self.text_prefix + text, provider=self.name, model=self.model)


class PermanentFailureFake(PoolFake):
    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((text, asin, field))
        return ProviderResponse(provider=self.name, model=self.model, status="failed", error="invalid request")


class ToggleFake(PoolFake):
    def __init__(self, name):
        super().__init__(name)
        self.fail_now = True

    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((text, asin, field))
        if self.fail_now:
            return ProviderResponse(provider=self.name, model=self.model, status="failed", error="HTTP 599")
        return ProviderResponse(text="ok", provider=self.name, model=self.model)


def task(text, n):
    return TranslationTask.from_values(text, asin=f"B{n:08d}", field="title_es_raw")


def test_two_providers_overlap_and_round_robin():
    barrier = threading.Barrier(2)
    a, b = PoolFake("qwen-mt", barrier=barrier), PoolFake("qwen-mt", barrier=barrier)
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    results = pool.submit([task("Uno", 1), task("Dos", 2)])
    assert len(a.calls) == 1 and len(b.calls) == 1
    assert {result.provider_alias for result in results} == {"qwen-a", "qwen-b"}


def test_ten_units_are_distributed_to_both_provider_aliases():
    a, b = PoolFake("qwen-mt"), PoolFake("qwen-mt")
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    pool.submit([task(f"unit-{index}", index) for index in range(10)])
    assert len(a.calls) > 0 and len(b.calls) > 0


def test_duplicate_translation_unit_has_one_provider_call():
    provider = PoolFake("qwen-mt")
    pool = ProviderPool({"qwen-a": provider})
    duplicate = task("same", 1)
    results = pool.submit([duplicate, duplicate])
    assert len(provider.calls) == 1
    assert results[0].response.text == results[1].response.text
    assert pool.snapshot()["completed"] == 1


def test_network_failure_fails_over_once_and_marks_provider_degraded():
    a, b = PoolFake("qwen-mt", fail=True), PoolFake("qwen-mt", text_prefix="B:")
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    result = pool.submit([task("one", 1)])[0]
    assert result.response.status == "success"
    assert result.provider_alias == "qwen-b"
    assert pool.stats()["qwen-a"]["state"] == DEGRADED
    assert pool.snapshot()["degraded_to_single_provider"] is True
    assert len(a.calls) == 1 and len(b.calls) == 1


def test_two_provider_failure_returns_failed_without_exception():
    a, b = PoolFake("qwen-mt", fail=True), PoolFake("qwen-mt", fail=True)
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    result = pool.submit([task("one", 1)])[0]
    assert result.response.status == "failed"
    assert len(a.calls) == 1 and len(b.calls) == 1


def test_permanent_failure_does_not_fail_over_to_second_provider():
    a, b = PermanentFailureFake("qwen-mt"), PoolFake("qwen-mt", text_prefix="B:")
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    result = pool.submit([task("one", 1)])[0]
    assert result.response.status == "failed"
    assert len(a.calls) == 1 and len(b.calls) == 0


def test_successful_unit_is_shared_as_tm_result_without_second_call():
    provider = PoolFake("qwen-mt")
    pool = ProviderPool({"qwen-a": provider})
    one = task("Acero inoxidable", 1)
    pool.submit([one])
    pool.submit([one])
    assert len(provider.calls) == 1


def test_failed_unit_remains_retryable_after_provider_recovers():
    provider = ToggleFake("qwen-mt")
    pool = ProviderPool({"qwen-a": provider})
    one = task("retry", 1)
    assert pool.submit([one])[0].response.status == "failed"
    provider.fail_now = False
    assert pool.submit([one])[0].response.status == "success"
    assert len(provider.calls) == 2


def test_all_disabled_providers_return_pending_instead_of_crashing():
    a, b = PermanentFailureFake("qwen-mt"), PermanentFailureFake("qwen-mt")
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    pool.submit([task("a", 1), task("b", 2)])
    pending = pool.submit([task("c", 3)])[0]
    assert pending.source == "pending"
    assert pending.response.error == "NO_HEALTHY_PROVIDER"


def test_translation_cache_concurrent_writes_remain_valid(tmp_path):
    cache = TranslationCache(tmp_path / "cache.json")

    def write(index):
        cache.put(f"key-{index}", {"translated_text": str(index)})

    threads = [threading.Thread(target=write, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    cache.save()
    loaded = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert len(loaded["entries"]) == 20


def test_qwen_pool_config_uses_aliases_without_real_calls():
    pool = build_qwen_provider_pool({
        "max_workers": 2,
        "providers": [
            {"name": "qwen-a", "api_key_env": "MISSING_A", "endpoint_env": "MISSING_EA"},
            {"name": "qwen-b", "api_key_env": "MISSING_B", "endpoint_env": "MISSING_EB"},
        ],
    })
    assert pool.aliases == ["qwen-a", "qwen-b"]
    assert pool.max_workers == 2
    assert all(stats["requests"] == 0 for stats in pool.stats().values())
