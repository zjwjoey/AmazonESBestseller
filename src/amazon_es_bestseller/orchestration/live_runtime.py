"""Explicit, bounded live-runtime factories for Production V1.

Nothing in this module starts a browser or a provider request merely by being
imported.  ``commands.run`` calls these factories only after both the task
configuration and an explicit CLI acknowledgement allow the live path.
"""
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from ..collection.quota import normalize_source_url
from ..collection.task import validate_task_plan
from ..translation.budget import BudgetLedger, BudgetedProvider, VerifiedPriceCard
from ..translation.providers.qwen_mt import QwenMTProvider
from ..translation.providers.base import ProviderResponse, TranslationProvider
from .task_config import TaskConfig
from .workflow import ExistingV1DetailCollector, ExistingV1SnapshotCollector


class LiveRuntimeError(RuntimeError):
    """Raised before a live browser or provider request is allowed."""


class _TruncationFailClosedProvider(TranslationProvider):
    """Turn a provider-side output-limit response into a non-promotable field.

    The existing Qwen adapter preserves its raw response.  This tiny boundary
    consumes that evidence before the budget ledger settles the request, so a
    `finish_reason=length` response is conservatively charged and can never
    become a translation success.
    """

    def __init__(self, provider: TranslationProvider) -> None:
        self.provider, self.name = provider, provider.name

    @property
    def model(self) -> str:
        return self.provider.model

    @staticmethod
    def _finish_reason(raw: Mapping[str, Any] | None) -> str:
        body = raw if isinstance(raw, Mapping) else {}
        choices = body.get("choices")
        if not isinstance(choices, list):
            output = body.get("output")
            choices = output.get("choices") if isinstance(output, Mapping) else []
        first = choices[0] if isinstance(choices, list) and choices else {}
        return str(first.get("finish_reason") or "") if isinstance(first, Mapping) else ""

    def translate(self, text: str, *, asin: str, field: str, source_language: str = "es",
                  target_language: str = "zh-CN", context: dict[str, Any] | None = None) -> ProviderResponse:
        response = self.provider.translate(text, asin=asin, field=field, source_language=source_language,
                                           target_language=target_language, context=context)
        if response.status == "success" and self._finish_reason(response.raw).casefold() == "length":
            return ProviderResponse(text=response.text, provider=response.provider or self.name,
                                    model=response.model or self.model, status="failed",
                                    error="QWEN_OUTPUT_TRUNCATED", attempts=response.attempts,
                                    raw=dict(response.raw or {}))
        return response


