from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider
from amazon_es_bestseller.translation.service import TranslationService


class CountingProvider(TranslationProvider):
    name = "counting"
    model = "test"

    def __init__(self):
        self.calls = 0

    def translate(self, text, **kwargs):
        self.calls += 1
        return ProviderResponse(text="翻译 " + text, provider=self.name, model=self.model)


def test_service_does_not_reuse_old_dictionary_version_field_or_tm_cache(tmp_path):
    cache_path = tmp_path / "cache.json"
    first_provider = CountingProvider()
    first = TranslationService(first_provider, TranslationCache(cache_path),
                               dictionary_manifest={"dictionary_version": 1, "dictionary_hash": "one"})
    first.translate_records([{"asin": "B000000001", "title_es_raw": "Producto especial"}])
    second_provider = CountingProvider()
    second = TranslationService(second_provider, TranslationCache(cache_path),
                                dictionary_manifest={"dictionary_version": 2, "dictionary_hash": "two"})
    result = second.translate_records([{"asin": "B000000001", "title_es_raw": "Producto especial"}])
    field = result["records"]["B000000001"]["fields"]["title_zh"]
    assert first_provider.calls == 1
    assert second_provider.calls == 1
    assert field["dictionary_version"] == "2"
    assert field["dictionary_hash"] == "two"
