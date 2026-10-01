import json
from pathlib import Path

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.pool import ProviderPool
from amazon_es_bestseller.translation.preclean import audit_records
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider
from amazon_es_bestseller.translation.service import TranslationService


class FakeProvider(TranslationProvider):
    name = "fake"
    model = "fake-model"

    def __init__(self, fail_fields=()):
        self.calls = []
        self.fail_fields = set(fail_fields)
        self.response_text = None

    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((asin, field, text))
        if field in self.fail_fields:
            return ProviderResponse(provider=self.name, model=self.model, status="failed", error="synthetic")
        return ProviderResponse(text=self.response_text if self.response_text is not None else "中文 " + text,
                                provider=self.name, model=self.model)


class WrongNumericProvider(FakeProvider):
    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((asin, field, text))
        return ProviderResponse(text="250 ml", provider=self.name, model=self.model)


class EmptyProvider(FakeProvider):
    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((asin, field, text))
        return ProviderResponse(provider=self.name, model=self.model, status="success", text="")


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


def test_empty_provider_response_is_reported_as_qa_failure(tmp_path):
    result = TranslationService(EmptyProvider(), TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001", "title_es_raw": "Taladro"}
    ])
    field = result["records"]["B00000001"]["fields"]["title_zh"]
    assert field["translation_status"] == "qa_failed"
    assert {issue["code"] for issue in field["qa_issues"]} == {"EMPTY_TRANSLATION"}


def test_service_dry_run_never_calls_provider(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records(records(100), dry_run=True, limit=5)
    assert result["summary"]["total_records"] == 5
    assert not provider.calls


def test_service_consumes_preclean_fields_and_respects_admission(tmp_path):
    prepared = audit_records([{
        "asin": "B00000001",
        "title_es_raw": "Taladro 18V",
    }, {
        "asin": "B00000002",
        "title_es_raw": "Taladro 18V",
        "specification_es": "Taladro 18V",
    }])["translation_input_records"]
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    plan = service.plan(prepared)
    assert plan["total_fields"] == 1
    assert not provider.calls


def test_parallel_qa_failed_does_not_fail_over_to_provider_b(tmp_path):
    a, b = WrongNumericProvider(), FakeProvider()
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    service = TranslationService(FakeProvider(), TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records_parallel(
        [{"asin": "B00000003", "title_es_raw": "Capacidad 500 ml"}], pool)
    field = result["records"]["B00000003"]["fields"]["title_zh"]
    assert field["translation_status"] == "qa_failed"
    assert len(a.calls) == 1 and len(b.calls) == 0


def test_parallel_identity_detail_bypasses_both_providers(tmp_path):
    a, b = FakeProvider(), FakeProvider()
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    service = TranslationService(FakeProvider(), TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records_parallel([{
        "asin": "B00000004",
        "product_details_es": "Modelo: Cera Tec\nReferencia OEM: 20002\nMarca: HOVVIDA",
    }], pool)
    field = result["records"]["B00000004"]["fields"]["product_details_zh"]
    assert field["translation_status"] == "success"
    assert len(a.calls) == 0 and len(b.calls) == 0


def test_brand_is_identity_data_and_never_calls_provider(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records([{"asin": "B00000001", "brand": "HOVVIDA"}])
    field = result["records"]["B00000001"]["fields"]["brand_zh"]
    assert not provider.calls
    assert field["translated_text"] == "HOVVIDA"
    assert field["provider"] == "deterministic"


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


def test_persistent_translation_memory_reuses_same_text_for_new_asin(tmp_path):
    cache_path = tmp_path / "cache.json"
    provider1 = FakeProvider()
    TranslationService(provider1, TranslationCache(cache_path)).translate_records(
        [{"asin": "B00000001", "category_l1": "Hogar y cocina"}])
    provider2 = FakeProvider()
    result = TranslationService(provider2, TranslationCache(cache_path)).translate_records(
        [{"asin": "B00000002", "category_l1": "Hogar y cocina"}])
    assert len(provider1.calls) == 1
    assert not provider2.calls
    assert result["records"]["B00000002"]["category_l1_zh"]


def test_structured_bullets_are_translated_item_by_item(tmp_path):
    provider = FakeProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001", "feature_bullets_raw": ["Primero 500 ml", "Segundo USB-C"]}
    ])
    row = result["records"]["B00000001"]
    assert row["translation_status"] == "success"
    assert row["feature_bullets_zh"].count("\n") == 1
    assert len(provider.calls) == 2


def test_preclean_canonical_bullets_are_translated_item_by_item(tmp_path):
    prepared = audit_records([{
        "asin": "B00000001", "feature_bullets_es": ["Primero", "Segundo"]
    }])["translation_input_records"]
    provider = FakeProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records(prepared)
    assert len(provider.calls) == 2
    assert result["records"]["B00000001"]["translation_status"] == "success"


def test_structured_item_memory_is_reused_across_asins(tmp_path):
    cache_path = tmp_path / "cache.json"
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(cache_path))
    service.translate_records([{
        "asin": "B00000001", "feature_bullets_raw": ["Primero", "Segundo"]
    }])
    result = TranslationService(provider, TranslationCache(cache_path)).translate_records([{
        "asin": "B00000002", "feature_bullets_raw": ["Primero", "Tercero"]
    }])
    assert len(provider.calls) == 3
    assert result["records"]["B00000002"]["translation_status"] == "success"


def test_plan_counts_structured_items_and_deterministic_specs_correctly(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    structured = service.plan([{
        "asin": "B00000001", "feature_bullets_raw": ["Primero", "Segundo"]
    }])
    deterministic = service.plan([{
        "asin": "B00000002",
        "specification_es": "Dimensiones: 30 x 20 cm; Capacidad: 500 ml"
    }])
    assert structured["estimated_api_requests"] == 2
    assert deterministic["estimated_api_requests"] == 0


def test_plan_excludes_identity_detail_values_from_api_requests(tmp_path):
    service = TranslationService(FakeProvider(), TranslationCache(tmp_path / "cache.json"))
    planned = service.plan([{
        "asin": "B00000001",
        "product_details_es": "Modelo: Cera Tec\nColor: Negro\nDescripción: Producto resistente",
    }])
    assert planned["estimated_api_requests"] == 1


def test_structured_details_keep_labels_and_order(tmp_path):
    provider = FakeProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001", "detail_attributes_raw": [
            {"label_raw": "Material", "value_raw": "Acero 500 ml"},
            {"label_raw": "Color", "value_raw": "Rojo"},
        ]}
    ])
    text = result["records"]["B00000001"]["fields"]["product_details_zh"]["translated_text"]
    assert text.splitlines()[0].startswith("材质：")
    assert text.splitlines()[1].startswith("颜色：")
    assert len(provider.calls) == 1


