"""Persistent, fail-closed spending reservations for real translation calls.

The production command is offline by default.  This module is only used when
an explicitly configured remote provider is selected.  It never inspects or
stores credentials; it stores the pricing evidence, reservations, and charged
amounts needed to make a small approved spend cap enforceable across resumes.
"""
from __future__ import annotations

import math
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any, Mapping

from ..runtime_state import atomic_write_json
from .providers.base import ProviderResponse, TranslationProvider


MAX_APPROVED_BUDGET_CNY = Decimal("5.00")


class BudgetBlocked(RuntimeError):
    """Raised before transport when a call cannot safely be funded."""


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class VerifiedPriceCard:
    """CNY token pricing with explicit external verification evidence."""

    provider: str
    model: str
    input_per_million_cny: Decimal
    output_per_million_cny: Decimal
    verified_at: str
    source: str
    currency: str = "CNY"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None, *, provider: str, model: str) -> "VerifiedPriceCard":
        if not isinstance(value, Mapping):
            raise BudgetBlocked("BUDGET_PRICE_UNVERIFIED")
        if str(value.get("currency") or "").upper() != "CNY":
            raise BudgetBlocked("BUDGET_CURRENCY_UNVERIFIED")
        if str(value.get("provider") or provider) != provider or str(value.get("model") or model) != model:
            raise BudgetBlocked("BUDGET_PRICE_MODEL_MISMATCH")
        if not str(value.get("verified_at") or "").strip() or not str(value.get("source") or "").strip():
            raise BudgetBlocked("BUDGET_PRICE_UNVERIFIED")
        try:
            input_rate = _money(value["input_per_million_cny"])
            output_rate = _money(value["output_per_million_cny"])
        except (KeyError, ArithmeticError, ValueError) as exc:
            raise BudgetBlocked("BUDGET_PRICE_UNVERIFIED") from exc
        if input_rate < 0 or output_rate < 0:
            raise BudgetBlocked("BUDGET_PRICE_UNVERIFIED")
        return cls(provider=provider, model=model, input_per_million_cny=input_rate,
                   output_per_million_cny=output_rate, verified_at=str(value["verified_at"]),
                   source=str(value["source"]), currency="CNY")


