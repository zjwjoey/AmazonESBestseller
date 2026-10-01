"""Safe dual-provider execution for Translation V2.

The pool owns scheduling and provider health; individual providers remain
single-endpoint, single-request adapters.  This module is transport-agnostic
and is intended to be tested with Fake Providers before any real API use.
"""
from __future__ import annotations

import hashlib
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .providers.base import ProviderResponse, TranslationProvider
from .providers.qwen_mt import QwenMTProvider


HEALTHY = "HEALTHY"
RATE_LIMITED = "RATE_LIMITED"
DEGRADED = "DEGRADED"
DISABLED = "DISABLED"


@dataclass(frozen=True)
class TranslationTask:
    key: str
    text: str
    asin: str
    field: str
    context: dict[str, Any] = field(default_factory=dict)
    tm_key: str = ""

    @classmethod
    def from_values(cls, text: str, *, asin: str, field: str,
                    source_language: str = "es", target_language: str = "zh-CN",
                    schema_version: str = "", prompt_version: str = "") -> "TranslationTask":
        digest = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
        key = "|".join((digest, field, schema_version, prompt_version, target_language))
        return cls(key=key, text=str(text or ""), asin=asin, field=field,
                   context={"source_language": source_language, "target_language": target_language},
                   tm_key=key)


@dataclass
class ProviderStats:
    alias: str
    state: str = HEALTHY
    requests: int = 0
    success: int = 0
    retries: int = 0
    rate_limited: int = 0
    http_5xx: int = 0
    network_errors: int = 0
    failed: int = 0


@dataclass
class PoolResult:
    task_key: str
    response: ProviderResponse
    provider_alias: str = ""
    source: str = "provider"
    qa_failed: bool = False


class ProviderPool:
    """Round-robin provider pool with in-flight dedupe and bounded failover."""

    def __init__(self, providers: Mapping[str, TranslationProvider], *, max_workers: Optional[int] = None,
                 failover: bool = True, tm: Optional[dict[str, PoolResult]] = None):
        if not providers:
            raise ValueError("ProviderPool requires at least one provider")
        self.providers = dict(providers)
        self.aliases = list(self.providers)
        self.max_workers = max_workers or len(self.aliases)
        if self.max_workers < 1 or self.max_workers > len(self.aliases):
            raise ValueError("max_workers must be between 1 and provider count")
        self.failover = failover
        self._lock = threading.RLock()
        self._provider_locks = {alias: threading.Lock() for alias in self.aliases}
        self._inflight: dict[str, Future[PoolResult]] = {}
        self._completed: dict[str, PoolResult] = dict(tm or {})
        self._cursor = 0
        self._stats = {alias: ProviderStats(alias=alias) for alias in self.aliases}

    def _choose_alias(self, excluded: set[str] | None = None) -> str:
        excluded = excluded or set()
        with self._lock:
            for _ in range(len(self.aliases)):
                alias = self.aliases[self._cursor % len(self.aliases)]
                self._cursor += 1
                if alias not in excluded and self._stats[alias].state == HEALTHY:
                    return alias
            for _ in range(len(self.aliases)):
                alias = self.aliases[self._cursor % len(self.aliases)]
                self._cursor += 1
                if alias not in excluded and self._stats[alias].state != DISABLED:
                    return alias
        raise RuntimeError("no healthy provider available")

    @staticmethod
    def _retry_class(response: ProviderResponse) -> str:
        error = str(response.error or "").casefold()
        if "429" in error or "rate" in error:
            return RATE_LIMITED
        if any(token in error for token in ("500", "502", "503", "504", "5xx")):
            return DEGRADED
        if response.status == "failed":
            return DEGRADED if any(token in error for token in ("599", "timeout", "network", "connection")) else DISABLED
        return HEALTHY

    def _call_one(self, task: TranslationTask, alias: str) -> PoolResult:
        provider = self.providers[alias]
        stats = self._stats[alias]
        with self._lock:
            stats.requests += 1
        # A provider lock gives each endpoint one active request at a time,
        # even when the shared executor has work queued for that alias.
        with self._provider_locks[alias]:
            response = provider.translate(task.text, asin=task.asin, field=task.field,
                                          source_language=task.context.get("source_language", "es"),
                                          target_language=task.context.get("target_language", "zh-CN"),
                                          context={**task.context, "provider_alias": alias})
        response.raw = {**(response.raw or {}), "provider_alias": alias}
        if response.status == "success":
            with self._lock:
                stats.success += 1
                stats.state = HEALTHY
            return PoolResult(task.key, response, alias)
        classification = self._retry_class(response)
        with self._lock:
            if classification == RATE_LIMITED:
                stats.rate_limited += 1
                stats.state = RATE_LIMITED
            elif classification == DEGRADED:
                stats.http_5xx += 1 if "5" in str(response.error or "") else 0
                stats.network_errors += 1 if classification == DEGRADED and "5" not in str(response.error or "") else 0
                stats.state = DEGRADED
            else:
                stats.failed += 1
                stats.state = DISABLED
        return PoolResult(task.key, response, alias)

    def _execute_task(self, task: TranslationTask, first_alias: str) -> PoolResult:
        result = self._call_one(task, first_alias)
        if result.response.status == "success" or not self.failover or len(self.aliases) == 1:
            return result
        # Failover is deliberately bounded to transport/rate-limit classes.
        # A permanent request/configuration failure must not be replayed on a
        # second endpoint, and QA failures are represented as successful
        # responses and therefore already returned above.
        if self._retry_class(result.response) not in {RATE_LIMITED, DEGRADED}:
            return result
        excluded = {first_alias}
        try:
            second = self._choose_alias(excluded)
        except RuntimeError:
            return result
        with self._lock:
            self._stats[first_alias].retries += 1
        return self._call_one(task, second)

    def submit(self, tasks: Sequence[TranslationTask]) -> list[PoolResult]:
        """Execute tasks concurrently, preserving input order and deduping keys."""
        results: list[Optional[Future[PoolResult] | PoolResult]] = []
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="qwen-pool") as executor:
            for task in tasks:
                with self._lock:
                    if task.key in self._completed:
                        results.append(self._completed[task.key])
                        continue
                    future = self._inflight.get(task.key)
                    if future is None:
                        try:
                            alias = self._choose_alias()
                        except RuntimeError:
                            # Preserve the task as pending rather than
                            # crashing a batch when every provider is down.
                            results.append(PoolResult(
                                task.key,
                                ProviderResponse(status="failed", error="NO_HEALTHY_PROVIDER", attempts=0),
                                source="pending"))
                            continue
                        future = executor.submit(self._execute_task, task, alias)
                        self._inflight[task.key] = future
                    results.append(future)
            output: list[PoolResult] = []
            for item in results:
                result = item.result() if isinstance(item, Future) else item
                assert result is not None
                output.append(result)
                with self._lock:
                    # Failed/pending results must remain retryable after a
                    # provider recovers; only successful results are shared
                    # through the pool's completed/TM registry.
                    if result.response.status == "success":
                        self._completed[result.task_key] = result
                    self._inflight.pop(result.task_key, None)
            return output

    def stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {alias: vars(stats).copy() for alias, stats in self._stats.items()}

    def snapshot(self) -> dict[str, Any]:
        healthy = sum(1 for stats in self._stats.values() if stats.state == HEALTHY)
        return {"provider_count": len(self.providers), "max_workers": self.max_workers,
                "in_flight": len(self._inflight), "completed": len(self._completed),
                "degraded_to_single_provider": len(self.providers) > 1 and healthy == 1,
                "providers": self.stats()}


