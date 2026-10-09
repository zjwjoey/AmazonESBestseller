"""Qwen-MT provider adapter.

The endpoint/protocol is explicit and injectable.  No credential or endpoint
is hard-coded as a secret; the default endpoint is the public DashScope
OpenAI-compatible endpoint and can be replaced by configuration.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from .base import ProviderResponse, TranslationProvider


Transport = Callable[[str, Dict[str, str], Dict[str, Any], float], Dict[str, Any]]


def qwen_endpoint_configuration(*, endpoint: Optional[str] = None, endpoint_env: str = "",
                                shared_endpoint: Optional[str] = None, base_url_env: str = "",
                                allow_public_default: bool = False) -> tuple[Optional[str], str, bool]:
    """Select configured endpoint facts with the adapter's shared fallback order.

    The third return value distinguishes a base URL requiring a chat suffix.
    Strict callers never opt into the adapter's unconfigured public default.
    """
    candidates = [
        (endpoint, "configuration:alias.endpoint", False),
        (os.getenv(endpoint_env) if endpoint_env else None, "environment:" + endpoint_env, False),
        (shared_endpoint, "configuration:endpoint", False),
        (os.getenv("QWEN_API_ENDPOINT"), "environment:QWEN_API_ENDPOINT", False),
        (os.getenv("DASHSCOPE_API_ENDPOINT"), "environment:DASHSCOPE_API_ENDPOINT", False),
        (os.getenv(base_url_env) if base_url_env else None, "environment:" + base_url_env, True),
        (os.getenv("QWEN_MT_BASE_URL"), "environment:QWEN_MT_BASE_URL", True),
    ]
    for value, source, is_base in candidates:
        if value:
            return value, source, is_base
    if allow_public_default:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "adapter_default", False
    return None, "unconfigured", False


def validate_qwen_endpoint(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("QWEN_ENDPOINT_FORMAT_INVALID")
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or not parts.path.endswith("/chat/completions")):
        raise ValueError("QWEN_ENDPOINT_FORMAT_INVALID")
    return value


def normalize_qwen_base_url(value: str) -> str:
    """Use the existing OpenAI-compatible chat path, never infer a host route."""
    if not isinstance(value, str):
        raise ValueError("QWEN_BASE_URL_PATH_INVALID")
    value = value.rstrip("/")
    parts = urlsplit(value)
    if parts.path in {"/compatible-mode/v1", "/v1"}:
        value += "/chat/completions"
    elif parts.path not in {"/compatible-mode/v1/chat/completions", "/v1/chat/completions"}:
        raise ValueError("QWEN_BASE_URL_PATH_INVALID")
    return validate_qwen_endpoint(value)


class QwenMTProvider(TranslationProvider):
    name = "qwen-mt"
    _LANGUAGE_NAMES = {
        "es": "Spanish", "spanish": "Spanish",
        "zh": "Chinese", "zh-cn": "Chinese", "zh_cn": "Chinese",
        "simplified chinese": "Chinese", "chinese": "Chinese",
    }

    def __init__(self, *, api_key: Optional[str] = None,
                 endpoint: Optional[str] = None,
                 model: Optional[str] = None,
                 protocol: Optional[str] = None,
                 timeout: float = 60.0, max_retries: int = 2,
                 backoff_seconds: float = 5.0,
                 rate: float = 0.5,
                 transport: Optional[Transport] = None):
        self.api_key = api_key or os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        configured_endpoint, self.endpoint_source, is_base = qwen_endpoint_configuration(
            endpoint=endpoint, allow_public_default=True)
        self.endpoint = normalize_qwen_base_url(configured_endpoint) if is_base else configured_endpoint
        self._model = model or os.getenv("QWEN_MT_MODEL") or "qwen-mt-flash"
        self.protocol = protocol or os.getenv("QWEN_API_PROTOCOL", "openai_compatible")
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.rate = float(rate)
        if self.rate < 0:
            raise ValueError("rate must be >= 0 calls per second")
        self._min_interval = 1.0 / self.rate if self.rate else 0.0
        self._last_request_at: Optional[float] = None
        self.transport = transport or self._http_transport
        self.before_send = None

    @property
    def model(self) -> str:
        return self._model

    def _http_transport(self, url: str, headers: Dict[str, str],
                        payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:  # nosec B310 - configured endpoint
                body = response.read().decode("utf-8")
                return {"status_code": response.status, "body": json.loads(body)}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = {"error": body[:500]}
            return {"status_code": exc.code, "body": parsed}
        except (URLError, TimeoutError, OSError) as exc:
            return {"status_code": 599, "error": str(exc)}

    def _wait_for_rate_limit(self) -> None:
        """Keep serial attempts at or below the configured calls/second rate."""
        if not self._min_interval:
            return
        now = time.monotonic()
        if self._last_request_at is not None:
            remaining = self._min_interval - (now - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    @classmethod
    def _language_name(cls, value: str, fallback: str) -> str:
        raw = str(value or fallback).strip()
        return cls._LANGUAGE_NAMES.get(raw.casefold(), raw)

    def _payload(self, text: str, *, asin: str, field: str,
                 context: Dict[str, Any]) -> Dict[str, Any]:
        translation_options = {
            "source_lang": self._language_name(context.get("source_language"), "Spanish"),
            "target_lang": self._language_name(context.get("target_language"), "Chinese"),
        }
        if self.protocol == "openai_compatible":
            payload = {"model": self._model, "messages": [
                {"role": "user", "content": text}],
                "translation_options": translation_options}
            if context.get("max_output_tokens") is not None:
                payload["max_tokens"] = int(context["max_output_tokens"])
            return payload
        if self.protocol == "dashscope":
            payload = {"model": self._model,
                    "input": {"messages": [{"role": "user", "content": text}]},
                    "parameters": {"translation_options": translation_options}}
            if context.get("max_output_tokens") is not None:
                payload["parameters"]["max_tokens"] = int(context["max_output_tokens"])
            return payload
        raise ValueError("unsupported Qwen protocol: %s" % self.protocol)

    @staticmethod
    def _extract(body: Dict[str, Any]) -> str:
        choices = body.get("choices") if isinstance(body, dict) else None
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x)
                                  for x in content)
            if content:
                return str(content).strip()
        output = body.get("output") if isinstance(body, dict) else None
        if isinstance(output, dict):
            choices = output.get("choices")
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message") or {}
                content = message.get("content")
                if content:
                    return str(content).strip()
            if output.get("text"):
                return str(output["text"]).strip()
        if isinstance(body, dict) and body.get("text"):
            return str(body["text"]).strip()
        return ""

    def translate(self, text: str, *, asin: str, field: str,
                  source_language: str = "es", target_language: str = "zh-CN",
                  context: Optional[Dict[str, Any]] = None) -> ProviderResponse:
        if not self.api_key:
            return ProviderResponse(provider=self.name, model=self._model,
                                    status="failed", error="missing QWEN_API_KEY/DASHSCOPE_API_KEY",
                                    attempts=0)
        payload = self._payload(text, asin=asin, field=field,
                                context={"source_language": source_language,
                                         "target_language": target_language, **(context or {})})
        headers = {"Authorization": "Bearer " + self.api_key,
                   "Content-Type": "application/json"}
        last_error = "provider request failed"
        for attempt in range(1, self.max_retries + 2):
            self._wait_for_rate_limit()
            if self.before_send is not None and not self.before_send():
                return ProviderResponse(provider=self.name, model=self._model, status="pending",
                                        attempts=attempt - 1, error="PROVIDER_HALTED_BEFORE_SEND")
            try:
                response = self.transport(self.endpoint, headers, payload, self.timeout)
            except Exception:  # The request may already have been billed; never replay it.
                response = {"status_code": 599}
            if not isinstance(response, dict):
                response = {"status_code": 599}
            # Tests and alternate transports may return decoded provider JSON
            # directly instead of the {status_code, body} envelope.
            if isinstance(response, dict) and "status_code" not in response and (
                    "choices" in response or "output" in response or "text" in response):
                response = {"status_code": 200, "body": response}
            try:
                code = int(response.get("status_code", 200) or 0)
            except (TypeError, ValueError):
                code = 599
            if code == 599:
                return ProviderResponse(provider=self.name, model=self._model, status="pending",
                    error="TRANSPORT_OUTCOME_UNKNOWN", attempts=attempt, raw={"outcome_unknown": True})
            body = response.get("body") if isinstance(response, dict) else {}
            text_out = self._extract(body if isinstance(body, dict) else {})
            if 200 <= code < 300 and text_out:
                return ProviderResponse(text=text_out, provider=self.name,
                                        model=self._model, attempts=attempt, raw=body or {})
            if 200 <= code < 300 and not text_out:
                # An HTTP success without translated content is a QA failure,
                # not a transport failure.  Keep the marker so the service can
                # persist it and expose it in the field-closure report.
                return ProviderResponse(provider=self.name, model=self._model,
                                        status="failed", error="EMPTY_TRANSLATION",
                                        attempts=attempt, raw=body or {})
            detail = response.get("error") or (body.get("error") if isinstance(body, dict) else None)
            last_error = "HTTP %s%s" % (code, (": " + str(detail)[:300]) if detail else "")
            retryable = code == 429 or code >= 500
            if not retryable or attempt > self.max_retries:
                break
            if self.backoff_seconds:
                time.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        return ProviderResponse(provider=self.name, model=self._model, status="failed",
                                error=last_error, attempts=attempt, raw=response if isinstance(response, dict) else {})
