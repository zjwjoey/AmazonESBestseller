import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.pool import ProviderPool
from amazon_es_bestseller.translation.preclean import audit_records
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider
from amazon_es_bestseller.translation.service import TranslationService, source_hash


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


class PendingProvider(FakeProvider):
    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((asin, field, text))
        return ProviderResponse(provider=self.name, model=self.model,
                                status="pending", error="synthetic-pending")


class MissingProtectedTokenThenLiteralProvider(FakeProvider):
    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((asin, field, text))
        if "__T" in text:
            return ProviderResponse(text="中文", provider=self.name, model=self.model)
        return ProviderResponse(text="中文 UPF50+", provider=self.name, model=self.model)


class SlowProvider(FakeProvider):
    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls.append((asin, field, text))
        time.sleep(0.05)
        return ProviderResponse(text="中文 " + text, provider=self.name, model=self.model)


class ClaimInspectProvider(FakeProvider):
    """Fake provider that proves the durable claim exists at provider entry."""

    def __init__(self, cache_path, key, *, memory=False):
        super().__init__()
        self.cache_path = cache_path
        self.key = key
        self.memory = memory
        self.pending_seen = False

    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        cache = TranslationCache(self.cache_path)
        claimed = cache.get_memory(self.key) if self.memory else cache.get(self.key)
        self.pending_seen = bool(claimed and claimed.get("translation_status") == "pending"
                                 and claimed.get("attempt_state") == "claimed")
        return super().translate(text, asin=asin, field=field,
                                 source_language=source_language,
                                 target_language=target_language, context=context)


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


def test_source_missing_fields_are_neutral_for_record_status(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records(
        [{"asin": "B00000001", "title_es_raw": "Bolsa"}],
        fields=["title_es_raw", "description_es"],
    )
    row = result["records"]["B00000001"]
    assert row["fields"]["description_zh"]["translation_status"] == "source_missing"
    assert row["translation_status"] == "success"


def test_service_resolves_structured_unit_spec_without_provider_drift(tmp_path):
    provider = FakeProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([{
        "asin": "B00000001", "specification_es": "Voltaje: 9 Voltios / Peso: 495 Gramos",
    }])
    field = result["records"]["B00000001"]["fields"]["specification_zh"]
    assert not provider.calls
    assert field["provider"] == "deterministic"
    assert "9V" in field["translated_text"]
    assert "495克" in field["translated_text"]


def test_empty_provider_response_is_reported_as_qa_failure(tmp_path):
    result = TranslationService(EmptyProvider(), TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001", "title_es_raw": "Taladro"}
    ])
    field = result["records"]["B00000001"]["fields"]["title_zh"]
    assert field["translation_status"] == "qa_failed"
    assert {issue["code"] for issue in field["qa_issues"]} == {"EMPTY_TRANSLATION"}


def test_missing_protected_token_is_preserved_for_repair_without_provider_retry(tmp_path):
    provider = MissingProtectedTokenThenLiteralProvider()
    cache_path = tmp_path / "cache.json"
    result = TranslationService(provider, TranslationCache(cache_path)).translate_records([
        {"asin": "B00000001", "title_es_raw": "Parasol Protección UPF50+"}
    ])
    field = result["records"]["B00000001"]["fields"]["title_zh"]
    assert field["translation_status"] == "qa_failed"
    assert field["qa_status"] == "qa_failed"
    assert field["candidate_text"] == "中文"
    assert {issue["code"] for issue in field["qa_issues"]} == {"PROTECTED_TOKEN_MISSING"}
    assert len(provider.calls) == 1

    # The failed QA envelope is persisted and reused by a later run by
    # default. An explicit repair workflow owns any future provider call.
    resumed_provider = MissingProtectedTokenThenLiteralProvider()
    resumed = TranslationService(resumed_provider, TranslationCache(cache_path)).translate_records([
        {"asin": "B00000002", "title_es_raw": "Parasol Protección UPF50+"}
    ])
    resumed_field = resumed["records"]["B00000002"]["fields"]["title_zh"]
    assert resumed_field["translation_status"] == "qa_failed"
    assert resumed_field["candidate_text"] == field["candidate_text"]
    assert not resumed_provider.calls


