import json
import threading
import time

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.pool import (
    DEGRADED, PoolProviderAdapter, ProviderPool, TranslationTask, build_qwen_provider_pool,
)
from amazon_es_bestseller.translation.field_contract import canonical_translation_unit_field
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


class SlowInvalidFake(PermanentFailureFake):
    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        time.sleep(0.02)
        return super().translate(text, asin=asin, field=field, source_language=source_language,
                                 target_language=target_language, context=context)


class BarrierInvalidFake(PermanentFailureFake):
    def __init__(self, name, barrier):
        super().__init__(name)
        self.barrier = barrier

    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.barrier.wait(timeout=2)
        return super().translate(text, asin=asin, field=field, source_language=source_language,
                                 target_language=target_language, context=context)


class InternalRetryFake(PoolFake):
    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((text, asin, field))
        return ProviderResponse(text="ok", provider=self.name, model=self.model, attempts=3)


def task(text, n):
    return TranslationTask.from_values(text, asin=f"B{n:08d}", field="title_es_raw")


def test_translation_task_key_uses_canonical_field_and_languages():
    category_l1 = TranslationTask.from_values(
        "Hogar y cocina", asin="B00000001", field="category_l1",
        source_language="es", target_language="zh-CN")
    category_l2 = TranslationTask.from_values(
        "Hogar y cocina", asin="B00000002", field="category_l2",
        source_language="es", target_language="zh-CN")
    title = TranslationTask.from_values(
        "Hogar y cocina", asin="B00000003", field="title_es_raw",
        source_language="es", target_language="zh-CN")
    english = TranslationTask.from_values(
        "Hogar y cocina", asin="B00000004", field="category_l1",
        source_language="en", target_language="zh-CN")
    assert category_l1.key == category_l2.key
    assert title.key != category_l1.key
    assert english.key != category_l1.key


def test_structured_translation_task_key_is_scoped_by_normalized_label():
    color_a = TranslationTask.from_values(
        "Natural", asin="B00000001", field="product_details_es",
        translation_unit_field="product_details:color")
    color_b = TranslationTask.from_values(
        "Natural", asin="B00000002", field="product_details_es",
        translation_unit_field="product_details:color")
    material = TranslationTask.from_values(
        "Natural", asin="B00000003", field="product_details_es",
        translation_unit_field="product_details:material")
    assert color_a.key == color_b.key
    assert material.key != color_a.key
    assert color_a.field == "product_details_es"
    assert canonical_translation_unit_field("product_details_es", label="Tamaño") == "product_details:tamano"


def test_pool_adapter_uses_structured_semantic_key_without_changing_provider_field():
    provider = PoolFake("qwen-mt")
    adapter = PoolProviderAdapter(ProviderPool({"qwen-a": provider}))
    adapter.translate("Natural", asin="B00000001", field="product_details_es",
                      context={"translation_unit_field": "product_details:color"})
    adapter.translate("Natural", asin="B00000002", field="product_details_es",
                      context={"translation_unit_field": "product_details:color"})
    adapter.translate("Natural", asin="B00000003", field="product_details_es",
                      context={"translation_unit_field": "product_details:material"})
    assert len(provider.calls) == 2
    assert {call[2] for call in provider.calls} == {"product_details_es"}


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


def test_pool_adapter_preserves_pending_status_for_service_resume():
    a, b = PermanentFailureFake("qwen-mt"), PermanentFailureFake("qwen-mt")
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    pool.submit([task("first", 1), task("second", 2)])
    response = PoolProviderAdapter(pool).translate("third", asin="B00000003", field="title_es_raw")
    assert response.status == "pending"
    assert response.error == "NO_HEALTHY_PROVIDER"


def test_bulk_submission_stops_after_provider_health_boundary():
    barrier = threading.Barrier(2)
    a, b = BarrierInvalidFake("qwen-mt", barrier), BarrierInvalidFake("qwen-mt", barrier)
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    results = pool.submit([task(f"bulk-{index}", index) for index in range(20)])
    assert len(a.calls) + len(b.calls) <= 2
    assert sum(result.source == "pending" for result in results) >= 18


def test_provider_internal_attempts_are_counted_as_retries():
    provider = InternalRetryFake("qwen-mt")
    pool = ProviderPool({"qwen-a": provider})
    pool.submit([task("retry-stats", 1)])
    assert pool.stats()["qwen-a"]["retries"] == 2


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
