# -*- coding: utf-8 -*-
"""中文业务派生层：确定性词典（zh）、商品类型识别（product_type）、ASIN 例外（exceptions）。"""
"""Legacy translation modules plus the opt-in Translation V2 pipeline."""

from .cache import TranslationCache
from .service import TranslationService

__all__ = ["TranslationCache", "TranslationService"]