def test_structured_missing_protected_token_is_preserved_for_repair_without_provider_retry(tmp_path):
    provider = MissingProtectedTokenThenLiteralProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001", "feature_bullets_raw": ["Protección UPF50+"]}
    ])
    field = result["records"]["B00000001"]["fields"]["feature_bullets_zh"]
    assert field["translation_status"] == "qa_failed"
    assert field["items"][0]["candidate_text"] == "中文"
    assert {issue["code"] for issue in field["qa_issues"]} == {"PROTECTED_TOKEN_MISSING"}
    assert len(provider.calls) == 1


def test_entries_only_qa_failed_field_cache_resumes_without_provider_call(tmp_path):
    cache_path = tmp_path / "cache.json"
    provider = MissingProtectedTokenThenLiteralProvider()
    service = TranslationService(provider, TranslationCache(cache_path))
    text = "Parasol Protección UPF50+"
    key = service.cache.key("B00000001", "title_es_raw", source_hash(text),
                            provider.name, provider.model, service.schema_version,
                            service.prompt_version, service.dictionary_version)
    # Simulate an older cache with only per-field entries: there is no
    # translation-memory record available to conceal a field-cache regression.
    service.cache.put(key, {
        "asin": "B00000001", "field": "title_es_raw", "target_field": "title_zh",
        "source_text": text, "source_hash": source_hash(text), "candidate_text": "中文",
        "translated_text": "中文", "translation_status": "qa_failed", "qa_status": "qa_failed",
        "qa_issues": [{"code": "PROTECTED_TOKEN_MISSING"}],
    })
    service.cache.save()

    resumed_provider = MissingProtectedTokenThenLiteralProvider()
    result = TranslationService(resumed_provider, TranslationCache(cache_path)).translate_records([
        {"asin": "B00000001", "title_es_raw": text}
    ])
    field = result["records"]["B00000001"]["fields"]["title_zh"]
    assert not resumed_provider.calls
    assert field["translation_status"] == "qa_failed"
    assert field["candidate_text"] == "中文"


def test_cache_reuses_immutable_result_after_dictionary_hash_change_and_restart(tmp_path):
    cache_path = tmp_path / "cache.json"
    original_provider = FakeProvider()
    original = TranslationService(
        original_provider, TranslationCache(cache_path),
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "hash-a"},
    ).translate_records([{"asin": "B00000001", "title_es_raw": "Taladro compacto"}])
    original_field = original["records"]["B00000001"]["fields"]["title_zh"]
    assert len(original_provider.calls) == 1

    # Persist the original process cache then recreate the process with a new
    # dictionary content hash. The old raw provider candidate remains intact,
    # but it is review-required rather than silently certified as a new render.
    resumed_provider = FakeProvider()
    resumed = TranslationService(
        resumed_provider, TranslationCache(cache_path),
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "hash-b"},
    ).translate_records([{"asin": "B00000001", "title_es_raw": "Taladro compacto"}])
    field = resumed["records"]["B00000001"]["fields"]["title_zh"]
    assert not resumed_provider.calls
    assert field["translation_status"] == "pending"
    assert field["qa_status"] == "review_required"
    assert field["candidate_text"] == original_field["candidate_text"]
    persisted = TranslationCache(cache_path)
    raw = persisted.find_result("B00000001", "title_es_raw", source_hash("Taladro compacto"))
    assert raw["candidate_text"] == original_field["candidate_text"]
    assert raw["translation_status"] == "success"


def test_entries_only_qa_failed_result_stops_new_dictionary_namespace_call(tmp_path):
    cache_path = tmp_path / "cache.json"
    text = "Parasol Protecci��n UPF50+"
    provider = MissingProtectedTokenThenLiteralProvider()
    original = TranslationService(
        provider, TranslationCache(cache_path),
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "hash-a"},
    )
    key = original._field_cache_key("B00000001", "title_es_raw", source_hash(text))
    original.cache.entries[key] = {
        "asin": "B00000001", "field": "title_es_raw", "target_field": "title_zh",
        "source_text": text, "source_hash": source_hash(text), "candidate_text": "����",
        "translated_text": "����", "translation_status": "qa_failed", "qa_status": "qa_failed",
        "qa_issues": [{"code": "PROTECTED_TOKEN_MISSING"}],
    }
    original.cache.save()

    resumed_provider = MissingProtectedTokenThenLiteralProvider()
    resumed = TranslationService(
        resumed_provider, TranslationCache(cache_path),
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "hash-b"},
    ).translate_records([{"asin": "B00000001", "title_es_raw": text}])
    field = resumed["records"]["B00000001"]["fields"]["title_zh"]
    assert not resumed_provider.calls
    assert field["translation_status"] == "qa_failed"
    assert field["candidate_text"] == "����"


