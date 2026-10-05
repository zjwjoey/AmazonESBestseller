"""SKU gate derived from Chinese field QA without guessing missing facts."""
from __future__ import annotations
from typing import Iterable, Mapping
def evaluate_chinese_gate(rows: Iterable[Mapping]) -> dict:
    rows=list(rows); statuses={str(r.get("status") or "BLOCKED") for r in rows}
    status="SKU_ZH_READY" if statuses <= {"PASS"} and rows else ("BLOCKED" if "BLOCKED" in statuses else "REVIEW_REQUIRED")
    return {"status":status,"fields":rows}