def _config_path(value: object, root: Path, *, code: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise LiveRuntimeError(code)
    path = Path(raw)
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def _integer(value: object, *, code: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise LiveRuntimeError(code) from exc
    if parsed < 1:
        raise LiveRuntimeError(code)
    return parsed


@dataclass(frozen=True)
class LiveTransportScope:
    """A local, per-run ceiling layered on the reviewed source-plan gate."""

    max_ranking_pages: int
    max_detail_requests: int
    postal_code: str
    headful: bool
    profile_dir: str
    manual_assist: bool
    challenge_wait_seconds: float
    reviewed_plan_path: Path
    reviewed_plan_hash: str
    target_unique: int
    category_count: int
    source_counts: Mapping[str, int]


def load_live_transport_scope(task: TaskConfig, *, config_dir: str | Path) -> LiveTransportScope:
    """Validate a reviewed task plan and the explicit local request ceilings."""
    root = Path(config_dir).resolve()
    raw = task.raw.get("live_transport")
    if not isinstance(raw, Mapping) or raw.get("enabled") is not True:
        raise LiveRuntimeError("LIVE_TRANSPORT_CONFIG_DISABLED")
    if str(raw.get("kind") or "browser-v1").strip().lower() != "browser-v1":
        raise LiveRuntimeError("LIVE_TRANSPORT_KIND_UNSUPPORTED")
    budget = raw.get("request_budget")
    if not isinstance(budget, Mapping):
        raise LiveRuntimeError("LIVE_TRANSPORT_REQUEST_BUDGET_REQUIRED")
    max_ranking_pages = _integer(budget.get("max_ranking_pages"),
                                 code="LIVE_TRANSPORT_RANKING_BUDGET_INVALID")
    max_detail_requests = _integer(budget.get("max_detail_requests"),
                                   code="LIVE_TRANSPORT_DETAIL_BUDGET_INVALID")
    if len(task.source_urls) * task.pages_per_url > max_ranking_pages:
        raise LiveRuntimeError("LIVE_TRANSPORT_RANKING_BUDGET_EXCEEDED")

    plan_path = task.reviewed_task_plan or _config_path(
        task.raw.get("reviewed_task_plan"), root,
        code="LIVE_TRANSPORT_REVIEWED_PLAN_REQUIRED")
    current_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    if not task.reviewed_task_plan_hash or current_hash != task.reviewed_task_plan_hash:
        raise LiveRuntimeError("LIVE_TRANSPORT_REVIEWED_PLAN_FINGERPRINT_MISMATCH")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        reviewed = validate_task_plan(plan, plan_path=plan_path)
    except (OSError, ValueError) as exc:
        raise LiveRuntimeError("LIVE_TRANSPORT_SCOPE_GATE_FAILED:%s" % exc) from exc
    if str(reviewed.get("task_id") or "") != task.task_id:
        raise LiveRuntimeError("LIVE_TRANSPORT_TASK_ID_MISMATCH")
    approved_urls = {
        normalize_source_url(source.get("source_url"))
        for category in reviewed.get("categories") or []
        for source in category.get("sources") or []
        if isinstance(source, Mapping)
    }
    if not set(map(normalize_source_url, task.source_urls)).issubset(approved_urls):
        raise LiveRuntimeError("LIVE_TRANSPORT_SOURCE_OUT_OF_REVIEWED_SCOPE")
    role_counts = {"primary": 0, "reserve": 0}
    for category in reviewed.get("categories") or []:
        for source in category.get("sources") or []:
            role = str(source.get("role") or "").casefold()
            if role in role_counts:
                role_counts[role] += 1
    # The reviewed 5,500 task is deliberately frozen to the existing category
    # tree and URL set.  Other small, separately reviewed tasks still use the
    # same plan/hash/scope gate without pretending to be this task.
    if task.task_id == "amazon_es_bestseller_5500_202610":
        if (int(reviewed.get("target_unique") or 0) != 5500
                or len(reviewed.get("categories") or []) != 15
                or role_counts != {"primary": 55, "reserve": 31}
                or int(reviewed.get("pages_per_url") or 0) != 2):
            raise LiveRuntimeError("LIVE_TRANSPORT_5500_SCOPE_INVALID")
    try:
        challenge_wait = float(raw.get("challenge_wait_seconds", 180.0))
    except (TypeError, ValueError) as exc:
        raise LiveRuntimeError("LIVE_TRANSPORT_CHALLENGE_WAIT_INVALID") from exc
    if challenge_wait < 0:
        raise LiveRuntimeError("LIVE_TRANSPORT_CHALLENGE_WAIT_INVALID")
    postal_code = str(raw.get("postal_code") or "28001").strip()
    if len(postal_code) != 5 or not postal_code.isdigit():
        raise LiveRuntimeError("LIVE_TRANSPORT_POSTAL_CODE_INVALID")
    return LiveTransportScope(
        max_ranking_pages=max_ranking_pages,
        max_detail_requests=max_detail_requests,
        postal_code=postal_code,
        headful=bool(raw.get("headful", False)),
        profile_dir=str(raw.get("profile_dir") or "").strip(),
        manual_assist=bool(raw.get("manual_assist", False)),
        challenge_wait_seconds=challenge_wait,
        reviewed_plan_path=plan_path,
        reviewed_plan_hash=current_hash,
        target_unique=int(reviewed.get("target_unique") or 0),
        category_count=len(reviewed.get("categories") or []),
        source_counts=role_counts,
    )


class _BudgetedDetailCollector:
    """Enforce a declared ASIN request ceiling before invoking V1 collection."""

    def __init__(self, session: Any, maximum: int) -> None:
        self._collector = ExistingV1DetailCollector(session)
        self._maximum = maximum
        self._requested: set[str] = set()

    def __call__(self, asins: list[str], session: Any, output_root: str, **kwargs: Any) -> list[dict]:
        requested = {str(asin or "").strip().upper() for asin in asins if str(asin or "").strip()}
        if len(self._requested | requested) > self._maximum:
            raise LiveRuntimeError("LIVE_TRANSPORT_DETAIL_BUDGET_EXCEEDED")
        self._requested.update(requested)
        return self._collector(asins, session, output_root, **kwargs)


class ReviewedV1Transport:
    """Context manager which opens exactly one reviewed V1 browser session."""

    def __init__(self, task: TaskConfig, scope: LiveTransportScope) -> None:
        self.task, self.scope = task, scope
        self._browser_context: Any = None
        self.session: Any = None
        self.snapshot_collector: ExistingV1SnapshotCollector | None = None
        self.detail_collector: _BudgetedDetailCollector | None = None
        self._last_navigation_url = ""
        self._last_http_status: int | None = None
        self._navigation_counts = {name: 0 for name in (
            "homepage", "setup", "delivery", "ranking", "pagination", "reload",
            "detail", "other", "unknown",
        )}
        self._category_navigation_counts: dict[str, int] = {}
        self._navigation_observer_installed = False
        self.failure_evidence: dict[str, Any] = {}

    @staticmethod
    def _navigation_kind(url: object) -> tuple[str, str]:
        """Classify top-level Amazon documents, never subresources."""
        parsed = urlparse(str(url or ""))
        host, path = (parsed.hostname or "").casefold(), parsed.path.casefold()
        if not host.endswith("amazon.es"):
            return "unknown", ""
        if path in {"", "/"}:
            return "homepage", ""
        if "/gp/bestsellers" in path:
            category = path.split("/gp/bestsellers", 1)[1].strip("/")
            return ("pagination" if "pg=" in parsed.query.casefold() else "ranking"), category
        if any(marker in path for marker in ("/dp/", "/gp/product/", "/gp/aw/d/", "/product/")):
            return "detail", ""
        return "other", ""

    def _observe_navigation(self, frame: Any) -> None:
        page = getattr(self.session, "page", None)
        if frame is not getattr(page, "main_frame", None):
            return
        kind, category = self._navigation_kind(getattr(frame, "url", ""))
        if kind == "unknown":
            return
        self._navigation_counts[kind] += 1
        if category:
            self._category_navigation_counts[category] = (
                self._category_navigation_counts.get(category, 0) + 1)

    def _track_session_goto(self) -> None:
        """Retain the last status while the browser keeps normal V1 pacing."""
        original = getattr(self.session, "goto", None)
        if not callable(original):
            return

        def tracked_goto(url: str, *args: Any, **kwargs: Any) -> Any:
            self._last_navigation_url = str(url)
            status = original(url, *args, **kwargs)
            self._last_http_status = status if isinstance(status, int) else None
            return status

        self.session.goto = tracked_goto
        page = getattr(self.session, "page", None)
        on = getattr(page, "on", None)
        if callable(on):
            on("framenavigated", self._observe_navigation)
            self._navigation_observer_installed = True

    def _page_evidence(self) -> dict[str, Any]:
        """Read the current document only; no extra navigation or retry."""
        from ..access.detector import detect_access_status

        page = getattr(self.session, "page", None)
        html = ""
        screenshot = b""
        try:
            html = str(page.content() if page is not None else "")
        except Exception as exc:
            html_error = "%s: %s" % (type(exc).__name__, exc)
        else:
            html_error = ""
        try:
            candidate = page.screenshot(type="png") if page is not None else b""
            screenshot = bytes(candidate) if isinstance(candidate, (bytes, bytearray)) else b""
        except Exception as exc:
            screenshot_error = "%s: %s" % (type(exc).__name__, exc)
        else:
            screenshot_error = ""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        state = detect_access_status(self._last_http_status, html).value
        return {
            "homepage_html": html,
            "screenshot_png": screenshot,
            "final_url": str(getattr(page, "url", "") or self._last_navigation_url),
            "http_status": self._last_http_status,
            "access_state": state,
            "visible_text_excerpt": text[:2000],
            "html_capture_error": html_error,
            "screenshot_capture_error": screenshot_error,
            "delivery": dict(getattr(self.session, "delivery_diagnostics", {}) or {}),
        }

    def transport_observations(self) -> dict[str, Any]:
        """Stable counters: top-level Amazon documents, not page subresources."""
        counts = dict(self._navigation_counts)
        return {
            "definition": "top-level Amazon.es document navigations observed via Playwright; "
                          "delivery UI submissions are reported separately and subresources are excluded",
            "observer_installed": self._navigation_observer_installed,
            "site_navigation_total": sum(counts.values()),
            "site_navigation_counts": counts,
            "category_navigation_counts": dict(self._category_navigation_counts),
            "last_requested_url": self._last_navigation_url,
            "last_http_status": self._last_http_status,
            "delivery": dict(getattr(self.session, "delivery_diagnostics", {}) or {}),
        }

    def __enter__(self) -> "ReviewedV1Transport":
        # Imported here so offline workflows and their tests need no Playwright.
        from ..access.browser import BrowserSession
        from ..access.location import ensure_spain_delivery

        self._browser_context = BrowserSession(
            headless=not self.scope.headful,
            profile_dir=self.scope.profile_dir or None,
        )
        try:
            self.session = self._browser_context.__enter__()
            self._track_session_goto()
            self.session.challenge_wait_seconds = self.scope.challenge_wait_seconds
            self.session.manual_assist = self.scope.manual_assist
            # This is a normal Amazon UI check, not a bypass. Any access
            # signal raises through the existing AccessGate before ranking
            # collection.
            ensure_spain_delivery(self.session, self.scope.postal_code)
            self.snapshot_collector = ExistingV1SnapshotCollector(self.session)
            self.detail_collector = _BudgetedDetailCollector(
                self.session, self.scope.max_detail_requests)
            return self
        except BaseException as exc:
            # A failed BrowserSession enter or delivery check happens before
            # the outer ``with`` body exists, so clean it up here rather than
            # leaking a Playwright driver on a fail-closed stop.
            self.failure_evidence = self._page_evidence()
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if self._browser_context is not None:
            return bool(self._browser_context.__exit__(exc_type, exc, traceback))
        return False


def build_qwen_provider(task: TaskConfig, *, run_dir: str | Path,
                        allow_qwen_translation: bool, offline: bool) -> BudgetedProvider | None:
    """Create the only CLI Qwen route: verified card + durable <=5 CNY ledger."""
    mode = str(task.translation.get("provider_mode") or "fake").strip().lower()
    if mode == "fake":
        return None
    if mode != "qwen-mt":
        raise LiveRuntimeError("TRANSLATION_PROVIDER_UNSUPPORTED")
    if offline:
        raise LiveRuntimeError("OFFLINE_PROVIDER_MUST_BE_FAKE")
    if not allow_qwen_translation or task.translation.get("enabled") is not True:
        raise LiveRuntimeError("QWEN_TRANSLATION_NOT_AUTHORIZED")
    provider = QwenMTProvider(
        endpoint=task.translation.get("endpoint"),
        model=str(task.translation.get("model") or "qwen-mt-flash"),
        protocol=task.translation.get("protocol"),
        timeout=float(task.translation.get("timeout", 60)),
        # Provider-level retries would make one ledger reservation hide
        # multiple provider calls.  A later retry must be a fresh workflow
        # action/reservation instead.
        max_retries=0,
        rate=float(task.translation.get("rate", 0.5)),
    )
    endpoint = urlparse(str(provider.endpoint or ""))
    if (endpoint.scheme != "https" or (endpoint.hostname or "").casefold()
            not in {"dashscope.aliyuncs.com"}):
        raise LiveRuntimeError("QWEN_ENDPOINT_UNAPPROVED")
    if not provider.api_key:
        raise LiveRuntimeError("QWEN_CREDENTIAL_UNAVAILABLE")
    pricing = task.translation.get("pricing_verification")
    if not isinstance(pricing, Mapping):
        raise LiveRuntimeError("BUDGET_PRICE_UNVERIFIED")
    if str(pricing.get("endpoint_host") or "").casefold() != (endpoint.hostname or "").casefold():
        raise LiveRuntimeError("QWEN_PRICE_ENDPOINT_MISMATCH")
    if str(pricing.get("billing_currency") or "").upper() != "CNY":
        raise LiveRuntimeError("QWEN_BILLING_CURRENCY_UNVERIFIED")
    source_hash = str(pricing.get("official_price_source_sha256") or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise LiveRuntimeError("QWEN_PRICE_EVIDENCE_HASH_REQUIRED")
    try:
        card = VerifiedPriceCard.from_mapping(
            pricing,
            provider=provider.name, model=provider.model,
        )
        max_input_tokens = int(task.translation.get("budget_max_input_tokens", 8192))
        max_output_tokens = int(task.translation.get("budget_max_output_tokens", 2048))
        prompt_overhead_tokens = int(task.translation.get("budget_prompt_overhead_tokens", 1024))
        if (max_input_tokens > 8192 or max_output_tokens > 8192
                or max_input_tokens + max_output_tokens + prompt_overhead_tokens > 16384):
            raise LiveRuntimeError("QWEN_CONTEXT_LIMIT_INVALID")
        ledger = BudgetLedger(
            Path(run_dir) / "budget" / "translation_budget_ledger.json",
            limit_cny=task.translation.get("budget_cny", "5.00"),
            price_card=card,
            safety_buffer_cny=task.translation.get("budget_safety_buffer_cny", "0.10"),
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            prompt_overhead_tokens=prompt_overhead_tokens,
            max_unique_asins=int(task.translation.get("max_unique_asins", 1500)),
        )
    except (ValueError, RuntimeError) as exc:
        raise LiveRuntimeError(str(exc)) from exc
    return BudgetedProvider(_TruncationFailClosedProvider(provider), ledger)


__all__ = ["LiveRuntimeError", "LiveTransportScope", "ReviewedV1Transport",
           "build_qwen_provider", "load_live_transport_scope"]
