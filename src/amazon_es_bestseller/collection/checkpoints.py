# -*- coding: utf-8 -*-
"""Per-ASIN resumable checkpoints for long-running detail collection."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _safe_asin(asin: str) -> str:
    return str(asin or "").strip().upper()


def write_checkpoint(root, asin: str, payload: dict) -> Path:
    """Atomically persist one terminal per-ASIN outcome."""
    a = _safe_asin(asin)
    if not a:
        raise ValueError("checkpoint requires ASIN")
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (a + ".json")
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target


def read_checkpoint(root, asin: str) -> dict | None:
    a = _safe_asin(asin)
    if not a:
        return None
    path = Path(root) / (a + ".json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
