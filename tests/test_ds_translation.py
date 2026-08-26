import json

from amazon_es_bestseller.translation.ds import DeepSeekTranslator, TranslationError


class FakeTransport:
    def __init__(self):
        self.calls = 0
        self.last_payload = None
        self.failures_before_success = 0

    def __call__(self, url, headers, payload, timeout):
        self.calls += 1
        self.last_payload = payload
        if self.calls <= self.failures_before_success:
            raise OSError("temporary failure")
        return {
            "choices": [{"message": {"content": json.dumps({"title_zh": "电钻", "ignored": "drop"}, ensure_ascii=False)}}]
        }


def test_translate_record_returns_only_allowed_chinese_fields():
    transport = FakeTransport()
    client = DeepSeekTranslator(api_key="secret", transport=transport)
    out = client.translate_record({"asin": "B000000001", "title_es_raw": "Taladro"})
    assert out == {"asin": "B000000001", "title_zh": "电钻", "translation_status": "success"}
    assert "api_key" not in transport.last_payload
    assert "secret" not in json.dumps(transport.last_payload, ensure_ascii=False)


def test_translate_record_uses_cache_without_second_request(tmp_path):
    transport = FakeTransport()
    client = DeepSeekTranslator(api_key="secret", cache_path=tmp_path / "translations.json", transport=transport)
    first = client.translate_record({"asin": "B000000001", "title_es_raw": "Taladro"})
    second = client.translate_record({"asin": "B000000001", "title_es_raw": "Taladro"})
    assert first == second
    assert transport.calls == 1
    client.save_cache()
    saved = json.loads((tmp_path / "translations.json").read_text(encoding="utf-8"))
    assert saved["B000000001"]["title_zh"] == "电钻"


def test_translate_record_retries_then_saves_failure():
    transport = FakeTransport()
    transport.failures_before_success = 3
    client = DeepSeekTranslator(api_key="secret", max_retries=2, backoff_seconds=0, transport=transport)
    result = client.translate_record({"asin": "B000000001", "title_es_raw": "Taladro"})
    assert result["asin"] == "B000000001"
    assert result["translation_status"] == "failed"
    assert transport.calls == 3


def test_translate_records_isolates_one_failed_asin():
    transport = FakeTransport()
    client = DeepSeekTranslator(api_key="secret", max_retries=0, backoff_seconds=0, transport=transport)
    records = [{"asin": "B000000001", "title_es_raw": "Taladro"}, {"asin": "B000000002", "title_es_raw": "Martillo"}]
    results = client.translate_records(records)
    assert [r["asin"] for r in results] == ["B000000001", "B000000002"]
    assert all(r["translation_status"] == "success" for r in results)


def test_translate_record_does_not_retry_non_retryable_401():
    class UnauthorizedTransport:
        calls = 0

        def __call__(self, url, headers, payload, timeout):
            self.calls += 1
            raise TranslationError("HTTP 401", retryable=False)

    transport = UnauthorizedTransport()
    client = DeepSeekTranslator(api_key="secret", max_retries=2, backoff_seconds=1, transport=transport)
    result = client.translate_record({"asin": "B000000001", "title_es_raw": "Taladro"})
    assert result["translation_status"] == "failed"
    assert result["translation_error"] == "HTTP 401"
    assert transport.calls == 1


def test_failed_cache_entry_is_not_treated_as_successful_cache_hit(tmp_path):
    cache = tmp_path / "translations.json"
    cache.write_text(json.dumps({"B000000001": {"asin": "B000000001", "translation_status": "failed"}}), encoding="utf-8")
    transport = FakeTransport()
    client = DeepSeekTranslator(api_key="secret", cache_path=cache, transport=transport)
    result = client.translate_record({"asin": "B000000001", "title_es_raw": "Taladro"})
    assert result["translation_status"] == "success"
    assert transport.calls == 1