def test_multiline_rendered_details_are_not_sent_as_one_free_article(tmp_path):
    provider = FakeProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001", "product_details_es": "Material: Acero\nColor: Rojo"}
    ])
    detail = result["records"]["B00000001"]["fields"]["product_details_zh"]
    assert len(provider.calls) == 0
    assert detail["translated_text"].splitlines()[0].startswith("材质：")


def test_identity_detail_values_bypass_provider_and_preserve_source(tmp_path):
    provider = FakeProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001", "product_details_es":
         "Modelo: Cera Tec\nReferencia OEM: 20002\nModelo: 26431\nFabricante: Energía Eléctrica Eficiente SL"}
    ])
    detail = result["records"]["B00000001"]["fields"]["product_details_zh"]
    assert not provider.calls
    assert detail["provider"] == "deterministic"
    assert detail["model"] == "identity-v1"
    assert detail["attempt_count"] == 0
    assert "型号：Cera Tec" in detail["translated_text"]
    assert "OEM参考号：20002" in detail["translated_text"]
    assert "型号：26431" in detail["translated_text"]
    assert "制造商：Energía Eléctrica Eficiente SL" in detail["translated_text"]
    assert all(item["provider"] == "deterministic" for item in detail["items"])


def test_mixed_identity_detail_only_sends_natural_language_value(tmp_path):
    provider = FakeProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001", "product_details_es":
         "Modelo: Cera Tec\nColor: Negro\nDescripción: Producto resistente"}
    ])
    detail = result["records"]["B00000001"]["fields"]["product_details_zh"]
    assert len(provider.calls) == 1
    assert provider.calls[0][2] == "Producto resistente"
    assert "型号：Cera Tec" in detail["translated_text"]


def test_variation_aliases_share_only_canonical_target(tmp_path):
    service = TranslationService(FakeProvider(), TranslationCache(tmp_path / "cache.json"))
    assert service.field_map["selected_variant_es"] == "selected_variation_zh"
    assert service.field_map["selected_variation_raw"] == "selected_variation_zh"
    assert service.field_map["variation_es"] == "selected_variation_zh"
    selected = service.selected_fields({"asin": "B00000001", "selected_variant_es": "Unidad"})
    assert selected == [("selected_variant_es", "selected_variation_zh", "Unidad")]
    assert all(target != "selected_variant_zh" for _, target, _ in selected)


def test_known_specification_uses_deterministic_rules_before_provider(tmp_path):
    provider = FakeProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001", "specification_es": "Dimensiones: 30 x 20 cm; Capacidad: 500 ml"}
    ])
    row = result["records"]["B00000001"]
    assert not provider.calls
    assert row["translation_status"] == "success"
    assert "尺寸" in row["specification_zh"] and "容量" in row["specification_zh"]


def test_repair_failed_bypasses_persistent_failed_memory(tmp_path):
    cache_path = tmp_path / "cache.json"
    first_provider = FakeProvider()
    TranslationService(first_provider, TranslationCache(cache_path)).translate_records([
        {"asin": "B00000001", "title_es_raw": "Para"}
    ])
    second_provider = FakeProvider()
    second_provider.response_text = "中文"
    result = TranslationService(second_provider, TranslationCache(cache_path)).translate_records([
        {"asin": "B00000002", "title_es_raw": "Para"}
    ], repair_failed=True)
    assert second_provider.calls
    assert result["records"]["B00000002"]["translation_status"] == "success"
