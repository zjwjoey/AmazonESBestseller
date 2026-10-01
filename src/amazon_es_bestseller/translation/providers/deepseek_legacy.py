"""Opt-in adapter exposing the legacy DeepSeek translator as a V2 provider.

The legacy ASIN-level translator remains unchanged; this adapter only makes
its transport boundary usable by callers that explicitly select it.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..ds import DeepSeekTranslator
from .base import ProviderResponse, TranslationProvider


class DeepSeekLegacyProvider(TranslationProvider):
    name = "deepseek-legacy"

    def __init__(self, translator: Optional[DeepSeekTranslator] = None, **kwargs: Any):
        self.translator = translator or DeepSeekTranslator(**kwargs)

    @property
    def model(self) -> str:
        return getattr(self.translator, "model", "deepseek-chat")

    def translate(self, text: str, *, asin: str, field: str,
                  context: Optional[Dict[str, Any]] = None) -> ProviderResponse:
        # Deliberately use the legacy request builder only when explicitly selected.
        try:
            source_key = field
            aliases = {"brand": "title_es_raw", "title_es": "title_es_raw",
                       "selected_variant_es": "selected_variation_raw"}
            source_key = aliases.get(source_key, source_key)
            result = self.translator._call_once({"asin": asin, source_key: text})
            target = {"title_es_raw": "title_zh", "specification_es": "specification_zh",
                      "product_details_es": "product_details_zh", "feature_bullets_es": "feature_bullets_zh",
                      "selected_variation_raw": "selected_variation_zh"}.get(source_key, source_key)
            translated = result.get(target) if isinstance(result, dict) else ""
            if translated:
                return ProviderResponse(text=str(translated), provider=self.name, model=self.model)
            return ProviderResponse(provider=self.name, model=self.model, status="failed",
                                    error="legacy provider returned no field text")
        except Exception as exc:  # legacy adapter must isolate one field
            return ProviderResponse(provider=self.name, model=self.model, status="failed", error=str(exc))