class PoolProviderAdapter(TranslationProvider):
    """TranslationProvider facade used by TranslationService workers."""

    name = "qwen-mt"

    def __init__(self, pool: ProviderPool, *, model: str = "qwen-mt-flash"):
        self.pool = pool
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def translate(self, text: str, *, asin: str, field: str,
                  source_language: str = "es", target_language: str = "zh-CN",
                  context: Optional[dict[str, Any]] = None) -> ProviderResponse:
        task = TranslationTask.from_values(
            text, asin=asin, field=field, source_language=source_language,
            target_language=target_language,
            schema_version=str((context or {}).get("schema_version", "")),
            prompt_version=str((context or {}).get("prompt_version", "")))
        task = TranslationTask(task.key, task.text, task.asin, task.field,
                               {**task.context, **(context or {})}, task.tm_key)
        return self.pool.submit([task])[0].response


def build_qwen_provider_pool(config: Mapping[str, Any], *, transport_factory: Any = None) -> ProviderPool:
    """Build a pool from aliases without ever storing credentials in config."""
    specs = config.get("providers") if isinstance(config, Mapping) else None
    if not specs:
        specs = [{"name": "qwen-a", "type": "qwen-mt", "model": config.get("model", "qwen-mt-flash"),
                  "endpoint_env": "QWEN_API_ENDPOINT", "api_key_env": "QWEN_API_KEY",
                  "rate": config.get("rate", 0.5)}]
    providers: dict[str, TranslationProvider] = {}
    for spec in specs:
        alias = str(spec.get("name") or spec.get("alias") or "qwen-a")
        endpoint = os.getenv(str(spec.get("endpoint_env", "QWEN_API_ENDPOINT")))
        api_key = os.getenv(str(spec.get("api_key_env", "QWEN_API_KEY")))
        kwargs = dict(model=spec.get("model", "qwen-mt-flash"), endpoint=endpoint, api_key=api_key,
                      rate=float(spec.get("rate", 0.5)), timeout=float(spec.get("timeout", 60)),
                      max_retries=int(spec.get("max_retries", 2)),
                      backoff_seconds=float(spec.get("backoff_seconds", 5.0)))
        if transport_factory is not None:
            kwargs["transport"] = transport_factory(alias)
        providers[alias] = QwenMTProvider(**kwargs)
    return ProviderPool(providers, max_workers=min(len(providers), int(config.get("max_workers", len(providers)))))
