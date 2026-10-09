"""Provider identity must match durable, ASIN-independent semantic claims."""
import pytest

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.pool import PoolProviderAdapter, ProviderPool
from amazon_es_bestseller.translation.protection import protect, restore
from amazon_es_bestseller.translation.providers.base import ProviderResponse
from amazon_es_bestseller.translation.qa import qa_field
from amazon_es_bestseller.translation.service import TranslationService


class EchoProvider:
    name = "qwen-mt"
    model = "identity-fixture"

    def __init__(self, response=None):
        self.calls = []
        self.response = response

    def translate(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return self.response or ProviderResponse(text=text, provider=self.name, model=self.model)


def make_service(tmp_path, provider, **kwargs):
    adapter = PoolProviderAdapter(ProviderPool({"A": provider}), model=provider.model)
    return TranslationService(adapter, TranslationCache(tmp_path / "cache.json"), **kwargs)


@pytest.mark.parametrize("left,right", [("9L", "30L"), ("10×15cm", "10×10mm")])
def test_placeholder_template_restores_each_original_fact(left, right):
    a, b = protect(left), protect(right)
    assert a.text == b.text
    assert restore(a, a.text) == (left, [])
    assert restore(b, a.text) == (right, [])


@pytest.mark.parametrize("left,right", [("9L", "30L"), ("10×15cm", "10×10mm")])
@pytest.mark.parametrize("source_field", ["product_description", "product_details"])
def test_raw_semantic_claims_are_provider_dispatch_identity(tmp_path, left, right, source_field):
    provider = EchoProvider()
    service = make_service(tmp_path, provider)
    rows = [{"asin": f"B00000000{i}", source_field: (
        [{"label_raw": "Medida de prueba", "value_raw": text}]
        if source_field == "product_details" else text)}
            for i, text in enumerate((left, right), 1)]
    plan = service.plan(rows)
    result = service.translate_records(rows)
    assert len(provider.calls) == plan["estimated_api_requests"] == 2
    assert {call[1]["context"]["canonical_dispatch_key"] for call in provider.calls} == set(plan["dispatch_keys"])
    for row, original in zip(result["records"].values(), (left, right)):
        field = row["fields"][service.field_map[source_field]]
        assert field["qa_status"] == "pass"
        value = field["items"][0]["translated_text"] if source_field == "product_details" else field["translated_text"]
        assert value.replace(" ", "").casefold() == original.casefold()


def test_literal_template_reuse_is_not_safe_and_real_qa_rejects_it():
    qa = qa_field(protect("30L"), "9L", "30L", field="product_description")
    assert qa["qa_status"] == "qa_failed"
    assert "NUMERIC_MISMATCH" in {issue["code"] for issue in qa["issues"]}


def test_identical_semantics_reuse_across_asins_and_resume(tmp_path):
    provider = EchoProvider()
    service = make_service(tmp_path, provider)
    rows = [{"asin": asin, "product_description": "9L"}
            for asin in ("B000000001", "B000000002")]
    assert service.plan(rows)["estimated_api_requests"] == 1
    service.translate_records(rows)
    service.cache.save()
    assert len(provider.calls) == 1
    restarted = make_service(tmp_path, provider)
    restarted.translate_records(rows)
    assert len(provider.calls) == 1


@pytest.mark.parametrize("context_change", [
    {"schema_version": "other-schema"},
    {"prompt_version": "other-prompt"},
    {"dictionary_manifest": {"dictionary_version": "2", "dictionary_hash": "other-dictionary"}},
    {"structured_schema_version": "other-structured-schema"},
    {"target_language": "en"},
])
def test_distinct_contexts_do_not_share_pool_result(tmp_path, context_change):
    provider = EchoProvider()
    adapter = PoolProviderAdapter(ProviderPool({"A": provider}), model=provider.model)
    first = TranslationService(adapter, TranslationCache(tmp_path / "first.json"))
    second = TranslationService(adapter, TranslationCache(tmp_path / "second.json"), **context_change)
    row = {"asin": "B000000001", "product_description": "9L"}
    first.translate_records([row])
    second.translate_records([row])
    assert len(provider.calls) == 2


def test_key_upgrade_does_not_repeat_preexisting_durable_success(tmp_path):
    provider = EchoProvider()
    service = make_service(tmp_path, provider)
    # Seed the existing durable namespace, not the new ephemeral pool identity.
    key = service._memory_key("9L", "product_description")
    service.cache.put_memory(key, {"translated_text": "9L", "candidate_text": "9L",
                                  "translation_status": "success", "qa_status": "pass",
                                  "qa_issues": [], "provider": "qwen-mt", "model": provider.model})
    service.cache.save()
    service.translate_records([{"asin": "B000000001", "product_description": "9L"}])
    assert not provider.calls


def test_crash_after_paid_response_keeps_unknown_claim_on_restart(tmp_path):
    provider = EchoProvider()
    service = make_service(tmp_path, provider)
    row = {"asin": "B000000001", "product_description": "9L"}

    def crash(_response):
        raise RuntimeError("fixture crash after response")

    service._after_provider_response = crash
    with pytest.raises(RuntimeError, match="fixture crash"):
        service._translate_record(row)
    assert len(provider.calls) == 1
    restarted = make_service(tmp_path, provider)
    assert restarted.plan([row])["estimated_api_requests"] == 0
    assert restarted._translate_record(row)["fields"]["description_zh"]["translation_status"] == "pending"
    assert len(provider.calls) == 1