def test_cross_asin_tm_stops_provider_repeat_when_namespace_changes(tmp_path):
    cache_path = tmp_path / "cache.json"
    first_provider = FakeProvider()
    TranslationService(
        first_provider, TranslationCache(cache_path),
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "hash-a"},
    ).translate_records([{"asin": "B00000001", "title_es_raw": "Bolsa aislante"}])
    assert len(first_provider.calls) == 1
    # Service writes atomically at translate_records completion; this reload is
    # deliberately a fresh process boundary.
    resumed_provider = FakeProvider()
    result = TranslationService(
        resumed_provider, TranslationCache(cache_path),
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "hash-b"},
    ).translate_records([{"asin": "B00000002", "title_es_raw": "Bolsa aislante"}])
    assert not resumed_provider.calls
    reused = result["records"]["B00000002"]["fields"]["title_zh"]
    assert reused["translation_status"] == "pending"
    assert reused["qa_status"] == "review_required"


def test_new_source_hash_is_plan_only_in_no_repeat_slice(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    plan = service.plan([{"asin": "B00000001", "title_es_raw": "Nuevo texto sin resultado"}])
    assert plan["estimated_api_requests"] == 1
    assert not provider.calls


def test_pending_prior_attempt_requires_manual_resume_not_provider_retry(tmp_path):
    cache_path = tmp_path / "cache.json"
    text = "Pendiente de recuperar"
    original = TranslationService(FakeProvider(), TranslationCache(cache_path))
    original.cache.put(original._field_cache_key("B00000001", "title_es_raw", source_hash(text)), {
        "asin": "B00000001", "field": "title_es_raw", "target_field": "title_zh",
        "source_text": text, "source_hash": source_hash(text), "candidate_text": "",
        "translated_text": "", "translation_status": "pending", "qa_status": "pending",
        "last_error": "NO_HEALTHY_PROVIDER", "qa_issues": [],
    })
    original.cache.save()
    resumed_provider = FakeProvider()
    resumed = TranslationService(
        resumed_provider, TranslationCache(cache_path),
        dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "changed"},
    ).translate_records([{"asin": "B00000001", "title_es_raw": text}])
    field = resumed["records"]["B00000001"]["fields"]["title_zh"]
    assert not resumed_provider.calls
    assert field["translation_status"] == "pending"
    assert field["qa_status"] == "review_required"


def test_provider_entry_observes_durable_field_claim(tmp_path):
    cache_path = tmp_path / "cache.json"
    seed = TranslationService(FakeProvider(), TranslationCache(cache_path))
    text = "Taladro durable"
    key = seed._field_cache_key("B00000001", "title_es_raw", source_hash(text))
    provider = ClaimInspectProvider(cache_path, key)
    TranslationService(provider, TranslationCache(cache_path)).translate_records([
        {"asin": "B00000001", "title_es_raw": text}
    ])
    assert provider.pending_seen
    assert len(provider.calls) == 1


def test_structured_provider_entry_observes_durable_item_claim(tmp_path):
    cache_path = tmp_path / "cache.json"
    seed = TranslationService(FakeProvider(), TranslationCache(cache_path))
    key = seed._memory_key("Detalle durable", "feature_bullets")
    provider = ClaimInspectProvider(cache_path, key, memory=True)
    TranslationService(provider, TranslationCache(cache_path)).translate_records([
        {"asin": "B00000001", "feature_bullets_raw": ["Detalle durable"]}
    ])
    assert provider.pending_seen
    assert len(provider.calls) == 1


