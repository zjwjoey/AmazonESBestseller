# -*- coding: utf-8 -*-
"""Per-ASIN resumable checkpoints for long-running detail collection."""
from __future__ import annotations

from pathlib import Path

from ..models import is_valid_asin, normalize_asin
from .runtime_io import atomic_json, read_json


def _safe_asin(asin: str) -> str:
    value = normalize_asin(asin)
    return value if is_valid_asin(value) else ""


def write_checkpoint(root, asin: str, payload: dict) -> Path:
    """Atomically persist one terminal per-ASIN outcome."""
    a = _safe_asin(asin)
    if not a:
        raise ValueError("checkpoint requires ASIN")
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (a + ".json")
    atomic_json(target, dict(payload))
    return target


def read_checkpoint(root, asin: str) -> dict | None:
    a = _safe_asin(asin)
    if not a:
        return None
    path = Path(root) / (a + ".json")
    try:
        value = read_json(path)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
