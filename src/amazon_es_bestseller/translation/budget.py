"""Durable, cross-process, fail-closed translation spending ledger."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from ..runtime_state import atomic_write_json
from .providers.base import ProviderResponse, TranslationProvider

MAX_APPROVED_BUDGET_CNY = Decimal("5.00")
MAX_PRICE_AGE_DAYS = 90

class BudgetBlocked(RuntimeError): pass

def _money(v: Any) -> Decimal: return Decimal(str(v)).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)
def _now() -> str: return datetime.now(timezone.utc).isoformat()
def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class VerifiedPriceCard:
    provider: str; model: str; input_per_million_cny: Decimal; output_per_million_cny: Decimal
    verified_at: str; source: str; currency: str = "CNY"; configuration_hash: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None, *, provider: str, model: str) -> "VerifiedPriceCard":
        if not isinstance(value, Mapping): raise BudgetBlocked("BUDGET_PRICE_UNVERIFIED")
        if str(value.get("currency") or "").upper() != "CNY": raise BudgetBlocked("BUDGET_CURRENCY_UNVERIFIED")
        if str(value.get("provider") or provider) != provider or str(value.get("model") or model) != model: raise BudgetBlocked("BUDGET_PRICE_MODEL_MISMATCH")
        source = str(value.get("source") or "").strip()
        parsed = urlparse(source)
        if parsed.scheme != "https" or not parsed.netloc: raise BudgetBlocked("BUDGET_PRICE_SOURCE_UNVERIFIED")
        # The only real adapter currently admitted by this ledger is Qwen.
        # Do not let an arbitrary HTTPS blog be labelled a verified price.
        host = (parsed.hostname or "").casefold()
        if provider == "qwen-mt" and host not in {"aliyun.com"} and not host.endswith(".aliyun.com"):
            raise BudgetBlocked("BUDGET_PRICE_SOURCE_UNVERIFIED")
        try:
            verified = datetime.fromisoformat(str(value.get("verified_at") or "").replace("Z", "+00:00"))
            input_rate, output_rate = _money(value["input_per_million_cny"]), _money(value["output_per_million_cny"])
        except (KeyError, ValueError, ArithmeticError) as exc: raise BudgetBlocked("BUDGET_PRICE_UNVERIFIED") from exc
        now = datetime.now(timezone.utc)
        if (verified.tzinfo is None or verified > now.replace(microsecond=0) + timedelta(minutes=5)
                or now - verified > timedelta(days=MAX_PRICE_AGE_DAYS)
                or input_rate <= 0 or output_rate <= 0):
            raise BudgetBlocked("BUDGET_PRICE_UNVERIFIED")
        evidence = {"provider": provider, "model": model, "currency": "CNY", "input_per_million_cny": str(input_rate), "output_per_million_cny": str(output_rate), "verified_at": verified.isoformat(), "source": source}
        return cls(provider, model, input_rate, output_rate, verified.isoformat(), source, "CNY", _canonical_hash(evidence))


class _FileLock:
    """Exclusive lockfile; stale locks are recovered only after owner death."""
    def __init__(self, path: Path, timeout: float = 20.0): self.path, self.timeout = path, timeout
    @staticmethod
    def _alive(pid: int) -> bool:
        try: os.kill(pid, 0); return True
        except OSError: return False
    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"pid": os.getpid(), "created_at": _now()}).encode()); os.close(fd); return self
            except FileExistsError:
                try: owner = json.loads(self.path.read_text(encoding="utf-8")); stale = not self._alive(int(owner.get("pid", 0)))
                except (OSError, ValueError, TypeError): stale = False
                if stale:
                    try: self.path.unlink()
                    except FileNotFoundError: pass
                    continue
                if time.monotonic() >= deadline: raise BudgetBlocked("BUDGET_LEDGER_LOCK_TIMEOUT")
                time.sleep(.025)
    def __exit__(self, *_):
        try: self.path.unlink()
        except FileNotFoundError: pass


class BudgetLedger:
    def __init__(self, path: str | Path, *, limit_cny: Any, price_card: VerifiedPriceCard,
                 safety_buffer_cny: Any = "0.10", max_input_tokens: int = 12000,
                 max_output_tokens: int = 2048, prompt_overhead_tokens: int = 1024,
                 run_scope: str = "qwen-mt-production-v1", max_unique_asins: int = 1500):
        self.path, self.lock_path = Path(path), Path(str(path) + ".lock")
        self.limit_cny, self.buffer_cny, self.price_card = _money(limit_cny), _money(safety_buffer_cny), price_card
        self.max_input_tokens, self.max_output_tokens, self.prompt_overhead_tokens = int(max_input_tokens), int(max_output_tokens), int(prompt_overhead_tokens)
        self.run_scope, self.max_unique_asins = str(run_scope), int(max_unique_asins)
        if not (Decimal("0") < self.limit_cny <= MAX_APPROVED_BUDGET_CNY): raise ValueError("BUDGET_LIMIT_OUT_OF_RANGE")
        if not (1 <= self.max_unique_asins <= 1500): raise ValueError("BUDGET_UNIQUE_ASIN_SCOPE_INVALID")
        if not (Decimal("0") <= self.buffer_cny < self.limit_cny) or min(self.max_input_tokens, self.max_output_tokens, self.prompt_overhead_tokens, self.max_unique_asins) < 1: raise ValueError("BUDGET_LIMIT_INVALID")
        self._lock = threading.RLock()
        # Validate persisted evidence at open; every mutation still reloads it under file lock.
        with self._transaction() as state: self._state = state

    def _card(self) -> dict[str, str]:
        return {"provider": self.price_card.provider, "model": self.price_card.model, "input_per_million_cny": str(self.price_card.input_per_million_cny), "output_per_million_cny": str(self.price_card.output_per_million_cny), "verified_at": self.price_card.verified_at, "source": self.price_card.source, "currency": "CNY", "configuration_hash": self.price_card.configuration_hash}
    def _new(self): return {"version": 3, "limit_cny": str(self.limit_cny), "safety_buffer_cny": str(self.buffer_cny), "price_card": self._card(), "run_scope": self.run_scope, "max_unique_asins": self.max_unique_asins, "selected_asins": [], "committed_cny": "0", "reservations": {}, "settlements": {}, "request_claims": {}, "events": [], "reconciliation_required": False}
    def _read(self):
        if not self.path.exists(): return self._new()
        try: state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc: raise BudgetBlocked("BUDGET_LEDGER_UNREADABLE") from exc
        if not isinstance(state, dict) or state.get("limit_cny") != str(self.limit_cny) or state.get("price_card") != self._card() or state.get("run_scope") != self.run_scope or int(state.get("max_unique_asins", 0)) != self.max_unique_asins: raise BudgetBlocked("BUDGET_LEDGER_MISMATCH")
        for key, default in (("reservations", {}), ("settlements", {}), ("request_claims", {}), ("events", []), ("selected_asins", [])): state.setdefault(key, default)
        state.setdefault("committed_cny", "0"); state.setdefault("reconciliation_required", False); return state
    @contextmanager
    def _transaction(self):
        with self._lock, _FileLock(self.lock_path):
            state = self._read(); yield state; atomic_write_json(self.path, state, sort_keys=True); self._state = state
    @staticmethod
    def estimate_input_tokens(text: str) -> int: return max(1, len(str(text or "").encode("utf-8")))
    def estimate_cost(self, *, input_tokens: int, output_tokens: int) -> Decimal: return ((Decimal(input_tokens) * self.price_card.input_per_million_cny + Decimal(output_tokens) * self.price_card.output_per_million_cny) / Decimal(1_000_000)).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)
    @staticmethod
    def _reserved(state): return sum((_money(r["reserved_cny"]) for r in state["reservations"].values()), Decimal("0"))
    @staticmethod
    def _request_fingerprint(*, asin: str, field: str, text: str) -> str:
        return _canonical_hash({"asin": asin, "field": field, "text": text})
    def reserve(self, *, asin: str, field: str, text: str, request_id: str | None = None) -> dict[str, Any]:
        tokens = self.estimate_input_tokens(text)
        if tokens > self.max_input_tokens: raise BudgetBlocked("BUDGET_FIELD_TOO_LONG")
        asin = str(asin).strip().upper(); field = str(field); text = str(text or ""); request_id = str(request_id or uuid.uuid4().hex)
        fingerprint = self._request_fingerprint(asin=asin, field=field, text=text)
        with self._transaction() as state:
            claim = state["request_claims"].get(request_id)
            if claim:
                if claim.get("fingerprint") != fingerprint: raise BudgetBlocked("BUDGET_REQUEST_FINGERPRINT_MISMATCH")
                raise BudgetBlocked("BUDGET_REQUEST_IN_FLIGHT" if claim.get("status") == "PENDING" else "BUDGET_REQUEST_ALREADY_SETTLED")
            selected = set(state["selected_asins"])
            if asin not in selected and len(selected) >= self.max_unique_asins: raise BudgetBlocked("BUDGET_UNIQUE_ASIN_CAP")
            reserved = self.estimate_cost(input_tokens=tokens + self.prompt_overhead_tokens, output_tokens=self.max_output_tokens)
            if _money(state["committed_cny"]) + self._reserved(state) + reserved > self.limit_cny - self.buffer_cny: raise BudgetBlocked("BUDGET_CAP_REACHED")
            selected.add(asin); state["selected_asins"] = sorted(selected)
            row = {"reservation_id": uuid.uuid4().hex, "request_id": request_id, "request_fingerprint": fingerprint, "asin": asin, "field": field, "created_at": _now(), "input_tokens_reserved": tokens + self.prompt_overhead_tokens, "output_tokens_reserved": self.max_output_tokens, "reserved_cny": str(reserved)}
            state["request_claims"][request_id] = {"fingerprint": fingerprint, "reservation_id": row["reservation_id"], "status": "PENDING"}
            state["reservations"][row["reservation_id"]] = row; state["events"].append({"kind": "reserve", **row}); return dict(row)
    def settle(self, reservation: Mapping[str, Any], *, response_raw: Mapping[str, Any] | None, success: bool) -> dict[str, Any]:
        rid = str(reservation["reservation_id"])
        with self._transaction() as state:
            if rid in state["settlements"]: return dict(state["settlements"][rid])
            current = state["reservations"].pop(rid, None)
            if current is None: raise BudgetBlocked("BUDGET_RESERVATION_MISSING")
            usage = (response_raw or {}).get("usage") if isinstance(response_raw, Mapping) else None
            charge, known = _money(current["reserved_cny"]), False
            if success and isinstance(usage, Mapping):
                try:
                    prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
                    if isinstance(prompt, bool) or isinstance(completion, bool) or not isinstance(prompt, int) or not isinstance(completion, int) or prompt < 0 or completion < 0: raise ValueError("invalid token usage")
                    actual = self.estimate_cost(input_tokens=prompt, output_tokens=completion)
                    # A verified provider usage report may release unused
                    # pre-reserved output capacity.  Unknown/failed calls
                    # deliberately retain the full reservation instead.
                    charge, known = actual, True
                except (KeyError, TypeError, ValueError): pass
            if _money(state["committed_cny"]) + charge + self._reserved(state) > self.limit_cny: raise BudgetBlocked("BUDGET_OVERRUN_OBSERVED")
            state["committed_cny"] = str(_money(state["committed_cny"]) + charge)
            event = {"kind": "settle", "reservation_id": rid, "asin": current["asin"], "field": current["field"], "settled_at": _now(), "success": bool(success), "charged_cny": str(charge), "usage": dict(usage) if isinstance(usage, Mapping) else None, "charge_status": "USAGE_VERIFIED" if known else "UNKNOWN_CONSERVATIVE", "reconciliation_required": not known}
            if not known: state["reconciliation_required"] = True
            claim = state["request_claims"].get(str(current.get("request_id")))
            if claim: claim["status"] = "SETTLED"
            state["settlements"][rid] = event; state["events"].append(event); return dict(event)
    def snapshot(self) -> dict[str, Any]:
        with self._transaction() as state:
            return {**state, "available_cny": str(self.limit_cny - self.buffer_cny - _money(state["committed_cny"]) - self._reserved(state))}


class BudgetedProvider(TranslationProvider):
    """The only Qwen call path: reservation is persisted before transport."""
    def __init__(self, provider: TranslationProvider, ledger: BudgetLedger): self.provider, self.ledger, self.name = provider, ledger, provider.name
    @property
    def model(self) -> str: return self.provider.model
    def translate(self, text: str, *, asin: str, field: str, source_language: str = "es", target_language: str = "zh-CN", context: dict[str, Any] | None = None) -> ProviderResponse:
        try: reservation = self.ledger.reserve(asin=asin, field=field, text=text, request_id=(context or {}).get("budget_request_id"))
        except BudgetBlocked as exc: return ProviderResponse(provider=self.name, model=self.model, status="failed", error=str(exc), attempts=0, raw={"budget_blocked": str(exc)})
        response = None
        try:
            response = self.provider.translate(text, asin=asin, field=field, source_language=source_language, target_language=target_language, context={**(context or {}), "max_output_tokens": self.ledger.max_output_tokens})
            return response
        finally:
            event = self.ledger.settle(reservation, response_raw=(response.raw if response else None), success=bool(response and response.status == "success"))
            if response is not None: response.raw = {**(response.raw or {}), "budget": event}
