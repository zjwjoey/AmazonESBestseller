"""Deterministic terminology bridge for Translation V2.

The existing ``zh.py``/``full_detail.py`` rules remain the source of truth for
known Spanish terms.  V2 applies them around the provider rather than copying
or replacing those legacy modules.
"""
from __future__ import annotations

import re
from typing import Any

from .full_detail import render_bullets_zh, render_details_zh
from .zh import TERMS, apply_terms, dedupe_technical_units, translate_value

SPEC_LABELS = {
    "dimensiones": "尺寸", "dimension": "尺寸", "tamaño": "尺寸",
    "dimensiones del producto": "产品尺寸", "dimensiones del paquete": "包装尺寸",
    "capacidad": "容量", "peso": "重量", "cantidad": "件数",
    "peso del producto": "产品重量", "peso artículo": "商品重量",
    "material": "材质", "potencia": "功率", "voltaje": "电压",
    "vataje": "功率", "frecuencia": "频率",
    "color": "颜色", "modelo": "型号", "número de modelo": "型号",
    "referencia oem": "OEM参考号", "referencia del fabricante": "制造商参考编号",
    "referencia": "参考号", "requiere montaje": "需要组装",
}


def postprocess(field: str, translated: str, source: str) -> str:
    """Normalize known terms without inventing facts or changing raw source."""
    if not translated:
        return ""
    if field in {"brand", "brand_es"}:
        # Brand is identity data, never a translation target.
        return str(source)
    out = apply_terms(str(translated))
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
    parts = [part.strip() for part in re.split(r"[;；]\s*", str(source or "")) if part.strip()]
    rendered = []
    for part in parts:
        match = re.match(r"^([^:：]+)\s*[:：]\s*(.*)$", part)
        if match:
            label = match.group(1).strip()
            value = match.group(2).strip()
            label_zh = SPEC_LABELS.get(label.casefold(), label)
            rendered.append("%s：%s" % (label_zh, translate_value(value)))
        else:
            rendered.append(translate_value(part))
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
    text = re.sub(r"\d+(?:[.,]\d+)?", " ", str(source or ""))
    text = re.sub(r"\b[A-Z0-9]+(?:[-/][A-Z0-9]+)*\b", " ", text)
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", text.casefold())
    return all(word in known_words for word in words)
