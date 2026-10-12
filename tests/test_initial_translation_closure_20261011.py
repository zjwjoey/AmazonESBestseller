"""Offline regressions for the formal source pool's initial translation defects."""
import hashlib
import json
from copy import deepcopy

import pytest

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.providers.qwen_mt import QwenMTProvider
from amazon_es_bestseller.translation.service import TranslationService


def provider(tmp_path, transport):
    from amazon_es_bestseller.translation.raw_journal import RawResponseJournal
    return QwenMTProvider(api_key="secret-test-key", rate=0, max_retries=0,
                          transport=transport, response_journal=RawResponseJournal(tmp_path / "raw"))


def test_raw_response_survives_empty_content_and_preserves_usage(tmp_path):
    body = {"id": "request-123", "choices": [{"finish_reason": "length", "message": {"content": ""}}],
            "usage": {"prompt_tokens": 13, "completion_tokens": 19}}
    result = provider(tmp_path, lambda *_: {"status_code": 200, "body": body}).translate(
        "Hola", asin="B000000001", field="product_details", context={"item_index": 2, "source_hash": "abc"})
    assert result.error == "EMPTY_TRANSLATION"
    refs = result.raw["raw_response_refs"]
    assert len(refs) == 1
    saved = json.loads(__import__("pathlib").Path(refs[0]["metadata_path"]).read_text(encoding="utf-8"))
    raw = __import__("pathlib").Path(refs[0]["response_path"]).read_bytes()
    assert saved["binding"]["asin"] == "B000000001"
    assert saved["binding"]["context"]["item_index"] == 2
    assert json.loads(raw)["body"] == body
    assert hashlib.sha256(raw).hexdigest() == refs[0]["response_sha256"]
    assert "secret-test-key" not in raw.decode() + json.dumps(saved)


def test_tuple_journal_transport_does_not_discard_success(tmp_path):
    result = provider(tmp_path, lambda *_: (200, {"text": "中文"})).translate("Hola", asin="A", field="f")
    assert result.status == "success" and result.text == "中文"
    assert result.raw["raw_response_refs"]


def test_raw_archive_failure_is_fail_closed_without_retry(tmp_path):
    calls = []
    p = provider(tmp_path, lambda *_: calls.append(1) or {"body": {"text": "中文"}, "status_code": 200})
    p.max_retries = 3
    p.response_journal.save = lambda **_: (_ for _ in ()).throw(OSError("disk unavailable"))
    result = p.translate("Hola", asin="A", field="f")
    assert result.status == "pending" and result.error == "RAW_RESPONSE_PERSISTENCE_FAILED"
    assert len(calls) == 1
    again = p.translate("Otro", asin="B", field="f")
    assert again.error == "RAW_JOURNAL_HALTED" and len(calls) == 1


def test_raw_http_bytes_saved_before_json_parsing(tmp_path, monkeypatch):
    from amazon_es_bestseller.translation.providers import qwen_mt
    from amazon_es_bestseller.translation.raw_journal import RawResponseJournal
    raw = b"not JSON " + b"x" * 9000
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return raw
    monkeypatch.setattr(qwen_mt, "urlopen", lambda *_a, **_k: Response())
    p = QwenMTProvider(api_key="secret", rate=0, max_retries=0,
                       response_journal=RawResponseJournal(tmp_path / "raw"))
    result = p.translate("Hola", asin="A", field="f")
    assert result.error == "EMPTY_TRANSLATION"
    ref = result.raw["raw_response_refs"][0]
    assert __import__("pathlib").Path(ref["response_path"]).read_bytes() == raw


