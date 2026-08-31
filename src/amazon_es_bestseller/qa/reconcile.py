# -*- coding: utf-8 -*-
"""Cross-stage ASIN reconciliation for task delivery acceptance."""
from __future__ import annotations

from collections import Counter


def _asins(rows):
    return [str((row or {}).get("asin") or (row or {}).get("ASIN") or "").strip().upper()
            for row in (rows or [])]


def reconcile_task(task, items, products, translations=None, images=None,
                   workbook_asins=None) -> dict:
    """Compare approved target set with every downstream artifact."""
    task = task or {}
    target_rows = task.get("records", task.get("exact_asins", [])) if isinstance(task, dict) else task
    target = set(_asins(target_rows)) if target_rows and isinstance(target_rows[0], dict) else {
        str(a).strip().upper() for a in (target_rows or []) if str(a).strip()
    }
    report = {"target_count": len(target), "stages": {}, "conflicts": [], "status": "success"}
    sources = {"items": items, "products": products, "translations": translations or [],
               "images": images or [], "workbook": workbook_asins or []}
    for name, rows in sources.items():
        values = _asins(rows) if name != "workbook" else [str(a).strip().upper() for a in rows]
        counts = Counter(a for a in values if a)
        actual = set(counts)
        stage = {"count": len(actual), "missing": sorted(target - actual),
                 "extra": sorted(actual - target),
                 "duplicates": sorted(a for a, n in counts.items() if n > 1)}
        report["stages"][name] = stage
        if stage["missing"] or stage["extra"] or stage["duplicates"]:
            report["status"] = "partial"
    return report
