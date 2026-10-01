from amazon_es_bestseller.translation.providers.qwen_mt import QwenMTProvider


def test_qwen_openai_compatible_request_and_response():
    seen = {}

    def transport(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload)
        return {"status_code": 200, "body": {"choices": [{"message": {"content": "中文"}}]}}

    provider = QwenMTProvider(api_key="test-key", endpoint="https://example.invalid",
                              transport=transport, max_retries=0)
    result = provider.translate("Bolsa", asin="B000000001", field="title_es_raw")
    assert result.text == "中文"
    assert seen["payload"]["model"] == "qwen-mt-flash"
    assert seen["headers"]["Authorization"] == "Bearer test-key"


def test_qwen_retries_429_then_succeeds():
    calls = []

    def transport(*_):
        calls.append(1)
        if len(calls) == 1:
            return {"status_code": 429, "body": {"error": "busy"}}
        return {"status_code": 200, "body": {"choices": [{"message": {"content": "中文"}}]}}

    result = QwenMTProvider(api_key="k", transport=transport, max_retries=1,
                            backoff_seconds=0).translate("x", asin="A", field="f")
    assert result.status == "success" and result.attempts == 2


def test_qwen_500_and_timeout_are_bounded_failures():
    for response in ({"status_code": 500, "body": {"error": "server"}},
                     TimeoutError("timeout")):
        def transport(*_, response=response):
            if isinstance(response, Exception):
                raise response
            return response
        result = QwenMTProvider(api_key="k", transport=transport, max_retries=1,
                                backoff_seconds=0).translate("x", asin="A", field="f")
        assert result.status == "failed" and result.attempts == 2


def test_qwen_401_malformed_and_empty_do_not_retry_forever():
    responses = [
        {"status_code": 401, "body": {"error": "unauthorized"}},
        {"status_code": 200, "body": {"unexpected": True}},
        {"status_code": 200, "body": {"choices": [{"message": {"content": ""}}]}},
    ]
    for response in responses:
        calls = []
        def transport(*_, response=response):
            calls.append(1)
            return response
        result = QwenMTProvider(api_key="k", transport=transport, max_retries=3,
                                backoff_seconds=0).translate("x", asin="A", field="f")
        assert result.status == "failed"
        assert len(calls) == 1