def test_crash_after_provider_response_leaves_durable_pending_without_repeat(tmp_path):
    cache_path = tmp_path / "cache.json"
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(cache_path))
    service._after_provider_response = lambda _response: (_ for _ in ()).throw(RuntimeError("simulated crash"))
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.translate_records([{"asin": "B00000001", "title_es_raw": "Taladro crash"}])
    resumed_provider = FakeProvider()
    resumed = TranslationService(resumed_provider, TranslationCache(cache_path)).translate_records([
        {"asin": "B00000001", "title_es_raw": "Taladro crash"}
    ])
    assert not resumed_provider.calls
    assert resumed["records"]["B00000001"]["fields"]["title_zh"]["translation_status"] == "pending"


def test_two_services_same_tm_unit_make_one_provider_call(tmp_path):
    cache_path = tmp_path / "cache.json"
    provider = SlowProvider()
    start = threading.Barrier(2)
    def run(asin):
        start.wait(timeout=2)
        return TranslationService(provider, TranslationCache(cache_path)).translate_records([
            {"asin": asin, "title_es_raw": "Unidad TM concurrente"}
        ])
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(run, ["B00000001", "B00000002"]))
    assert len(provider.calls) == 1
    verifier = TranslationService(FakeProvider(), TranslationCache(cache_path))
    loser_key = verifier._field_cache_key("B00000002", "title_es_raw", source_hash("Unidad TM concurrente"))
    loser = TranslationCache(cache_path).get(loser_key)
    assert loser["translation_status"] == "cached"
    assert loser["source_hash"] == source_hash("Unidad TM concurrente")


def test_structured_first_item_settles_before_second_item_crash(tmp_path):
    cache_path = tmp_path / "cache.json"
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(cache_path))
    seen = {"count": 0}
    def crash_second(_response):
        seen["count"] += 1
        if seen["count"] == 2:
            raise RuntimeError("second item crash")
    service._after_provider_response = crash_second
    record = {"asin": "B00000001", "feature_bullets_raw": ["Primero durable", "Segundo durable"]}
    with pytest.raises(RuntimeError, match="second item crash"):
        service.translate_records([record])
    resumed_provider = FakeProvider()
    resumed = TranslationService(resumed_provider, TranslationCache(cache_path)).translate_records([record])
    items = resumed["records"]["B00000001"]["fields"]["feature_bullets_zh"]["items"]
    assert not resumed_provider.calls
    assert items[0]["translation_status"] == "success"
    assert items[1]["translation_status"] == "pending"


def test_structured_cross_namespace_memory_success_becomes_review_without_call(tmp_path):
    cache_path = tmp_path / "cache.json"
    first = FakeProvider()
    TranslationService(first, TranslationCache(cache_path),
                       dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "a"}).translate_records([
                           {"asin": "B00000001", "feature_bullets_raw": ["Detalle compartido"]}
                       ])
    resumed_provider = FakeProvider()
    resumed = TranslationService(resumed_provider, TranslationCache(cache_path),
                                 dictionary_manifest={"dictionary_version": "1", "dictionary_hash": "b"}).translate_records([
                                     {"asin": "B00000002", "feature_bullets_raw": ["Detalle compartido"]}
                                 ])
    item = resumed["records"]["B00000002"]["fields"]["feature_bullets_zh"]["items"][0]
    assert not resumed_provider.calls
    assert item["translation_status"] == "pending"
    assert {issue["code"] for issue in item["qa_issues"]} == {"CACHE_NAMESPACE_REVIEW_REQUIRED"}


def test_structured_failed_item_settles_error_and_does_not_repeat(tmp_path):
    cache_path = tmp_path / "cache.json"
    first = FakeProvider(fail_fields={"feature_bullets_raw"})
    TranslationService(first, TranslationCache(cache_path)).translate_records([
        {"asin": "B00000001", "feature_bullets_raw": ["Fallo durable"]}
    ])
    key = TranslationService(FakeProvider(), TranslationCache(cache_path))._memory_key(
        "Fallo durable", "feature_bullets")
    persisted = TranslationCache(cache_path).get_memory(key)
    assert persisted["translation_status"] == "failed"
    assert persisted["last_error"] == "synthetic"
    resumed = FakeProvider()
    TranslationService(resumed, TranslationCache(cache_path)).translate_records([
        {"asin": "B00000001", "feature_bullets_raw": ["Fallo durable"]}
    ])
    assert not resumed.calls


