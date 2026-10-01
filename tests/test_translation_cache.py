from amazon_es_bestseller.translation.cache import TranslationCache


def test_cache_atomic_roundtrip(tmp_path):
    path = tmp_path / "cache.json"
    cache = TranslationCache(path)
    cache.put("k", {"translation_status": "success", "source_hash": "h"})
    cache.save()
    assert TranslationCache(path).get("k")["source_hash"] == "h"
    assert not list(tmp_path.glob("*.tmp-*"))


def test_cache_corruption_is_preserved_and_recovered(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not-json", encoding="utf-8")
    cache = TranslationCache(path)
    assert cache.recovered_from_corruption
    assert len(cache) == 0
    assert list(tmp_path.glob("cache.json.corrupt-*"))


def test_translation_memory_roundtrip_is_not_asin_scoped(tmp_path):
    cache = TranslationCache(tmp_path / "cache.json")
    key = cache.memory_key("Hogar y cocina", "es", "zh-CN", "category",
                           "qwen-mt", "qwen-mt-flash", "translation-v2.1", "v1")
    cache.put_memory(key, {"translated_text": "家居与厨房", "translation_status": "success"})
    cache.save()
    loaded = TranslationCache(tmp_path / "cache.json")
    assert loaded.get_memory(key)["translated_text"] == "家居与厨房"
