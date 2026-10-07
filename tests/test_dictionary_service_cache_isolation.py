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
        return ProviderResponse(text="中文译文", provider=self.name, model=self.model)


def test_dictionary_namespace_reuses_raw_candidate_only_as_review_without_provider_call(tmp_path):
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
    assert second_provider.calls == 0
    assert field["dictionary_version"] == "2"
    assert field["dictionary_hash"] == "two"
    assert field["translation_status"] == "pending"
    assert field["qa_status"] == "review_required"
    assert field["candidate_text"]
    assert "CACHE_NAMESPACE_REVIEW_REQUIRED" in {
        issue["code"] for issue in field["qa_issues"]
    }
    assert first._field_cache_key("B000000001", "title_es_raw", field["source_hash"]) != second._field_cache_key(
        "B000000001", "title_es_raw", field["source_hash"])
