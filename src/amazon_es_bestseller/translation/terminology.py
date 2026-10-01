"""Deterministic terminology bridge for Translation V2.

The existing ``zh.py``/``full_detail.py`` rules remain the source of truth for
known Spanish terms.  V2 applies them around the provider rather than copying
or replacing those legacy modules.
"""
from __future__ import annotations

from typing import Any

from .full_detail import render_bullets_zh, render_details_zh
from .zh import apply_terms


def postprocess(field: str, translated: str, source: str) -> str:
    """Normalize known terms without inventing facts or changing raw source."""
    if not translated:
        return ""
    if field in {"brand", "brand_es"}:
        # Brand is identity data, never a translation target.
        return str(source)
    out = apply_terms(str(translated))
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