def test_structured_empty_and_pending_items_settle_before_resume(tmp_path):
    for provider_cls, text, expected in (
        (EmptyProvider, "Vacio durable", "qa_failed"),
        (PendingProvider, "Pendiente durable", "pending"),
    ):
        cache_path = tmp_path / (expected + ".json")
        first = provider_cls()
        TranslationService(first, TranslationCache(cache_path)).translate_records([
            {"asin": "B00000001", "feature_bullets_raw": [text]}
        ])
        key = TranslationService(FakeProvider(), TranslationCache(cache_path))._memory_key(text, "feature_bullets")
        persisted = TranslationCache(cache_path).get_memory(key)
        assert persisted["translation_status"] == expected
        if expected == "pending":
            assert persisted["last_error"] == "synthetic-pending"
        resumed = FakeProvider()
        TranslationService(resumed, TranslationCache(cache_path)).translate_records([
            {"asin": "B00000001", "feature_bullets_raw": [text]}
        ])
        assert not resumed.calls


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
    assert plan["total_fields"] == 3
    assert plan["review_blocked"] == 0
    assert not provider.calls


def test_plan_counts_preclean_gate_fields_when_other_fields_are_selected(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    prepared = [{
        "asin": "B00000001",
        "fields": {
            "title_es_raw": {"source_text": "Taladro", "clean_text": "Taladro",
                              "translate_allowed": True},
            "product_details": {"source_text": "Color: Rojo", "clean_text": "Color: Rojo",
                                 "translate_allowed": True},
            "feature_bullets": {"source_text": "Primero", "clean_text": "Primero",
                                 "translate_allowed": True},
            "specification_es": {"source_text": "Tamaño ambiguo", "clean_text": "",
                                  "translate_allowed": False},
            "product_description": {"source_text": "", "clean_text": "",
                                     "translate_allowed": False},
        },
    }]
    plan = service.plan(prepared, fields=["title_es_raw", "product_details",
                                           "feature_bullets", "specification_es",
                                           "product_description"])
    assert plan["review_blocked"] == 1
    assert plan["source_missing"] == 1
    assert plan["total_fields"] >= 1


def test_translation_preserves_preclean_review_blocked_source(tmp_path):
    provider = FakeProvider()
    provider.response_text = "中文"
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    prepared = [{
        "asin": "B00000001",
        "fields": {
            "title_es_raw": {"source_text": "Taladro", "clean_text": "Taladro",
                              "translate_allowed": True},
            "specification_es": {"source_text": "Tamaño ambiguo", "clean_text": "",
                                  "clean_status": "NEEDS_REVIEW",
                                  "issues": ["CROSS_FIELD_OVERLAP"],
                                  "translate_allowed": False},
        },
    }]
    result = service.translate_records(
        prepared, fields=["title_es_raw", "specification_es"])
    row = result["records"]["B00000001"]
    blocked = row["fields"]["specification_zh"]
    assert blocked["translation_status"] == "preclean_blocked"
    assert blocked["source_text"] == "Tamaño ambiguo"
    assert blocked["resolution_source"] == "preclean_review"
    assert row["translation_status"] == "partial"
    assert result["qa_report"]["counts"]["preclean_blocked"] == 1


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


def test_preclean_brand_is_used_for_protection_across_scalar_translation(tmp_path):
    for brand in ("Metal", "Bosch", "Rain-X", "CeraVe"):
        prepared = audit_records([{
            "asin": "B00000001", "brand": brand,
            "title_es_raw": "Producto %s profesional" % brand,
        }])["translation_input_records"]
        provider = FakeProvider()
        result = TranslationService(provider, TranslationCache(tmp_path / (brand.replace("-", "") + ".json"))).translate_records(prepared)
        field = result["records"]["B00000001"]["fields"]["title_zh"]
        provider_input = provider.calls[0][2]
        assert brand not in provider_input
        assert "__T" in provider_input
        # Brand identity remains in the dedicated brand/source fields; the
        # Chinese display name follows the no-brand presentation policy.
        assert brand not in field["translated_text"]


def test_parallel_unknown_category_deduplicates_across_levels(tmp_path):
    a, b = SlowProvider(), SlowProvider()
    a.name, b.name = "qwen-a", "qwen-b"
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    service = TranslationService(FakeProvider(), TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records_parallel([
        {"asin": "B00000001", "category_l1": "Categoria futura XYZ"},
        {"asin": "B00000002", "category_l2": "Categoria futura XYZ"},
    ], pool)
    assert len(a.calls) + len(b.calls) == 1
    assert result["records"]["B00000001"]["category_l1_zh"]
    assert result["records"]["B00000002"]["category_l2_zh"]


def test_parallel_same_title_deduplicates_across_asins_but_not_other_fields(tmp_path):
    a, b = SlowProvider(), SlowProvider()
    a.name, b.name = "qwen-a", "qwen-b"
    pool = ProviderPool({"qwen-a": a, "qwen-b": b})
    service = TranslationService(FakeProvider(), TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records_parallel([
        {"asin": "B00000001", "title_es_raw": "Producto especial XYZ"},
        {"asin": "B00000002", "title_es_raw": "Producto especial XYZ"},
        {"asin": "B00000003", "description_es": "Producto especial XYZ"},
    ], pool)
    assert len(a.calls) + len(b.calls) == 2
    assert result["records"]["B00000001"]["fields"]["title_zh"]["translated_text"]


def test_category_translation_memory_is_shared_across_levels(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    row = {"asin": "B00000001", "category_l1": "Hogar y cocina",
           "category_l2": "Hogar y cocina"}
    result = service.translate_records([row])
    assert result["records"]["B00000001"]["fields"]["category_l1_zh"]["translated_text"] == \
           result["records"]["B00000001"]["fields"]["category_l2_zh"]["translated_text"]
    assert len(provider.calls) == 0


def test_scalar_dictionary_resolution_precedes_provider(tmp_path):
    provider = FakeProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([{
        "asin": "B00000001", "category_l1": "Hogar y cocina",
        "category_l2": "Hogar y cocina", "selected_variant_es": "Unidad",
    }])
    row = result["records"]["B00000001"]
    assert not provider.calls
    assert row["category_l1_zh"] == "家居与厨房"
    assert row["category_l2_zh"] == "家居与厨房"
    assert row["selected_variation_zh"] == "单件"
    assert row["fields"]["selected_variation_zh"]["resolution_source"] == "dictionary"


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
        [{"asin": "B00000001", "category_l1": "Categoria futura XYZ"}])
    provider2 = FakeProvider()
    result = TranslationService(provider2, TranslationCache(cache_path)).translate_records(
        [{"asin": "B00000002", "category_l1": "Categoria futura XYZ"}])
    assert len(provider1.calls) == 1
    assert not provider2.calls
    assert result["records"]["B00000002"]["category_l1_zh"]


def test_exact_cached_source_hash_does_not_call_provider_in_new_service_run(tmp_path):
    cache_path = tmp_path / "cache.json"
    record = {"asin": "B00000001", "category_l1": "Categoria futura XYZ"}
    initial_provider = FakeProvider()
    TranslationService(initial_provider, TranslationCache(cache_path)).translate_records([record])
    resumed_provider = FakeProvider()
    resumed = TranslationService(resumed_provider, TranslationCache(cache_path)).translate_records([record])
    field = resumed["records"]["B00000001"]["fields"]["category_l1_zh"]
    assert len(initial_provider.calls) == 1
    assert not resumed_provider.calls
    assert field["translation_status"] == "cached"


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


def test_structured_detail_tm_shares_only_the_same_label_semantic(tmp_path):
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records([
        {"asin": "B00000001", "product_details_es": "Color: Natural"},
        {"asin": "B00000002", "product_details_es": "Color: Natural"},
        {"asin": "B00000003", "product_details_es": "Material: Natural"},
    ])
    assert len(provider.calls) == 2
    assert result["records"]["B00000002"]["fields"]["product_details_zh"]["items"][0]["resolution_source"] == "cached"


def test_structured_detail_tm_does_not_share_different_labels_within_one_sku(tmp_path):
    provider = FakeProvider()
    TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([{
        "asin": "B00000001", "product_details_es": "Color: Normal\nTipo: Normal"
    }])
    assert len(provider.calls) == 2


def test_structured_detail_tm_ignores_detail_position_for_same_label(tmp_path):
    provider = FakeProvider()
    TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001", "product_details_es": "Modelo: Cera Tec\nColor: Natural"},
        {"asin": "B00000002", "product_details_es": "Color: Natural"},
    ])
    assert len(provider.calls) == 1


def test_structured_detail_persistent_tm_is_scoped_by_label(tmp_path):
    cache_path = tmp_path / "cache.json"
    first = FakeProvider()
    TranslationService(first, TranslationCache(cache_path)).translate_records([{
        "asin": "B00000001", "product_details_es": "Color: Natural"
    }])
    same_label = FakeProvider()
    TranslationService(same_label, TranslationCache(cache_path)).translate_records([{
        "asin": "B00000002", "product_details_es": "Color: Natural"
    }])
    different_label = FakeProvider()
    TranslationService(different_label, TranslationCache(cache_path)).translate_records([{
        "asin": "B00000003", "product_details_es": "Material: Natural"
    }])
    assert len(first.calls) == 1
    assert not same_label.calls
    assert len(different_label.calls) == 1


def test_structured_detail_plan_matches_actual_semantic_request_count(tmp_path):
    records = [
        {"asin": "B00000001", "product_details_es": "Color: Natural"},
        {"asin": "B00000002", "product_details_es": "Color: Natural"},
        {"asin": "B00000003", "product_details_es": "Material: Natural"},
    ]
    provider = FakeProvider()
    service = TranslationService(provider, TranslationCache(tmp_path / "cache.json"))
    assert service.plan(records)["estimated_api_requests"] == 2
    service.translate_records(records)
    assert len(provider.calls) == 2


def test_structured_identity_and_dictionary_values_stay_before_provider(tmp_path):
    provider = FakeProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001", "product_details_es": "Modelo: Cera Tec"},
        {"asin": "B00000002", "product_details_es": "Modelo: Cera Tec"},
        {"asin": "B00000003", "product_details_es": "Color: Negro"},
    ])
    assert not provider.calls
    assert "型号：Cera Tec" in result["records"]["B00000001"]["product_details_zh"]
    assert "颜色：黑色" in result["records"]["B00000003"]["product_details_zh"]


