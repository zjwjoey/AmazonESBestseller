"""Safe multi-provider execution for Translation V2.

The pool owns scheduling and provider health; individual providers remain
single-endpoint, single-request adapters.  This module is transport-agnostic
and is intended to be tested with Fake Providers before any real API use.
"""
from __future__ import annotations

import hashlib
import os
import threading
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional, Sequence

from .providers.base import ProviderResponse, TranslationProvider
from .providers.qwen_mt import (
    QwenMTProvider, normalize_qwen_base_url, qwen_endpoint_configuration, validate_qwen_endpoint,
)
from .field_contract import canonical_translation_unit_field


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
                    schema_version: str = "", prompt_version: str = "",
                    translation_unit_field: Optional[str] = None) -> "TranslationTask":
        digest = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
        key = "|".join((digest, source_language, target_language,
                         canonical_translation_unit_field(translation_unit_field or field),
                         schema_version, prompt_version))
        return cls(key=key, text=str(text or ""), asin=asin, field=field,
                   context={"source_language": source_language, "target_language": target_language},
                   tm_key=key)


@dataclass
class ProviderStats:
    alias: str
    state: str = HEALTHY
    requests: int = 0
    http_attempts: int = 0
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
        self._transient_failures: dict[str, set[str]] = {}
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
            try:
                response = provider.translate(task.text, asin=task.asin, field=task.field,
                                              source_language=task.context.get("source_language", "es"),
                                              target_language=task.context.get("target_language", "zh-CN"),
                                              context={**task.context, "provider_alias": alias})
            except Exception:
                response = ProviderResponse(provider=getattr(provider, "name", ""),
                                            model=getattr(provider, "model", ""),
                                            status="pending", error="TRANSPORT_OUTCOME_UNKNOWN",
                                            attempts=1, raw={"outcome_unknown": True})
        if response.status == "failed" and any(token in str(response.error or "").casefold()
                for token in ("599", "timeout", "network", "connection")):
            response = replace(response, status="pending", error="TRANSPORT_OUTCOME_UNKNOWN",
                               raw={**(response.raw or {}), "outcome_unknown": True})
        with self._lock:
            # Provider-level retry attempts are part of the provider's own
            # response envelope; expose them in pool stats as well as pool
            # failover retries.
            stats.retries += max(0, int(response.attempts or 1) - 1)
            stats.http_attempts += max(0, int(response.attempts))
        response.raw = {**(response.raw or {}), "provider_alias": alias}
        if response.status == "success":
            with self._lock:
                stats.success += 1
                stats.state = HEALTHY
            return PoolResult(task.key, response, alias)
        if response.status == "pending":
            with self._lock:
                stats.network_errors += 1
                stats.state = DEGRADED
            return PoolResult(task.key, response, alias, source="pending")
        classification = self._retry_class(response)
        with self._lock:
            stats.failed += 1
            if classification == RATE_LIMITED:
                stats.rate_limited += 1
                stats.state = RATE_LIMITED
            elif classification == DEGRADED:
                error = str(response.error or "").casefold()
                if any(token in error for token in ("599", "timeout", "network", "connection")):
                    stats.network_errors += 1
                else:
                    stats.http_5xx += 1
                stats.state = DEGRADED
            if classification in {RATE_LIMITED, DEGRADED}:
                self._transient_failures.setdefault(task.key, set()).add(alias)
            else:
                stats.state = DISABLED
        return PoolResult(task.key, response, alias)

    def _execute_task(self, task: TranslationTask, first_alias: str) -> PoolResult:
        result = self._call_one(task, first_alias)
        if result.response.status in {"success", "pending"} or not self.failover or len(self.aliases) == 1:
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
        """Execute tasks with bounded admission, preserving order and deduping keys.

        Only ``max_workers`` new tasks are admitted at a time.  This matters
        for provider health: once all endpoints fail, the remaining batch is
        represented as pending rather than already having been sent to dead
        providers by an unbounded executor queue.
        """
        # A retry of the exact failed task is an explicit probe/resume.  Do
        # not reset transient states for unrelated new tasks: when both
        # endpoints are unavailable those tasks must remain pending.
        task_keys = {task.key for task in tasks}
        with self._lock:
            if task_keys and all(key in self._transient_failures for key in task_keys):
                for stats in self._stats.values():
                    if stats.state in {DEGRADED, RATE_LIMITED}:
                        stats.state = HEALTHY
        entries: list[tuple[str, Future[PoolResult] | PoolResult | None]] = []
        pending: deque[TranslationTask] = deque()
        queued_keys: set[str] = set()
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="qwen-pool") as executor:
            # First resolve cache hits and futures owned by another submitter.
            # New work is kept in a queue so admission can stop at the health
            # boundary instead of filling ThreadPoolExecutor's unbounded
            # internal queue.
            for task in tasks:
                with self._lock:
                    cached = self._completed.get(task.key)
                    existing = self._inflight.get(task.key)
                if cached is not None:
                    entries.append((task.key, cached))
                elif existing is not None:
                    entries.append((task.key, existing))
                elif task.key in queued_keys:
                    entries.append((task.key, None))
                else:
                    queued_keys.add(task.key)
                    pending.append(task)
                    entries.append((task.key, None))

            active: dict[Future[PoolResult], tuple[str, str]] = {}
            active_aliases: set[str] = set()
            resolved: dict[str, PoolResult] = {}
            external: dict[str, Future[PoolResult]] = {}

            def admit() -> bool:
                admitted = False
                while pending and len(active) < self.max_workers:
                    task = pending[0]
                    with self._lock:
                        cached = self._completed.get(task.key)
                        existing = self._inflight.get(task.key)
                        if cached is not None:
                            resolved[task.key] = cached
                            pending.popleft()
                            continue
                        if existing is not None:
                            # Another submitter won the race.  Keep this task
                            # as an external future and do not duplicate it.
                            external[task.key] = existing
                            pending.popleft()
                            continue
                        try:
                            # Admission must stop at the health boundary. A
                            # provider lock serializes execution too late: a
                            # second admitted task could otherwise become a
                            # third request after the first probe fails.
                            alias = self._choose_alias(active_aliases)
                        except RuntimeError:
                            break
                        future = executor.submit(self._execute_task, task, alias)
                        self._inflight[task.key] = future
                    pending.popleft()
                    active[future] = (task.key, alias)
                    active_aliases.add(alias)
                    admitted = True
                return admitted

            admit()
            while active:
                done, _ = wait(tuple(active), return_when=FIRST_COMPLETED)
                for future in done:
                    key, alias = active.pop(future)
                    active_aliases.discard(alias)
                    result = future.result()
                    resolved[key] = result
                    with self._lock:
                        if result.response.status in {"success", "pending"} or result.response.error == "EMPTY_TRANSLATION":
                            self._completed[result.task_key] = result
                        if self._inflight.get(result.task_key) is future:
                            self._inflight.pop(result.task_key, None)
                admit()

            # If no active task can restore provider health, all remaining
            # work is explicitly pending and can be retried by the caller.
            while pending:
                task = pending.popleft()
                resolved[task.key] = PoolResult(
                    task.key,
                    ProviderResponse(status="failed", error="NO_HEALTHY_PROVIDER", attempts=0),
                    source="pending")

            output: list[PoolResult] = []
            for key, item in entries:
                if isinstance(item, Future):
                    result = item.result()
                    with self._lock:
                        if result.response.status in {"success", "pending"} or result.response.error == "EMPTY_TRANSLATION":
                            self._completed[result.task_key] = result
                        if self._inflight.get(result.task_key) is item:
                            self._inflight.pop(result.task_key, None)
                elif isinstance(item, PoolResult):
                    result = item
                else:
                    future = external.get(key)
                    result = future.result() if future is not None else resolved[key]
                    if future is not None:
                        with self._lock:
                            if result.response.status in {"success", "pending"} or result.response.error == "EMPTY_TRANSLATION":
                                self._completed[result.task_key] = result
                            if self._inflight.get(result.task_key) is future:
                                self._inflight.pop(result.task_key, None)
                output.append(result)
            return output

    def stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {alias: vars(stats).copy() for alias, stats in self._stats.items()}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            healthy = sum(1 for stats in self._stats.values() if stats.state == HEALTHY)
            return {"provider_count": len(self.providers), "max_workers": self.max_workers,
                    "in_flight": len(self._inflight),
                    "completed": sum(result.response.status == "success" for result in self._completed.values()),
                    "held_pending": sum(result.response.status == "pending" for result in self._completed.values()),
                    "held_qa_failed": sum(result.response.error == "EMPTY_TRANSLATION" for result in self._completed.values()),
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
            prompt_version=str((context or {}).get("prompt_version", "")),
            translation_unit_field=(context or {}).get("translation_unit_field"))
        # Service callers already own a durable, ASIN-independent semantic
        # claim before protection replaces source facts with placeholders.
        # Reuse that identity instead of deduping unlike raw values by their
        # identical protected templates. This changes only the ephemeral pool
        # namespace; durable cache keys and existing attempt holds stay intact.
        # Other callers retain the established TranslationTask key contract.
        dispatch_key = str((context or {}).get("canonical_dispatch_key") or task.key)
        task = replace(task, key=dispatch_key, tm_key=dispatch_key)
        task = TranslationTask(task.key, task.text, task.asin, task.field,
                               {**task.context, **(context or {})}, task.tm_key)
        result = self.pool.submit([task])[0]
        response = result.response
        if result.source == "pending":
            response = replace(response, status="pending", error=response.error or "NO_HEALTHY_PROVIDER")
        return response


