import json
from pathlib import Path

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider
from amazon_es_bestseller.translation.service import TranslationService


class FakeProvider(TranslationProvider):
    name = "fake"
    model = "fake-model"

    def __init__(self, fail_fields=()):
        self.calls = []
        self.fail_fields = set(fail_fields)

    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((asin, field, text))
        if field in self.fail_fields:
            return ProviderResponse(provider=self.name, model=self.model, status="failed", error="synthetic")
        return ProviderResponse(text="中文 " + text, provider=self.name, model=self.model)


def records(n=100):
    return [{"asin": "B%08d" % i, "title_es_raw": "Bolsa 500 ml", "brand": "Acme",
             "feature_bullets_es": "Para coche"} for i in range(1, n + 1)]


def fixture_records():
    return json.loads((Path(__file__).parent / "fixtures" / "translation_v2_100sku.json").read_text(encoding="utf-8"))


def test_service_100_sku_offline_chain_and_category_memory(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records(fixture_records())
    assert len(result["records"]) == 100
    assert len(provider.calls) <= 3
    assert result["records"]["B00000001"]["title_zh"]
    assert result["qa_report"]["status"] in {"pass", "qa_failed"}


def test_service_isolates_failed_fields(tmp_path):
    provider = FakeProvider(fail_fields={"feature_bullets_es"})
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records(records(1))
    row = result["records"]["B00000001"]
    assert row["translation_status"] == "partial"
    assert row["fields"]["title_zh"]["translation_status"] in {"success", "cached"}
    assert row["fields"]["feature_bullets_zh"]["translation_status"] == "failed"


def test_service_dry_run_never_calls_provider(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records(records(100), dry_run=True, limit=5)
    assert result["summary"]["total_records"] == 5
    assert not provider.calls


def test_category_translation_memory_is_shared_across_levels(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    row = {"asin": "B00000001", "category_l1": "Hogar y cocina",
           "category_l2": "Hogar y cocina"}
    result = service.translate_records([row])
    assert result["records"]["B00000001"]["fields"]["category_l1_zh"]["translated_text"] == \
           result["records"]["B00000001"]["fields"]["category_l2_zh"]["translated_text"]
    assert len(provider.calls) == 1


def test_canonical_field_wins_over_raw_fallback(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    selected = service.selected_fields({"asin": "B00000001",
                                        "feature_bullets_es": "canonical",
                                        "feature_bullets_raw": ["fallback"]})
    assert [(src, target) for src, target, _ in selected] == [("feature_bullets_es", "feature_bullets_zh")]


def test_source_hash_change_retranslates_only_changed_field(tmp_path):
    provider = FakeProvider()
    cache = TranslationCache(tmp_path / "cache.json")
    service = TranslationService(provider, cache)
    first = {"asin": "B00000001", "title_es_raw": "Bolsa 500 ml", "brand": "Acme"}
    service.translate_records([first])
    calls_after_first = len(provider.calls)
    second = dict(first, title_es_raw="Bolsa 750 ml")
    service.translate_records([second])
    assert len(provider.calls) == calls_after_first + 1
    assert provider.calls[-1][1] == "title_es_raw"
