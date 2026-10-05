from __future__ import annotations

import multiprocessing
import pytest

from amazon_es_bestseller.translation.budget import (
    BudgetBlocked,
    BudgetLedger,
    BudgetedProvider,
    VerifiedPriceCard,
)
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider


def _card():
    return VerifiedPriceCard.from_mapping({
        "provider": "test", "model": "test-model", "currency": "CNY",
        "input_per_million_cny": "500000", "output_per_million_cny": "1000000",
        "verified_at": "2026-10-05T00:00:00Z", "source": "https://help.aliyun.com/pricing/qwen-mt",
    }, provider="test", model="test-model")


class _Provider(TranslationProvider):
    name = "test"

    @property
    def model(self):
        return "test-model"

    def __init__(self):
        self.calls = 0
        self.context = None

    def translate(self, text, *, asin, field, source_language="es", target_language="zh-CN", context=None):
        self.calls += 1
        self.context = context
        return ProviderResponse(text="ok", provider=self.name, model=self.model,
                                raw={"usage": {"prompt_tokens": 1, "completion_tokens": 1}})


def test_unverified_price_or_wrong_currency_blocks_before_transport():
    with pytest.raises(BudgetBlocked, match="BUDGET_PRICE_UNVERIFIED"):
        VerifiedPriceCard.from_mapping(None, provider="test", model="test-model")
    with pytest.raises(BudgetBlocked, match="BUDGET_CURRENCY_UNVERIFIED"):
        VerifiedPriceCard.from_mapping({"currency": "USD"}, provider="test", model="test-model")


def test_reservations_are_durable_and_cap_inflight_spend(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger.json", limit_cny="5", price_card=_card(),
                          safety_buffer_cny="0.1", max_output_tokens=1,
                          prompt_overhead_tokens=1)
    first = ledger.reserve(asin="B1", field="title", text="a")  # 2 CNY
    second = ledger.reserve(asin="B2", field="title", text="a")  # 2 CNY
    with pytest.raises(BudgetBlocked, match="BUDGET_CAP_REACHED"):
        ledger.reserve(asin="B3", field="title", text="a")
    ledger.settle(first, response_raw={"usage": {"prompt_tokens": 1, "completion_tokens": 1}}, success=True)
    resumed = BudgetLedger(tmp_path / "ledger.json", limit_cny="5", price_card=_card(),
                           safety_buffer_cny="0.1", max_output_tokens=1,
                           prompt_overhead_tokens=1)
    assert resumed.snapshot()["committed_cny"] == "1.500000"
    assert len(resumed.snapshot()["reservations"]) == 1
    resumed.settle(second, response_raw=None, success=False)
    with pytest.raises(BudgetBlocked, match="BUDGET_CAP_REACHED"):
        resumed.reserve(asin="B3", field="title", text="a")


def test_budgeted_provider_stops_long_field_and_passes_output_cap(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger.json", limit_cny="5", price_card=_card(),
                          max_input_tokens=3, max_output_tokens=2, prompt_overhead_tokens=1)
    raw = _Provider()
    provider = BudgetedProvider(raw, ledger)
    blocked = provider.translate("four", asin="B1", field="title")
    assert blocked.error == "BUDGET_FIELD_TOO_LONG" and raw.calls == 0
    # Fresh ledger avoids the intentionally short-field guard above.
    ledger = BudgetLedger(tmp_path / "second.json", limit_cny="5", price_card=_card(),
                          max_input_tokens=10, max_output_tokens=2, prompt_overhead_tokens=1)
    raw = _Provider()
    response = BudgetedProvider(raw, ledger).translate("ok", asin="B1", field="title")
    assert response.status == "success" and raw.calls == 1
    assert raw.context["max_output_tokens"] == 2


def _reserve_in_process(path, queue):
    ledger = BudgetLedger(path, limit_cny="5", price_card=_card(), safety_buffer_cny="0.1",
                          max_output_tokens=1, prompt_overhead_tokens=1)
    try:
        queue.put(("ok", ledger.reserve(asin="B1", field="title", text="a")["reservation_id"]))
    except BudgetBlocked as exc:
        queue.put(("blocked", str(exc)))


def test_cross_process_lock_idempotent_settlement_and_unknown_charge(tmp_path):
    path = str(tmp_path / "ledger.json")
    queue = multiprocessing.Queue()
    processes = [multiprocessing.Process(target=_reserve_in_process, args=(path, queue)) for _ in range(3)]
    for process in processes: process.start()
    for process in processes: process.join(10); assert process.exitcode == 0
    outcomes = [queue.get(timeout=2) for _ in processes]
    assert sum(1 for kind, _ in outcomes if kind == "ok") == 2
    ledger = BudgetLedger(path, limit_cny="5", price_card=_card(), safety_buffer_cny="0.1",
                          max_output_tokens=1, prompt_overhead_tokens=1)
    reservation = next(iter(ledger.snapshot()["reservations"].values()))
    first = ledger.settle(reservation, response_raw=None, success=False)
    assert ledger.settle(reservation, response_raw=None, success=False) == first
    assert ledger.snapshot()["reconciliation_required"] is True


def test_persisted_unique_asin_scope_rejects_expansion(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger.json", limit_cny="5", price_card=_card(),
                          max_unique_asins=1, max_output_tokens=1, prompt_overhead_tokens=1)
    ledger.reserve(asin="B1", field="title", text="a")
    with pytest.raises(BudgetBlocked, match="BUDGET_UNIQUE_ASIN_CAP"):
        ledger.reserve(asin="B2", field="title", text="a")