def preflight_qwen_provider_pool(config: Mapping[str, Any]) -> dict[str, Any]:
    """Read endpoint facts and credential *presence* only; never read key values."""
    rows, missing, seen = [], [], set()
    credential_names = set(os.environ)
    specs = config.get("providers") or []
    for spec in specs:
        alias = str(spec.get("name") or spec.get("alias") or "")
        key_env = str(spec.get("api_key_env") or "")
        endpoint_env = str(spec.get("endpoint_env") or "")
        base_env = str(spec.get("base_url_env") or "")
        endpoint, endpoint_source, is_base = qwen_endpoint_configuration(
            endpoint=spec.get("endpoint"), endpoint_env=endpoint_env,
            shared_endpoint=config.get("endpoint"), base_url_env=base_env)
        issues = []
        if not alias or alias in seen:
            issues.append("ALIAS_MISSING_OR_DUPLICATED")
        seen.add(alias)
        if not key_env or key_env not in credential_names:
            issues.append("CREDENTIAL_ENV_MISSING")
        try:
            endpoint = (normalize_qwen_base_url(endpoint) if is_base else validate_qwen_endpoint(endpoint)) if endpoint else None
        except ValueError as exc:
            endpoint = None
            issues.append(str(exc))
        if not endpoint:
            issues.append("ENDPOINT_MAPPING_MISSING")
        row = {"alias": alias, "api_key_env": key_env, "credential_present": key_env in credential_names,
            "endpoint_env": endpoint_env, "base_url_env": base_env, "endpoint": endpoint,
            "endpoint_source": endpoint_source,
            "model": spec.get("model") or os.getenv(str(spec.get("model_env") or "QWEN_MT_MODEL"))
                     or config.get("model") or "qwen-mt-flash"}
        rows.append(row)
        if issues:
            missing.append({"alias": alias, "issues": issues, "endpoint_env": endpoint_env, "api_key_env": key_env})
    workers = int(config.get("max_workers", len(rows)))
    if not rows or not 1 <= workers <= min(3, len(rows)):
        missing.append({"alias": "POOL", "issues": ["PROVIDER_COUNT_OR_WORKERS_INVALID"]})
    return {"status": "BLOCKED" if missing else "READY", "providers": rows, "missing": missing,
        "max_workers": workers, "dispatches": 0, "credential_values_read": False}