class BudgetLedger:
    """Atomic CNY budget ledger with in-flight reservation accounting."""

    def __init__(self, path: str | Path, *, limit_cny: Any,
                 price_card: VerifiedPriceCard, safety_buffer_cny: Any = "0.10",
                 max_input_tokens: int = 12000, max_output_tokens: int = 2048,
                 prompt_overhead_tokens: int = 1024) -> None:
        self.path = Path(path)
        self.limit_cny = _money(limit_cny)
        self.buffer_cny = _money(safety_buffer_cny)
        self.price_card = price_card
        self.max_input_tokens = int(max_input_tokens)
        self.max_output_tokens = int(max_output_tokens)
        self.prompt_overhead_tokens = int(prompt_overhead_tokens)
        if not (Decimal("0") < self.limit_cny <= MAX_APPROVED_BUDGET_CNY):
            raise ValueError("BUDGET_LIMIT_OUT_OF_RANGE")
        if not (Decimal("0") <= self.buffer_cny < self.limit_cny):
            raise ValueError("BUDGET_BUFFER_OUT_OF_RANGE")
        if min(self.max_input_tokens, self.max_output_tokens, self.prompt_overhead_tokens) < 1:
            raise ValueError("BUDGET_TOKEN_LIMIT_INVALID")
        self._lock = threading.RLock()
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "limit_cny": str(self.limit_cny),
                    "safety_buffer_cny": str(self.buffer_cny),
                    "price_card": self._price_card_dict(), "events": [],
                    "committed_cny": "0", "reservations": {}}
        import json
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("limit_cny") != str(self.limit_cny):
            raise BudgetBlocked("BUDGET_LEDGER_MISMATCH")
        if value.get("price_card") != self._price_card_dict():
            raise BudgetBlocked("BUDGET_LEDGER_PRICE_CHANGED")
        value.setdefault("reservations", {})
        value.setdefault("events", [])
        value.setdefault("committed_cny", "0")
        return value

    def _price_card_dict(self) -> dict[str, str]:
        return {"provider": self.price_card.provider, "model": self.price_card.model,
                "input_per_million_cny": str(self.price_card.input_per_million_cny),
                "output_per_million_cny": str(self.price_card.output_per_million_cny),
                "verified_at": self.price_card.verified_at, "source": self.price_card.source,
                "currency": self.price_card.currency}

    def _save(self) -> None:
        atomic_write_json(self.path, self._state, sort_keys=True)

    @staticmethod
    def estimate_input_tokens(text: str) -> int:
        # One token per UTF-8 byte is intentionally an overestimate for the
        # Spanish request text and leaves room for provider tokenisation drift.
        return max(1, len(str(text or "").encode("utf-8")))

    def estimate_cost(self, *, input_tokens: int, output_tokens: int) -> Decimal:
        return ((Decimal(input_tokens) * self.price_card.input_per_million_cny
                 + Decimal(output_tokens) * self.price_card.output_per_million_cny)
                / Decimal(1_000_000)).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)

    def _reserved_total(self) -> Decimal:
        return sum((_money(row["reserved_cny"]) for row in self._state["reservations"].values()), Decimal("0"))

    def reserve(self, *, asin: str, field: str, text: str) -> dict[str, Any]:
        with self._lock:
            input_tokens = self.estimate_input_tokens(text)
            if input_tokens > self.max_input_tokens:
                raise BudgetBlocked("BUDGET_FIELD_TOO_LONG")
            reserved_input = input_tokens + self.prompt_overhead_tokens
            reserved = self.estimate_cost(input_tokens=reserved_input, output_tokens=self.max_output_tokens)
            committed = _money(self._state["committed_cny"])
            available = self.limit_cny - self.buffer_cny - committed - self._reserved_total()
            if reserved > available:
                raise BudgetBlocked("BUDGET_CAP_REACHED")
            reservation_id = uuid.uuid4().hex
            reservation = {"reservation_id": reservation_id, "asin": str(asin), "field": str(field),
                           "created_at": _now(), "input_tokens_reserved": reserved_input,
                           "output_tokens_reserved": self.max_output_tokens,
                           "reserved_cny": str(reserved)}
            self._state["reservations"][reservation_id] = reservation
            self._state["events"].append({"kind": "reserve", **reservation})
            self._save()
            return dict(reservation)

    def settle(self, reservation: Mapping[str, Any], *, response_raw: Mapping[str, Any] | None,
               success: bool) -> dict[str, Any]:
        """Charge actual usage when supplied, otherwise retain full reservation.

        Failed/retried calls have unknown charge behaviour, so they are charged
        at the conservative reservation rather than treated as free.
        """
        reservation_id = str(reservation["reservation_id"])
        with self._lock:
            current = self._state["reservations"].pop(reservation_id, None)
            if current is None:
                raise BudgetBlocked("BUDGET_RESERVATION_MISSING")
            usage = (response_raw or {}).get("usage") if isinstance(response_raw, Mapping) else None
            charge = _money(current["reserved_cny"])
            if success and isinstance(usage, Mapping):
                try:
                    prompt = int(usage["prompt_tokens"])
                    completion = int(usage["completion_tokens"])
                    if prompt >= 0 and completion >= 0:
                        charge = self.estimate_cost(input_tokens=prompt, output_tokens=completion)
                except (KeyError, TypeError, ValueError):
                    pass
            self._state["committed_cny"] = str(_money(self._state["committed_cny"]) + charge)
            event = {"kind": "settle", "reservation_id": reservation_id, "asin": current["asin"],
                     "field": current["field"], "settled_at": _now(), "success": bool(success),
                     "charged_cny": str(charge), "usage": dict(usage) if isinstance(usage, Mapping) else None}
            self._state["events"].append(event)
            self._save()
            return event

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {**self._state, "available_cny": str(
                self.limit_cny - self.buffer_cny - _money(self._state["committed_cny"]) - self._reserved_total())}


class BudgetedProvider(TranslationProvider):
    """Provider guard that creates a durable reservation before every call."""

    def __init__(self, provider: TranslationProvider, ledger: BudgetLedger) -> None:
        self.provider = provider
        self.ledger = ledger
        self.name = provider.name

    @property
    def model(self) -> str:
        return self.provider.model

    def translate(self, text: str, *, asin: str, field: str,
                  source_language: str = "es", target_language: str = "zh-CN",
                  context: dict[str, Any] | None = None) -> ProviderResponse:
        try:
            reservation = self.ledger.reserve(asin=asin, field=field, text=text)
        except BudgetBlocked as exc:
            return ProviderResponse(provider=self.name, model=self.model, status="failed",
                                    error=str(exc), attempts=0,
                                    raw={"budget_blocked": str(exc), "budget": self.ledger.snapshot()})
        response: ProviderResponse | None = None
        try:
            response = self.provider.translate(text, asin=asin, field=field,
                                               source_language=source_language,
                                               target_language=target_language,
                                               context={**(context or {}),
                                                        "max_output_tokens": self.ledger.max_output_tokens})
            return response
        finally:
            event = self.ledger.settle(
                reservation, response_raw=(response.raw if response else None),
                success=bool(response and response.status == "success"),
            )
            if response is not None:
                response.raw = {**(response.raw or {}), "budget": event}
