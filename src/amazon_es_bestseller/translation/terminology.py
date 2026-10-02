"""Deterministic terminology bridge for Translation V2.

The existing ``zh.py``/``full_detail.py`` rules remain the source of truth for
known Spanish terms.  V2 applies them around the provider rather than copying
or replacing those legacy modules.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .full_detail import render_bullets_zh, render_details_zh
from .zh import TERMS, apply_terms, dedupe_technical_units, translate_value
from .dictionary_service import DictionaryService, normalize_key

SPEC_LABELS = {
    "dimensiones": "尺寸", "dimension": "尺寸", "tamaño": "尺寸",
    "dimensiones del producto": "产品尺寸", "dimensiones del paquete": "包装尺寸",
    "capacidad": "容量", "peso": "重量", "cantidad": "件数",
    "peso del producto": "产品重量", "peso artículo": "商品重量",
    "número de productos": "产品数量", "numero de productos": "产品数量",
    "número de piezas": "件数", "numero de piezas": "件数",
    "número de unidades": "数量", "numero de unidades": "数量",
    "cantidad de compartimentos": "隔层数量",
    "material": "材质", "potencia": "功率", "voltaje": "电压",
    "vataje": "功率", "frecuencia": "频率",
    "color": "颜色", "modelo": "型号", "número de modelo": "型号",
    "referencia oem": "OEM参考号", "referencia del fabricante": "制造商参考编号",
    "referencia": "参考号", "requiere montaje": "需要组装",
}

_DIMENSION_AXIS_RE = re.compile(
    r"(?P<number>\d+(?:[.,]\d+)?)\s*"
    r"(?P<axis>l\.|f\.|an\.|al\.|grosor|ancho|alto|largo|longitud|"
    r"anchura|altura|profundidad|espesor)(?=\s|[xX×;,)])",
    re.I,
)

_DICTIONARY = DictionaryService()
_UNIT_KEYS = sorted(_DICTIONARY.units, key=len, reverse=True)
_UNIT_DISPLAY_RE = re.compile(
    r"(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>"
    + "|".join(re.escape(value) for value in _UNIT_KEYS)
    + r")(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])",
    re.I,
)


def _load_contextual_terms() -> list[tuple[list[str], dict[str, str]]]:
    path = Path(__file__).with_name("dictionaries") / "contextual_terms.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = []
    for selector, replacements in data.items():
        if not isinstance(replacements, dict):
            continue
        selectors = [normalize_key(part) for part in str(selector).split("&&") if normalize_key(part)]
        rows.append((selectors, {str(old): str(new) for old, new in replacements.items()}))
    return rows


_CONTEXTUAL_TERMS = _load_contextual_terms()


def contextual_postprocess(field: str, translated: str, source: str) -> str:
    """Apply source-conditioned corrections for high-risk automotive polysemy.

    The correction is only eligible when the source contains the complete
    selector phrase.  This prevents a global replacement such as ``mosquitos``
    or ``jump starter`` from changing unrelated categories or brand names.
    """
    if field not in {"title_es_raw", "title_es", "title"} or not translated:
        return str(translated or "")
    source_key = normalize_key(source)
    output = str(translated)
    for selectors, replacements in _CONTEXTUAL_TERMS:
        if not all(selector in source_key for selector in selectors):
            continue
        for old, new in replacements.items():
            output = output.replace(old, new)
    return output


def strip_display_brand(translated: str, brand: str) -> str:
    """Remove only the explicit brand value from the displayed Chinese name."""
    output = str(translated or "").strip()
    value = str(brand or "").strip()
    if not output or not value:
        return output
    pattern = re.compile(r"(?<![\w-])" + re.escape(value) + r"\+?(?![\w-])", re.I)
    output = pattern.sub("", output)
    output = re.sub(r"^[\s|:：,，、–—-]+|[\s|:：,，、–—-]+$", "", output)
    return re.sub(r"\s{2,}", " ", output).strip()


def normalize_unit_display(value: str) -> str:
    """Normalize only explicit number+unit pairs through the unit dictionary.

    Protected tokens are restored after the provider call, so full Spanish
    units such as ``9 Voltios`` can otherwise leak into Chinese display text.
    This pass is intentionally numeric-context-only: it cannot translate a
    brand, model, prose word, or add a fact absent from the provider output.
    """
    def replace(match: re.Match[str]) -> str:
        translated = _DICTIONARY.lookup_unit(match.group("unit"))
        if translated is None:
            return match.group(0)
        return match.group("number") + translated

    return _UNIT_DISPLAY_RE.sub(replace, str(value or ""))


def normalize_spec_value(value: str) -> str:
    """Render known units and packaging phrases in one specification value."""
    # Amazon.es dimension exports often attach an axis marker to each number:
    # ``19,1l. x 7,2an. x 3,5Grosor centímetros``.  These markers are not
    # units and must be removed before the shared dimension renderer runs.
    normalized = _DIMENSION_AXIS_RE.sub(r"\g<number>", str(value or ""))
    normalized = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "×", normalized)
    out = normalize_unit_display(translate_value(normalized))
    for source, target in sorted(_DICTIONARY.packaging.items(), key=lambda item: -len(item[0])):
        out = re.sub(re.escape(source), target, out, flags=re.I)
    return out


def postprocess(field: str, translated: str, source: str) -> str:
    """Normalize known terms without inventing facts or changing raw source."""
    if not translated:
        return ""
    if field in {"brand", "brand_es"}:
        # Brand is identity data, never a translation target.
        return str(source)
    out = apply_terms(str(translated))
    out = normalize_unit_display(out)
    out = dedupe_technical_units(out)
    if field == "specification_es":
        # Only normalize labels/known values; the provider output remains the
        # source of non-deterministic text.
        out = apply_terms(out)
    return out.strip()


def deterministic_detail(source: Any) -> str:
    """Render structured raw detail/bullet lists through existing rules."""
    if isinstance(source, list):
        if all(isinstance(row, dict) and ("label_raw" in row or "value_raw" in row) for row in source):
            return render_details_zh(source)
        return render_bullets_zh(source)
    if isinstance(source, dict):
        return render_details_zh([{"label_raw": k, "value_raw": v} for k, v in source.items()])
    return ""


def deterministic_specification(source: str) -> str:
    """Translate structured specification labels and known values only."""
    parts = [part.strip() for part in re.split(r"[;；]\s*|\s+/\s+", str(source or "")) if part.strip()]
    rendered = []
    for part in parts:
        match = re.match(r"^([^:：]+)\s*[:：]\s*(.*)$", part)
        if match:
            label = match.group(1).strip()
            value = match.group(2).strip()
            dictionary_label = _DICTIONARY.lookup_attribute_label(label.replace("_", " "))
            label_zh = dictionary_label or SPEC_LABELS.get(label.casefold(), label)
            if label.casefold() == "tamaño":
                # ``Tamaño`` is a selection/packaging value in this feed, not
                # necessarily a physical dimension. Keep the distinction in
                # the display layer while preserving the source value.
                value_key = value.casefold()
                if re.search(r"\d+(?:[.,]\d+)?\s*(?:ml|l|litro|litros|毫升|升)\b", value_key):
                    label_zh = "规格"
                elif re.search(r"(?:xx[- ]?large|x[- ]?large|medium|small|\b(?:s|m|l|xl|xxl)\b)", value_key):
                    label_zh = "尺码"
                else:
                    label_zh = "规格"
            rendered.append("%s：%s" % (label_zh, normalize_spec_value(value)))
        else:
            rendered.append(normalize_spec_value(part))
    return "；".join(rendered)


def specification_is_deterministic(source: str) -> bool:
    """Conservative gate: only use rules when no unknown Spanish words remain."""
    # Explicit model/OEM references are identity values.  Their labels can be
    # translated deterministically while the value must be copied verbatim,
    # even when it is a brand-like name such as ``Cera Tec``.
    if re.match(
        r"^\s*(?:modelo|n[uú]mero\s+de\s+modelo|referencia(?:\s+oem)?|"
        r"referencia\s+del\s+fabricante)\s*[:：]\s*\S+",
        str(source or ""),
        re.I,
    ):
        return True
    known_words = {"de", "del", "la", "el", "y", "con", "sin", "pack", "set",
                   "unidad", "unidades", "por", "para"}
    for spanish, _ in TERMS:
        known_words.update(spanish.casefold().split())
    for label in SPEC_LABELS:
        known_words.update(label.split())
    for unit in _DICTIONARY.units:
        known_words.update(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", unit.casefold()))
    for packaging in _DICTIONARY.packaging:
        known_words.update(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", packaging.casefold()))
    text = re.sub(r"\d+(?:[.,]\d+)?", " ", str(source or ""))
    text = re.sub(r"\b[A-Z0-9]+(?:[-/][A-Z0-9]+)*\b", " ", text)
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", text.casefold())
    return all(word in known_words for word in words)
