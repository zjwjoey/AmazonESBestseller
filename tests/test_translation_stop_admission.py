"""Offline stop/drain fixtures: no production cache, credentials or HTTP."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.pool import ProviderPool, TranslationTask
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider
from amazon_es_bestseller.translation.providers.qwen_mt import QwenMTProvider
from amazon_es_bestseller.translation.service import TranslationService


class StopFake(TranslationProvider):
    name = "qwen-mt"
    model = "fixture"

    def __init__(self, hook=None):
        self.calls = []
        self.hook = hook

    def translate(self, text, **kwargs):
        self.calls.append((kwargs["asin"], kwargs["field"]))
        if self.hook:
            response = self.hook(text, kwargs)
            if response is not None:
                return response
        return ProviderResponse(text="测试商品", provider=self.name, model=self.model)


def records(n=20):
    return [{"asin": f"B{i:09d}", "title_es_raw": f"Producto especial letra {chr(65+i)}"}
            for i in range(n)]


def service(tmp_path, provider, **kwargs):
    return TranslationService(provider, TranslationCache(tmp_path / "cache.json"), **kwargs)


@pytest.mark.parametrize("parallel", [False, True])
def test_preexisting_halt_admits_no_records_or_claims(tmp_path, parallel):
    halt = tmp_path / "halt.json"
    halt.write_text('{"reason":"manual"}', encoding="utf-8")
    original = halt.read_bytes()
    provider = StopFake()
    pool = ProviderPool({"A": provider}, halt_path=halt)
    svc = service(tmp_path, provider, stop_requested=pool.admission_stopped)
    result = (svc.translate_records_parallel(records(), pool) if parallel
              else svc.translate_records(records()))
    assert result["records"] == {}
    assert result["admission"]["not_started_asins"] == [r["asin"] for r in records()]
    assert not provider.calls and not svc.cache.entries and not svc.cache.memory
    assert halt.read_bytes() == original


def test_serial_stop_saves_response_without_claiming_next_field_or_record(tmp_path):
    stopped = threading.Event()
    provider = StopFake(lambda *_: stopped.set())
    svc = service(tmp_path, provider, stop_requested=stopped.is_set)
    inputs = records()
    inputs[0]["description_es"] = "Texto largo para uso cotidiano"
    original = copy.deepcopy(inputs)
    result = svc.translate_records(inputs)
    row = result["records"][inputs[0]["asin"]]
    assert row["translation_status"] == "partial"
    assert row["fields"]["title_zh"]["candidate_text"] == "测试商品"
    assert row["fields"]["description_zh"]["translation_status"] == "not_started"
    assert len(provider.calls) == len(svc.cache.entries) == len(svc.cache.memory) == 1
    assert result["admission"]["not_started_asins"] == [r["asin"] for r in inputs[1:]]
    assert inputs == original
    saved = json.loads(svc.cache.path.read_text(encoding="utf-8"))
    assert next(iter(saved["entries"].values()))["candidate_text"] == "测试商品"


@pytest.mark.parametrize("field,value,target", [
    ("feature_bullets_es", ["Primera frase especial", "Segunda frase distinta"], "feature_bullets_zh"),
    ("product_details_es", [{"label": "Uso especial", "value": "Primera frase especial"},
                            {"label": "Nota especial", "value": "Segunda frase distinta"}], "product_details_zh"),
])
def test_structured_stop_keeps_completed_child_and_does_not_claim_siblings(tmp_path, field, value, target):
    stopped = threading.Event()
    provider = StopFake(lambda *_: stopped.set())
    svc = service(tmp_path, provider, stop_requested=stopped.is_set)
    inputs = [{"asin": "B000000001", field: value}]
    result = svc.translate_records(inputs, fields=[field])
    envelope = result["records"]["B000000001"]["fields"][target]
    assert envelope["translation_status"] == "partial"
    assert [i["translation_status"] for i in envelope["items"]] == ["success", "not_started"]
    assert len(provider.calls) == len(svc.cache.memory) == 1
    # Do not cache an interrupted parent as a terminal render. Child evidence
    # remains durable and reconstructs the parent on an explicit subsequent run.
    assert not svc.cache.entries
    stopped.clear()
    provider.hook = None
    svc.translate_records(inputs, fields=[field])
    assert len(provider.calls) == 2


def test_parallel_stop_drains_two_inflight_and_never_admits_remaining_records(tmp_path):
    halt = tmp_path / "halt.json"
    barrier = threading.Barrier(2)
    first_done = threading.Event()
    def rate_limit(*_):
        barrier.wait(5)
        return ProviderResponse(status="failed", error="HTTP 429", attempts=1)
    def delayed_success(*_):
        barrier.wait(5)
        assert first_done.wait(5)
    a, b = StopFake(rate_limit), StopFake(delayed_success)
    pool = ProviderPool({"A": a, "B": b}, failover=False,
                        stop_on_rate_limit=True, halt_path=halt)
    svc = service(tmp_path, a)
    # Signal only after the rate-limited response has been durably settled.
    original_settle = svc.cache.settle
    def settle(key, value):
        original_settle(key, value)
        if value.get("last_error") == "HTTP 429":
            first_done.set()
    svc.cache.settle = settle
    inputs = records(20)
    result = svc.translate_records_parallel(inputs, pool)
    assert len(a.calls) == len(b.calls) == 1
    assert len(result["records"]) == len(svc.cache.entries) == len(svc.cache.memory) == 2
    assert len(result["admission"]["not_started_asins"]) == 18
    assert any(row["fields"]["title_zh"]["candidate_text"] == "测试商品"
               for row in result["records"].values())
    assert pool.snapshot()["admission_stopped"] is True
    assert halt.exists()


def test_no_healthy_provider_stops_service_before_more_claims(tmp_path):
    provider = StopFake(lambda *_: ProviderResponse(status="failed", error="invalid request"))
    pool = ProviderPool({"A": provider})
    svc = service(tmp_path, provider)
    result = svc.translate_records_parallel(records(20), pool)
    assert len(provider.calls) == len(svc.cache.memory) == len(result["records"]) == 1
    assert len(result["admission"]["not_started_asins"]) == 19


def test_durable_halt_after_pool_creation_without_shared_rate_is_checked(tmp_path):
    provider = StopFake()
    halt = tmp_path / "halt.json"
    pool = ProviderPool({"A": provider}, halt_path=halt)
    halt.write_text('{}', encoding="utf-8")
    response = pool.submit([TranslationTask.from_values("texto", asin="TEST", field="title")])[0]
    assert not provider.calls
    assert response.response.attempts == 0


def test_transport_send_gate_composes_existing_callback_and_durable_stop(tmp_path):
    halt = tmp_path / "halt.json"
    sent = []
    provider = QwenMTProvider(api_key="fixture", rate=0, max_retries=0,
                             transport=lambda *args: sent.append(True))
    def earlier_gate():
        halt.write_text('{}', encoding="utf-8")
        return True
    provider.before_send = earlier_gate
    pool = ProviderPool({"A": provider}, halt_path=halt)
    response = pool.submit([TranslationTask.from_values("texto", asin="TEST", field="title")])[0]
    assert not sent
    assert response.response.attempts == 0
    assert response.response.error == "PROVIDER_HALTED_BEFORE_SEND"


@pytest.mark.parametrize("parallel", [False, True])
def test_unknown_send_outcome_stays_pending_and_is_not_replayed(tmp_path, parallel):
    def unknown(*_):
        raise TimeoutError("fixture outcome unknown")
    provider = StopFake(unknown)
    svc = service(tmp_path, provider)
    pool = ProviderPool({"A": provider})
    inputs = records(1)
    def run():
        return (svc.translate_records_parallel(inputs, pool, repair_failed=True) if parallel
                else svc.translate_records(inputs, repair_failed=True))
    result = run()
    assert result["records"][inputs[0]["asin"]]["translation_status"] == "pending"
    assert next(iter(svc.cache.memory.values()))["last_error"] == "TRANSPORT_OUTCOME_UNKNOWN"
    run()
    assert len(provider.calls) == 1


def test_stop_probe_error_fails_closed_and_clone_retains_probe(tmp_path):
    def unavailable():
        raise OSError("fixture")
    provider = StopFake()
    svc = service(tmp_path, provider, stop_requested=unavailable)
    clone = svc.with_structured_schema_version("fixture-v2")
    assert clone.translate_records(records())["records"] == {}
    assert not provider.calls and not svc.cache.memory


def test_stop_between_scalar_claims_does_not_create_memory_claim(tmp_path):
    stopped = threading.Event()
    provider = StopFake()
    svc = service(tmp_path, provider, stop_requested=stopped.is_set)
    original_claim = svc.cache.claim
    def claim(*args, **kwargs):
        result = original_claim(*args, **kwargs)
        stopped.set()
        return result
    svc.cache.claim = claim
    result = svc.translate_records(records(2))
    assert not provider.calls and not svc.cache.memory
    assert len(svc.cache.entries) == 1  # Preserve the already-published claim.
    assert next(iter(svc.cache.entries.values()))["attempt_state"] == "claimed"
    assert result["records"][records(1)[0]["asin"]]["translation_status"] == "pending"
    assert result["records"][records(1)[0]["asin"]]["fields"]["title_zh"]["last_error"] == "ADMISSION_STOPPED_AFTER_CLAIM"


def test_stop_during_structured_lookup_does_not_publish_new_claim(tmp_path, monkeypatch):
    stopped = threading.Event()
    provider = StopFake()
    svc = service(tmp_path, provider, stop_requested=stopped.is_set)
    def lookup(*args, **kwargs):
        stopped.set()
        return None
    monkeypatch.setattr(svc, "_memory_lookup", lookup)
    inputs = [{"asin": "B000000001", "feature_bullets_es": ["Frase especial", "Otra frase"]}]
    result = svc.translate_records(inputs)
    envelope = result["records"]["B000000001"]["fields"]["feature_bullets_zh"]
    assert [i["translation_status"] for i in envelope["items"]] == ["not_started", "not_started"]
    assert not provider.calls and not svc.cache.memory and not svc.cache.entries


def test_stop_on_first429_prevents_internal_qwen_retries(tmp_path):
    sent = []
    def transport(*args):
        sent.append(True)
        return {"status_code": 429, "body": {}}
    provider = QwenMTProvider(api_key="fixture", rate=0, max_retries=2,
                             backoff_seconds=0, transport=transport)
    pool = ProviderPool({"A": provider}, stop_on_rate_limit=True, halt_path=tmp_path / "halt.json")
    response = pool.submit([TranslationTask.from_values("texto", asin="TEST", field="title")])[0]
    assert len(sent) == response.response.attempts == 1
    assert pool.admission_stopped()


def test_default_known429_retry_policy_is_preserved_without_stop_policy():
    sent = []
    def transport(*args):
        sent.append(True)
        return {"status_code": 429 if len(sent) == 1 else 200, "body": {"text": "测试商品"}}
    provider = QwenMTProvider(api_key="fixture", rate=0, max_retries=2,
                             backoff_seconds=0, transport=transport)
    result = ProviderPool({"A": provider}).submit(
        [TranslationTask.from_values("texto", asin="TEST", field="title")])[0]
    assert result.response.status == "success"
    assert len(sent) == result.response.attempts == 2


def test_latched_halt_is_not_cleared_by_marker_deletion(tmp_path):
    halt = tmp_path / "halt.json"
    provider = StopFake()
    pool = ProviderPool({"A": provider}, halt_path=halt)
    halt.write_text('{}', encoding="utf-8")
    assert pool.admission_stopped()
    halt.unlink()  # Synthetic fixture only.
    pool.submit([TranslationTask.from_values("texto", asin="TEST", field="title")])
    assert not provider.calls and pool.admission_stopped()


def test_no_stop_parallel_preserves_order_and_selected_offset_limit(tmp_path):
    a, b = StopFake(), StopFake()
    pool = ProviderPool({"A": a, "B": b})
    svc = service(tmp_path, a)
    result = svc.translate_records_parallel(records(12), pool, offset=2, limit=5)
    assert list(result["records"]) == [r["asin"] for r in records(12)[2:7]]
    assert result["admission"] == {"selected": 5, "started": 5,
                                    "stop_requested": False, "not_started_asins": []}
    assert len(a.calls) + len(b.calls) == 5


def test_dry_run_with_stop_makes_no_claim_or_provider_entry(tmp_path):
    provider = StopFake()
    svc = service(tmp_path, provider, stop_requested=lambda: True)
    pool = ProviderPool({"A": provider})
    result = svc.translate_records_parallel(records(5), pool, dry_run=True)
    assert result["qa_report"]["status"] == "dry_run"
    assert not provider.calls and not svc.cache.entries and not svc.cache.memory
