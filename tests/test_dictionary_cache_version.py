from amazon_es_bestseller.translation.cache import TranslationCache


def test_dictionary_version_is_a_fail_closed_field_and_memory_namespace():
    field_v1 = TranslationCache.key("B000000001", "title_es_raw", "source", "qwen", "flash", "v2", "p1", "1")
    field_v2 = TranslationCache.key("B000000001", "title_es_raw", "source", "qwen", "flash", "v2", "p1", "2")
    memory_v1 = TranslationCache.memory_key("Filtro", "es", "zh-CN", "attribute", "qwen", "flash", "v2", "p1", "1")
    memory_v2 = TranslationCache.memory_key("Filtro", "es", "zh-CN", "attribute", "qwen", "flash", "v2", "p1", "2")
    assert field_v1 != field_v2
    assert memory_v1 != memory_v2