def test_details_without_attributes_are_not_omitted(tmp_path):
    calls = []
    p = provider(tmp_path, lambda *a: calls.append(a[2]) or {"text": "中文"})
    row = {"asin": "B000000001", "attributes": [], "detail_bullets_raw": ["País de origen: España", "ASIN: B000000001"]}
    before = deepcopy(row)
    service = TranslationService(p, TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records([row], fields=["product_details"])["records"][row["asin"]]
    items = result["fields"]["product_details_zh"]["items"]
    assert len(items) == 2
    assert items[1]["translated_text"] == row["asin"]
    assert all(x["disposition"] != "OMITTED" for x in result["source_coverage"]["entries"])
    assert result["source_coverage"]["covered_count"] == 2
    assert calls and row == before


def test_english_identity_before_cache_and_duplicate_keys_preserved(tmp_path):
    p = provider(tmp_path, lambda *_: pytest.fail("identity value was sent to Qwen"))
    row = {"asin": "B000000001", "attributes": [
        {"label_raw": "model", "value_raw": "Craft Master"},
        {"label_raw": "model", "value_raw": "Craft Master II"}]}
    service = TranslationService(p, TranslationCache(tmp_path / "cache.json"))
    result = service.translate_records([row], fields=["product_details"])["records"][row["asin"]]
    items = result["fields"]["product_details_zh"]["items"]
    assert [i["translated_text"] for i in items] == ["Craft Master", "Craft Master II"]


def test_cache_hit_keeps_original_raw_response_reference(tmp_path):
    calls = []
    p = provider(tmp_path, lambda *_: calls.append(1) or {"text": "中文"})
    s = TranslationService(p, TranslationCache(tmp_path / "cache.json"))
    out = s.translate_records([{"asin": "A", "title_es_raw": "Hola"},
                               {"asin": "B", "title_es_raw": "Hola"}], fields=["title_es_raw"])["records"]
    a, b = [r["fields"]["title_zh"] for r in out.values()]
    assert len(calls) == 1
    assert a["raw_response_refs"] == b["raw_response_refs"] and a["raw_response_refs"]


def test_unmapped_labels_have_explicit_initial_translation(tmp_path):
    calls = []
    p = provider(tmp_path, lambda *a: calls.append(a[2]["messages"][0]["content"]) or {"text": "中文"})
    row = {"asin": "B000000001", "attributes": [{"label_raw": "Acabado especial desconocido", "value_raw": "Brillante"}]}
    out = TranslationService(p, TranslationCache(tmp_path / "cache.json")).translate_records(
        [row], fields=["product_details"])["records"][row["asin"]]
    item = out["fields"]["product_details_zh"]["items"][0]
    assert "Acabado especial desconocido" in calls
    assert item["label_translation_status"] in {"success", "qa_failed"}


def test_cli_preclean_does_not_lose_details_when_attributes_empty(tmp_path):
    from amazon_es_bestseller.translation.preclean import audit_records
    from amazon_es_bestseller.translation.production import build_production_input, records_for_preclean
    row = {"asin": "B000000001", "attributes": [], "detail_bullets_raw": ["País de origen: España", "ASIN: B000000001"]}
    for source in ([row], records_for_preclean(build_production_input([row]))):
        prepared = audit_records(source)["translation_input_records"]
        assert prepared[0]["fields"]["product_details"]["translate_allowed"]
        p = provider(tmp_path, lambda *_: {"text": "西班牙"})
        result = TranslationService(p, TranslationCache(tmp_path / "preclean_cache.json")).translate_records(
            prepared, fields=["product_details"])["records"][row["asin"]]
        assert len(result["fields"]["product_details_zh"]["items"]) == 2


def test_preclean_review_gate_is_not_bypassed(tmp_path):
    from amazon_es_bestseller.translation.preclean import audit_records
    row = {"asin": "B000000001", "attributes": [{"label_raw": "Capacidad", "value_raw": "5 l / 9 l"}],
           "detail_bullets_raw": ["ASIN: B000000001"]}
    clean = audit_records([row])["translation_input_records"][0]
    assert not clean["fields"]["product_details"]["translate_allowed"]
    p = provider(tmp_path, lambda *_: pytest.fail("blocked field dispatched"))
    result = TranslationService(p, TranslationCache(tmp_path / "blocked_cache.json")).translate_records(
        [clean], fields=["product_details"])["records"][row["asin"]]
    assert result["fields"]["product_details_zh"]["translation_status"] == "preclean_blocked"


def test_provider_config_alias_cannot_silently_select_fake(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from amazon_es_bestseller.commands.translation import _service
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="QWEN_CREDENTIAL_UNAVAILABLE"):
        _service(tmp_path, {"provider": "qwen-mt"}, SimpleNamespace(model=None, rate=None))
    with pytest.raises(ValueError, match="PROVIDER_CONFIGURATION_CONFLICT"):
        _service(tmp_path, {"provider": "qwen-mt", "provider_mode": "fake"}, SimpleNamespace(model=None, rate=None))


def test_pool_aliases_share_fail_closed_response_journal(tmp_path):
    from amazon_es_bestseller.translation.pool import ProviderPool, PoolProviderAdapter
    aliases = {name: QwenMTProvider(api_key="fake", rate=0, transport=lambda *_: {"text": "中文"}) for name in ('a', 'b', 'c')}
    adapter = PoolProviderAdapter(ProviderPool(aliases, failover=False))
    TranslationService(adapter, TranslationCache(tmp_path / "pool_cache.json"))
    assert len({id(p.response_journal) for p in aliases.values()}) == 1


def test_parallel_qwen_pool_enforces_raw_contract_even_if_original_provider_is_mock(tmp_path):
    from types import SimpleNamespace
    from amazon_es_bestseller.translation.pool import ProviderPool
    calls = []
    p = provider(tmp_path, lambda *a: calls.append(a[2]["messages"][0]["content"]) or {"text": "中文"})
    s = TranslationService(SimpleNamespace(name="fake", model="fake"), TranslationCache(tmp_path / "cache.json"))
    row = {"asin": "B000000001", "attributes": [{"label_raw": "Acabado especial desconocido", "value_raw": "Brillante"}]}
    result = s.translate_records_parallel([row], ProviderPool({"a": p}, failover=False), fields=["product_details"])
    item = result["records"][row["asin"]]["fields"]["product_details_zh"]["items"][0]
    assert "Acabado especial desconocido" in calls
    assert item["label_translation_status"] in {"success", "qa_failed"}
    assert s.raw_response_required is False


def test_missing_raw_file_holds_cache_without_new_request(tmp_path):
    from pathlib import Path
    calls = []
    p = provider(tmp_path, lambda *_: calls.append(1) or {"text": "中文"})
    s = TranslationService(p, TranslationCache(tmp_path / "lost_cache.json"))
    row = {"asin": "B000000001", "title_es_raw": "Hola"}
    first = s.translate_records([row])["records"][row["asin"]]["fields"]["title_zh"]
    Path(first["raw_response_refs"][0]["response_path"]).rename(tmp_path / "moved_response.raw")
    second = s.translate_records([row])["records"][row["asin"]]["fields"]["title_zh"]
    assert second["translation_status"] == "pending"
    assert second["raw_response_status"] == "HISTORICAL_RAW_NOT_TRACEABLE"
    assert len(calls) == 1


def test_truncated_response_is_retained_partial_without_quality_retry(tmp_path):
    calls = []
    body = {"id": "truncated", "choices": [{"message": {"content": "部分译文"}, "finish_reason": "length"}],
            "usage": {"total_tokens": 45}}
    p = provider(tmp_path, lambda *_: calls.append(1) or body)
    result = p.translate("Texto largo", asin="B000000001", field="f")
    assert result.status == "partial" and result.text == "部分译文"
    assert result.raw["raw_response_refs"] and len(calls) == 1


@pytest.mark.parametrize("body", [
    {"choices": [{"message": "not an object"}]},
    {"output": "not an object", "text": "中文"},
])
def test_malformed_response_shape_still_has_a_durable_explicit_result(tmp_path, body):
    p = provider(tmp_path, lambda *_: {"status_code": 200, "body": body})
    result = p.translate("Hola", asin="B000000001", field="f")
    assert result.status in {"success", "failed"}
    assert result.raw["raw_response_refs"]
