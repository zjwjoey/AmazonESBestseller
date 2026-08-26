"""DeepSeek-compatible translation client with an on-disk ASIN cache.

This module is intentionally independent from Playwright and the pipeline.  It
uses one JSON-mode chat completion per ASIN, keeps raw Spanish input outside the
Chinese overlay, and treats malformed/failed responses as explicit failures.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib import error, request


ALLOWED_FIELDS = (
    "title_zh",
    "category_l1_zh",
    "category_l2_zh",
    "category_l3_zh",
    "leaf_category_zh",
    "selected_variation_zh",
    "specification_zh",
    "product_details_zh",
    "feature_bullets_zh",
)

_INPUT_FIELDS = (
    "title_es_raw",
    "category_l1",
    "category_l2",
    "category_l3",
    "leaf_category",
    "selected_variation_raw",
    "specification_es",
    "spec_v2",
    "product_details_es",
    "feature_bullets_es",
)
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)


class TranslationError(RuntimeError):
    """Raised internally for a retryable or malformed translation response."""


def _default_transport(url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        # Preserve status for retry classification without ever echoing headers
        # (which may contain the bearer token).
        raise TranslationError("HTTP %s" % exc.code) from exc
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise TranslationError(type(exc).__name__) from exc


def _endpoint(value: Optional[str]) -> str:
    raw = (value or os.getenv("DEEPSEEK_ENDPOINT") or "").strip()
    if not raw:
        base = (os.getenv("DEEPSEEK_BASE_URL") or os.getenv("DS_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        raw = base + "/chat/completions"
    elif not raw.rstrip("/").endswith("/chat/completions"):
        raw = raw.rstrip("/") + "/chat/completions"
    return raw


def _json_content(content: Any) -> dict:
    if isinstance(content, Mapping):
        return dict(content)
    text = str(content or "").strip()
    match = _FENCE_RE.match(text)
    if match:
        text = match.group(1).strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise TranslationError("response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise TranslationError("response JSON must be an object")
    return value


class DeepSeekTranslator:
    """Translate approved display fields one ASIN at a time.

    ``transport`` is injectable for offline tests and must return the decoded
    JSON response.  The default transport sends the API key only as an HTTP
    Authorization header.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        cache_path: Optional[os.PathLike[str] | str] = None,
        transport: Optional[Callable[[str, Mapping[str, str], Mapping[str, Any], float], Mapping[str, Any]]] = None,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = (api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("DS_API_KEY") or "").strip()
        if not self.api_key:
            raise ValueError("缺少 DEEPSEEK_API_KEY/DS_API_KEY")
        self.endpoint = _endpoint(endpoint)
        self.model = model or os.getenv("DEEPSEEK_MODEL") or os.getenv("DS_MODEL") or "deepseek-chat"
        self.cache_path = Path(cache_path) if cache_path else None
        self.transport = transport or _default_transport
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.timeout = float(timeout)
        self.cache: dict[str, dict] = self._load_cache()

    def _load_cache(self) -> dict[str, dict]:
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {str(k).strip().upper(): dict(v) for k, v in value.items() if isinstance(v, Mapping)}

    def save_cache(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.cache_path)

    @staticmethod
    def _source_payload(record: Mapping[str, Any]) -> dict[str, Any]:
        payload = {key: record.get(key) for key in _INPUT_FIELDS if record.get(key) not in (None, "", [], {})}
        # Keep the source evidence bounded enough for a single request while
        # retaining the full details that the collector already rendered.
        return payload

    def _request_payload(self, record: Mapping[str, Any]) -> dict[str, Any]:
        asin = str(record.get("asin") or record.get("ASIN") or "").strip().upper()
        source = self._source_payload(record)
        system = (
            "你是 Amazon.es 商品资料翻译器。请把给定西班牙语字段翻译成简洁准确的简体中文。"
            "只输出 JSON 对象，不要 Markdown，不要新增事实；无法翻译的字段保留原文。"
            "允许的键只有 title_zh、category_l1_zh、category_l2_zh、category_l3_zh、"
            "leaf_category_zh、selected_variation_zh、specification_zh、product_details_zh、feature_bullets_zh。"
        )
        user = json.dumps({"asin": asin, "source": source, "output_keys": list(ALLOWED_FIELDS)}, ensure_ascii=False)
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

    def _call_once(self, record: Mapping[str, Any]) -> dict:
        payload = self._request_payload(record)
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key}
        response = self.transport(self.endpoint, headers, payload, self.timeout)
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError("missing chat completion content") from exc
        obj = _json_content(content)
        result = {"asin": str(record.get("asin") or record.get("ASIN") or "").strip().upper()}
        for key in ALLOWED_FIELDS:
            value = obj.get(key)
            if value not in (None, ""):
                result[key] = str(value).strip()
        if len(result) == 1:
            raise TranslationError("translation object contains no allowed fields")
        result["translation_status"] = "success"
        return result

    def translate_record(self, record: Mapping[str, Any]) -> dict:
        asin = str(record.get("asin") or record.get("ASIN") or "").strip().upper()
        if not asin:
            return {"asin": "", "translation_status": "failed", "translation_error": "missing asin"}
        cached = self.cache.get(asin)
        if cached is not None:
            return dict(cached)
        last_error = "unknown error"
        for attempt in range(self.max_retries + 1):
            try:
                result = self._call_once(record)
                self.cache[asin] = result
                return dict(result)
            except Exception as exc:  # bounded retry; failure is isolated per ASIN
                last_error = str(exc) or type(exc).__name__
                if attempt < self.max_retries and self.backoff_seconds:
                    time.sleep(self.backoff_seconds * (2**attempt))
        result = {"asin": asin, "translation_status": "failed", "translation_error": last_error}
        self.cache[asin] = result
        return dict(result)

    def translate_records(self, records: Iterable[Mapping[str, Any]]) -> list[dict]:
        results = []
        for record in records:
            results.append(self.translate_record(record))
            self.save_cache()
        return results

