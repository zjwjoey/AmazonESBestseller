from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider


class FakeProvider(TranslationProvider):
    name = "fake"
    model = "fake-model"

    def __init__(self, fail_fields=()):
        self.calls = []
        self.fail_fields = set(fail_fields)

    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((asin, field, text, context or {}))
        if field in self.fail_fields:
            return ProviderResponse(provider=self.name, model=self.model, status="failed", error="synthetic")
        return ProviderResponse(text="中文 " + text, provider=self.name, model=self.model)


def test_provider_contract_is_one_field_and_context():
    provider = FakeProvider()
    result = provider.translate("Hola 500 ml", asin="B000000001", field="title_es_raw",
                                context={"target_field": "title_zh"})
    assert result.status == "success"
    assert provider.calls[0][0:2] == ("B000000001", "title_es_raw")
