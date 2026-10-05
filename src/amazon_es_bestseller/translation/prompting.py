"""Versioned Qwen-MT guidance using the provider's supported controls."""
from __future__ import annotations

import re
from typing import Any, Mapping

from .field_contract import canonical_translation_field_type

PROMPT_VERSION = "amazon-es-retail-v2"
_COMMON = (
    "The source is one publicly visible Amazon.es product field. Translate only "
    "the supplied field from Spanish into concise, natural Simplified Chinese. "
    "Do not add, infer, remove, or move facts. Preserve every number, unit, "
    "quantity, dimension, model, interface, standard, certification, negation, "
    "compatibility statement, and protected token exactly in meaning."
)
_GUIDANCE = {
    "title": "This is a retail product title: state the product type clearly and keep key specifications and compatibility tokens.",
    "specification": "This is a compact specification: preserve structure, options, quantities and units; never convert units.",
    "variation": "This is the selected variation: preserve exact size, color, pack count, dose, model and option.",
    "product_details": "This is a structured attribute label or value: preserve key meaning, negation, brands, standards and certifications.",
    "feature_bullets": "This is one product feature bullet: preserve claim boundaries, use, compatibility and technical facts without embellishment.",
    "description": "This is a product description: preserve paragraphs, actions, uses, negation and compatibility; do not summarize.",
    "category": "This is an Amazon category label: return only its direct Chinese meaning and do not invent hierarchy.",
    "brand": "This is a brand identity: preserve it verbatim.",
}
_TERMS = (
    ("bolsa térmica", "保温袋"),
    ("recipiente reutilizable", "可重复使用容器"),
    ("pastillas de limpieza", "清洁片"),
    ("mini motosierra", "迷你链锯"),
    ("motosierra mini", "迷你链锯"),
    ("hilo de corte", "割草机线"),
    ("hilo para desbrozadora", "割草机线"),
    ("portafiltro", "咖啡机滤杯手柄"),
    ("voltaje", "电压"),
    ("potencia", "功率"),
    ("sin alcohol", "不含酒精"),
    ("país de origen", "原产国"),
)


def domain_prompt(field: str) -> str:
    kind = canonical_translation_field_type(field)
    return (_COMMON + " " + _GUIDANCE.get(kind, "")).strip()


def relevant_terms(text: str) -> list[dict[str, str]]:
    value = str(text or "")
    return [{"source": source, "target": target} for source, target in _TERMS
            if re.search(r"(?<!\w)%s(?!\w)" % re.escape(source), value, re.I)]


def translation_options(text: str, *, field: str, source_language: str,
                        target_language: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    options: dict[str, Any] = {"source_lang": source_language, "target_lang": target_language,
                               "domains": domain_prompt(field)}
    terms = relevant_terms(text)
    for item in (context or {}).get("terms", []):
        if isinstance(item, Mapping) and item.get("source") and item.get("target"):
            pair = {"source": str(item["source"]), "target": str(item["target"])}
            if pair not in terms:
                terms.append(pair)
    if terms:
        options["terms"] = terms
    return options