def test_parallel_structured_detail_inflight_dedup_respects_label_semantic(tmp_path):
    same_a, same_b = SlowProvider(), SlowProvider()
    same_a.name, same_b.name = "qwen-a", "qwen-b"
    same_pool = ProviderPool({"qwen-a": same_a, "qwen-b": same_b})
    same_service = TranslationService(FakeProvider(), TranslationCache(tmp_path / "same.json"))
    same_service.translate_records_parallel([
        {"asin": "B00000001", "product_details_es": "Color: Natural"},
        {"asin": "B00000002", "product_details_es": "Color: Natural"},
    ], same_pool)
    assert len(same_a.calls) + len(same_b.calls) == 1

    different_a, different_b = SlowProvider(), SlowProvider()
    different_a.name, different_b.name = "qwen-a", "qwen-b"
    different_pool = ProviderPool({"qwen-a": different_a, "qwen-b": different_b})
    different_service = TranslationService(FakeProvider(), TranslationCache(tmp_path / "different.json"))
    different_service.translate_records_parallel([
        {"asin": "B00000003", "product_details_es": "Color: Natural"},
        {"asin": "B00000004", "product_details_es": "Material: Natural"},
    ], different_pool)
    assert len(different_a.calls) + len(different_b.calls) == 2


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


def test_deterministic_specification_qa_blocks_lost_thickness(tmp_path):
    provider = FakeProvider()
    result = TranslationService(provider, TranslationCache(tmp_path / "cache.json")).translate_records([
        {"asin": "B00000001",
         "specification_es": "Dimensiones del producto: 120l. x 80an. x 0,5Grosor centímetros"}
    ])
    field = result["records"]["B00000001"]["fields"]["specification_zh"]
    assert field["translation_status"] == "success"
    assert field["qa_status"] == "pass"
    assert field["translated_text"] == "产品尺寸：120×80×0.5厘米"


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