def build_qwen_provider_pool(config: Mapping[str, Any], *, transport_factory: Any = None) -> ProviderPool:
    """Build a pool from aliases without ever storing credentials in config."""
    specs = config.get("providers") if isinstance(config, Mapping) else None
    if not specs:
        specs = [{"name": "qwen-a", "type": "qwen-mt", "model": config.get("model"),
                  "endpoint_env": "QWEN_API_ENDPOINT", "api_key_env": "QWEN_API_KEY",
                  "rate": config.get("rate", 0.5)}]
    strict = bool(config.get("strict_provider_mapping"))
    preflight = preflight_qwen_provider_pool(config) if strict else None
    if preflight and preflight["status"] != "READY":
        raise ValueError("PROVIDER_CONFIGURATION_BLOCKED: " + str(preflight["missing"]))
    providers: dict[str, TranslationProvider] = {}
    for spec in specs:
        alias = str(spec.get("name") or spec.get("alias") or "qwen-a")
        endpoint = (next(row["endpoint"] for row in preflight["providers"] if row["alias"] == alias)
                    if preflight else spec.get("endpoint") or os.getenv(str(spec.get("endpoint_env", "QWEN_API_ENDPOINT"))))
        api_key = os.getenv(str(spec.get("api_key_env", "QWEN_API_KEY")))
        if strict and not api_key:
            raise ValueError("PROVIDER_CREDENTIAL_EMPTY: " + alias)
        model = spec.get("model") or os.getenv(str(spec.get("model_env") or "QWEN_MT_MODEL")) or config.get("model")
        kwargs = dict(model=model, endpoint=endpoint, api_key=api_key,
                      protocol=spec.get("protocol", config.get("protocol")),
                      rate=float(spec.get("rate", 0.5)), timeout=float(spec.get("timeout", 60)),
                      max_retries=int(spec.get("max_retries", 2)),
                      backoff_seconds=float(spec.get("backoff_seconds", 5.0)))
        if transport_factory is not None:
            kwargs["transport"] = transport_factory(alias)
        providers[alias] = QwenMTProvider(**kwargs)
    return ProviderPool(
        providers, max_workers=min(len(providers), int(config.get("max_workers", len(providers)))),
        failover=bool(config.get("failover", True)))
